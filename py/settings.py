import os
import csv
import json
import server
from aiohttp import web

# Set up the API routes
@server.PromptServer.instance.routes.get("/eagle/get_settings")
async def get_settings_handler(request):
    return get_eagle_settings()

@server.PromptServer.instance.routes.post("/eagle/set_setting")
async def set_setting_handler(request):
    data = await request.json()
    key = data.get("key")
    value = data.get("value")
    if key is None:
        return web.json_response({"status": "error", "message": "Setting 'key' not provided"}, status=400)
    
    settings = get_eagle_settings()
    settings[key] = value
    save_eagle_settings(settings)
    return web.json_response({"status": "ok"})

@server.PromptServer.instance.routes.get("/eagle/list_csv_files")
async def list_csv_files(request):
    return list_csv_files_endpoint(request)

@server.PromptServer.instance.routes.post("/send-to-eagle")
async def send_to_eagle(request):
    return await send_to_eagle_endpoint(request)

# Define the absolute path for the settings file to ensure it's always found.
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

def get_eagle_settings():
    """
    Loads the settings from the JSON file.
    Returns a default structure if the file doesn't exist or is invalid.
    """
    defaults = {
        "eagle.autosend.hostUrl": "http://localhost:41595",
        "eagle.autosend.token": "",
        "eagle.autosend.enable": True,
        "eagle.autosend.folderName": "",
        "eagle.autosend.annotation": "Parameters",
        "eagle.autosend.tags": "Positive",
        "eagle.autosend.tagsCsv": "",
        "eagle.autosend.tagsAlias": "Use main"
    }
    
    if not os.path.exists(SETTINGS_FILE):
        return defaults
        
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            settings = json.load(f)
            # Ensure all keys are present, add defaults for missing ones
            for key, value in defaults.items():
                if key not in settings:
                    settings[key] = value
            return settings
    except (json.JSONDecodeError, Exception):
        # Return default structure on any error
        return defaults

def save_eagle_settings(data):
    """
    Saves the provided data to the settings JSON file.
    """
    try:
        # Load existing settings to not overwrite everything
        existing_settings = get_eagle_settings()
        existing_settings.update(data)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing_settings, f, indent=4)
    except Exception as e:
        print(f"Error saving Eagle settings: {e}")
        pass

def list_csv_files_endpoint(request):
    """Endpoint handler to list CSV files."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    autocomplete_dir = os.path.join(base_dir, "..", "__csv__")
    
    if not os.path.exists(autocomplete_dir):
        return web.json_response([])
        
    try:
        csv_files = [f for f in os.listdir(autocomplete_dir) if f.endswith(".csv")]
        return web.json_response(csv_files)
    except Exception as e:
        print(f"Error listing CSV files: {e}")
        return web.json_response([], status=500)

def filter_tags_with_csv(tags, csv_filename, alias_handling):
    """Filters a list of tags based on a CSV file and alias handling rules."""
    if not csv_filename:
        return tags

    base_dir = os.path.dirname(os.path.abspath(__file__))
    autocomplete_dir = os.path.join(base_dir, "..", "__csv__")
    csv_path = os.path.join(autocomplete_dir, csv_filename)

    if not os.path.isfile(csv_path):
        return tags

    alias_map = {}
    canonical_map = {}
    tag_set = set()

    try:
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            next(reader, None)  # Skip header
            for row in reader:
                if len(row) < 4: continue
                tag = row[0].strip().lower().replace('_', ' ')
                tag_set.add(tag)
                canonical_map[tag] = tag
                if row[3]:
                    for alias in row[3].split(','):
                        alias_map[alias.strip().lower().replace('_', ' ')] = tag
    except Exception:
        return tags # Return original tags on CSV error

    result_tags = []
    for token in tags:
        token = token.lower().strip()
        base = alias_map.get(token, token)
        main = canonical_map.get(base)

        if alias_handling == "Use alias" and token in alias_map:
            result_tags.append(token)
        elif alias_handling == "Use main" and main:
            result_tags.append(main)
        elif alias_handling == "Use both" and token in alias_map and main:
            result_tags.extend([main, token])
        elif token in tag_set:
            result_tags.append(token)
            
    return list(dict.fromkeys(result_tags)) # Return unique tags
