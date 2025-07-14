import os
import json
import traceback
from aiohttp import web
from PIL import Image

from .eagle_api import EagleAPI
from .settings import get_eagle_settings, filter_tags_with_csv

def get_positive_prompt(parameters_text):
    """Extracts the positive prompt from the full parameter string."""
    if not parameters_text:
        return ""
    neg_prompt_index = parameters_text.lower().find('negative prompt:')
    return parameters_text[:neg_prompt_index].strip() if neg_prompt_index != -1 else parameters_text.strip()

async def send_to_eagle_endpoint(request):
    """Endpoint handler for sending the image and metadata to Eagle."""
    data = await request.json()
    filename = data.get("filename")
    subfolder = data.get("subfolder")
    folder_name = data.get("folder")

    if not filename or not subfolder:
        return web.Response(status=400, text="Invalid request: filename or subfolder missing")

    try:
        settings = get_eagle_settings()
        absolute_path = os.path.join(subfolder, filename)

        if not os.path.exists(absolute_path):
            return web.Response(status=404, text=f"File not found: {absolute_path}")

        annotation = ""
        tags = []
        
        with Image.open(absolute_path) as img:
            metadata = img.info
            parameters_text = metadata.get('parameters', '')
            prompt_text = metadata.get('prompt', '{}')
            positive_prompt_text = get_positive_prompt(parameters_text)

            # 1. Determine Annotation
            anno_setting = settings.get('eagle.autosend.annotation', 'Parameters')
            if anno_setting == 'Parameters':
                annotation = parameters_text
            elif anno_setting == 'Prompt':
                try:
                    annotation = json.dumps(json.loads(prompt_text), indent=4)
                except:
                    annotation = prompt_text
            elif anno_setting == 'Positive Prompt':
                annotation = positive_prompt_text

            # 2. Determine Tags
            tags_setting = settings.get('eagle.autosend.tags', 'Positive')
            if tags_setting != 'None':
                raw_tags = [tag.strip() for tag in positive_prompt_text.split(',') if tag.strip()]
                if tags_setting == 'Positive (filtered)':
                    tags = filter_tags_with_csv(raw_tags, settings.get('eagle.autosend.tagsCsv'), settings.get('eagle.autosend.tagsAlias'))
                else: # 'Positive'
                    tags = raw_tags

        item_data = {
            "path": absolute_path,
            "name": filename,
            "annotation": annotation,
            "tags": tags,
        }
        
        # Create a new EagleAPI instance with host and token from settings
        host_url = settings.get('eagle.autosend.hostUrl', 'http://localhost:41595')
        token = settings.get('eagle.autosend.token')
        eagle_api_instance = EagleAPI(base_url=host_url, token=token)
        
        folder_id = eagle_api_instance.find_or_create_folder(folder_name) if folder_name else None
        eagle_api_instance.add_item_from_path(item_data, folder_id=folder_id)
        
        return web.Response(status=200, text="Image and metadata sent to Eagle from path")
    except Exception as e:
        traceback.print_exc()
        return web.Response(status=500, text=f"Error sending to Eagle: {e}")
