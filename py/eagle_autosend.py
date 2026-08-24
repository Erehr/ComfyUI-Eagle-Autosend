import os
import json
import time
import traceback
from aiohttp import web
from PIL import Image

try:
    # ComfyUI core module - gives us the *real* output/input/temp directories
    # regardless of what the current working directory happens to be.
    import folder_paths
except Exception:  # pragma: no cover - ComfyUI always provides this
    folder_paths = None

from .eagle_api import EagleAPI, EagleUnavailable
from . import settings

LOG = "[Eagle Autosend]"

# When Eagle is closed, don't retry (and don't re-log) for every image in a
# batch. After a failed connection we stay quiet for this many seconds.
OFFLINE_BACKOFF_SECONDS = 30.0
_offline_until = 0.0


def get_positive_prompt(parameters_text):
    """Extracts the positive prompt from the full parameter string."""
    if not parameters_text:
        return ""
    neg_prompt_index = parameters_text.lower().find('negative prompt:')
    return parameters_text[:neg_prompt_index].strip() if neg_prompt_index != -1 else parameters_text.strip()


def _base_dir_for_type(image_type):
    """Returns the ComfyUI directory that corresponds to an image 'type'."""
    if folder_paths is None:
        return None
    try:
        if image_type == "input":
            return folder_paths.get_input_directory()
        if image_type == "temp":
            return folder_paths.get_temp_directory()
        return folder_paths.get_output_directory()
    except Exception:
        return None


def resolve_image_path(filename, subfolder, image_type="output"):
    """
    Resolves the on-disk path of an image reported by the ComfyUI frontend.

    The frontend sends {filename, subfolder, type} where subfolder is relative
    to ComfyUI's output/input/temp directory. Resolving it against the process
    working directory (the old behaviour) only worked by accident and breaks
    whenever ComfyUI is started from a different directory or the output dir
    is moved. Returns (path_or_None, tried_paths).
    """
    filename = os.path.basename(filename or "")
    subfolder = (subfolder or "").strip().replace("\\", "/").strip("/")
    tried = []

    if not filename:
        return None, tried

    base = _base_dir_for_type(image_type)
    if base:
        tried.append(os.path.normpath(os.path.join(base, subfolder, filename)))

    # Fall back to every other known ComfyUI directory, then to the legacy
    # cwd-relative behaviour, so nothing that used to work stops working.
    if folder_paths is not None:
        for getter in ("get_output_directory", "get_temp_directory", "get_input_directory"):
            try:
                other = getattr(folder_paths, getter)()
            except Exception:
                continue
            candidate = os.path.normpath(os.path.join(other, subfolder, filename))
            if candidate not in tried:
                tried.append(candidate)

    legacy = os.path.abspath(os.path.join(subfolder, filename))
    if legacy not in tried:
        tried.append(legacy)

    for candidate in tried:
        if os.path.isfile(candidate):
            return candidate, tried

    return None, tried


def _skipped_response(host_url, filename, quiet):
    """503 = Eagle isn't there. The frontend treats this as 'skip', not 'error'."""
    return web.json_response(
        {
            "status": "skipped",
            "reason": "eagle_offline",
            "host": host_url,
            "filename": filename,
            # `quiet` means we already told the user about this outage.
            "quiet": quiet,
        },
        status=503,
    )


async def send_to_eagle_endpoint(request):
    """Endpoint handler for sending the image and metadata to Eagle."""
    global _offline_until

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid request: body is not JSON")

    filename = data.get("filename")
    subfolder = data.get("subfolder") or ""
    image_type = data.get("type") or "output"
    folder_name = data.get("folder")

    if not filename:
        print(f"{LOG} request rejected: no filename in payload {data}")
        return web.Response(status=400, text="Invalid request: filename missing")

    try:
        current_settings = settings.get_eagle_settings()
        host_url = current_settings.get('eagle.autosend.hostUrl', 'http://localhost:41595')
        token = current_settings.get('eagle.autosend.token')

        # Eagle was down a moment ago - skip without touching the network again.
        if time.monotonic() < _offline_until:
            return _skipped_response(host_url, filename, quiet=True)

        image_path, tried = resolve_image_path(filename, subfolder, image_type)
        if not image_path:
            print(f"{LOG} file not found for filename={filename!r} subfolder={subfolder!r} "
                  f"type={image_type!r}. Tried: {tried}")
            return web.Response(status=404, text=f"File not found. Tried: {tried}")

        annotation = ""
        tags = []

        with Image.open(image_path) as img:
            metadata = img.info
            parameters_text = metadata.get('parameters', '')
            prompt_text = metadata.get('prompt', '{}')
            positive_prompt_text = get_positive_prompt(parameters_text)

            # 1. Determine Annotation
            anno_setting = current_settings.get('eagle.autosend.annotation', 'Parameters')
            if anno_setting == 'Parameters':
                annotation = parameters_text
            elif anno_setting == 'Prompt':
                try:
                    annotation = json.dumps(json.loads(prompt_text), indent=4)
                except Exception:
                    annotation = prompt_text
            elif anno_setting == 'Positive Prompt':
                annotation = positive_prompt_text

            # 2. Determine Tags
            tags_setting = current_settings.get('eagle.autosend.tags', 'Positive')
            if tags_setting != 'None':
                raw_tags = [tag.strip() for tag in positive_prompt_text.split(',') if tag.strip()]
                if tags_setting == 'Positive (filtered)':
                    tags = settings.filter_tags_with_csv(
                        raw_tags,
                        current_settings.get('eagle.autosend.tagsCsv'),
                        current_settings.get('eagle.autosend.tagsAlias'),
                    )
                else:  # 'Positive'
                    tags = raw_tags

        item_data = {
            "path": image_path,
            "name": filename,
            "annotation": annotation,
            "tags": tags,
        }

        eagle_api_instance = EagleAPI(base_url=host_url, token=token)

        try:
            folder_id = eagle_api_instance.find_or_create_folder(folder_name) if folder_name else None
            result = eagle_api_instance.add_item_from_path(item_data, folder_id=folder_id)
        except EagleUnavailable:
            # Eagle is closed. One tidy line, no traceback, and back off so a
            # batch of images doesn't repeat this 20 times.
            _offline_until = time.monotonic() + OFFLINE_BACKOFF_SECONDS
            print(f"{LOG} Eagle is not running at {host_url} - not sending images "
                  f"(retrying in {int(OFFLINE_BACKOFF_SECONDS)}s). Skipped: {filename}")
            return _skipped_response(host_url, filename, quiet=False)

        # Reached Eagle - clear any backoff from an earlier outage.
        _offline_until = 0.0

        status = (result or {}).get("status")
        if status != "success":
            print(f"{LOG} Eagle refused the item: {result}")
            return web.Response(status=502, text=f"Eagle returned: {result}")

        print(f"{LOG} sent {image_path} -> Eagle (folder={folder_name or 'library root'}, {len(tags)} tags)")
        return web.Response(status=200, text="Image and metadata sent to Eagle from path")
    except Exception as e:
        traceback.print_exc()
        return web.Response(status=500, text=f"Error sending to Eagle: {e}")
