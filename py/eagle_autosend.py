import os
import json
import time
import asyncio
import threading
import traceback
import requests
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


_queue_lock = threading.Lock()
_flush_lock = threading.Lock()


def _queue_file():
    return os.path.join(folder_paths.get_user_directory(), "__eagle_autosend", "queue.json")


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def load_queue():
    path = _queue_file()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except ValueError as e:
        # Move it aside so the next save does not overwrite entries that could still be recovered by hand.
        bad = f"{path}.bad-{int(time.time())}"
        os.replace(path, bad)
        print(f"{LOG} queue file unreadable ({e}), moved to {bad}")
        return []


def _save_queue(entries):
    path = _queue_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1)
    os.replace(tmp, path)


def enqueue(entry):
    with _queue_lock:
        entries = [e for e in load_queue() if e["path"] != entry["path"]]
        entries.append(entry)
        _save_queue(entries)


def flush_queue(api):
    """
    Sends every queued item whose target library is open in Eagle (blank target = whatever is open).
    Raises EagleUnavailable when Eagle is closed; items sent before that are still removed from the queue.
    """
    with _flush_lock:
        with _queue_lock:
            entries = load_queue()
        result = {"sent": set(), "dropped": set(), "pending": len(entries), "current": None}
        if not entries:
            return result

        current = result["current"] = api.current_library()
        folder_ids = {}
        try:
            for e in entries:
                if e.get("library") and not (current and same_path(e["library"], current)):
                    continue
                if not os.path.isfile(e["path"]):
                    print(f"{LOG} dropping queued {e['path']}: file no longer exists")
                    result["dropped"].add(e["path"])
                    continue
                folder = e.get("folder") or ""
                if folder not in folder_ids:
                    folder_ids[folder] = api.find_or_create_folder(folder) if folder else None
                item = {k: e[k] for k in ("path", "name", "annotation", "tags")}
                response = api.add_item_from_path(item, folder_id=folder_ids[folder])
                if (response or {}).get("status") == "success":
                    print(f"{LOG} sent {e['path']} -> Eagle (folder={folder or 'library root'}, {len(e['tags'])} tags)")
                    result["sent"].add(e["path"])
                else:
                    print(f"{LOG} Eagle refused {e['path']}, dropping it from the queue: {response}")
                    result["dropped"].add(e["path"])
        except requests.RequestException:
            pass  # Already logged by EagleAPI; the rest stays queued for the next attempt.
        finally:
            done = result["sent"] | result["dropped"]
            with _queue_lock:
                remaining = [e for e in load_queue() if e["path"] not in done]
                _save_queue(remaining)
            result["pending"] = len(remaining)
        return result


def eagle_from_settings():
    current_settings = settings.get_eagle_settings()
    host_url = current_settings.get('eagle.autosend.hostUrl', 'http://localhost:41595')
    return EagleAPI(base_url=host_url, token=current_settings.get('eagle.autosend.token')), host_url


async def sync_endpoint(request):
    api, host_url = eagle_from_settings()
    try:
        result = await asyncio.to_thread(flush_queue, api)
    except EagleUnavailable:
        return web.json_response({"status": "offline", "host": host_url, "pending": len(load_queue())})
    return web.json_response({"status": "ok", "sent": len(result["sent"]), "pending": result["pending"], "current": result["current"]})


async def libraries_endpoint(request):
    api, host_url = eagle_from_settings()
    try:
        current = await asyncio.to_thread(api.current_library)
        history = await asyncio.to_thread(api.library_history)
    except EagleUnavailable:
        return web.json_response({"status": "offline", "host": host_url}, status=503)
    # Eagle's history can list the same library with and without a trailing separator.
    libraries = {}
    for path in ([current] if current else []) + history:
        path = os.path.normpath(path)
        libraries.setdefault(os.path.normcase(path), path)
    return web.json_response({"status": "ok", "current": current, "libraries": list(libraries.values())})


async def send_to_eagle_endpoint(request):
    """Queues the image, then sends everything queued for the library currently open in Eagle."""

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid request: body is not JSON")

    filename = data.get("filename")
    subfolder = data.get("subfolder") or ""
    image_type = data.get("type") or "output"
    folder_name = data.get("folder") or ""
    library = data.get("library") or ""

    if not filename:
        print(f"{LOG} request rejected: no filename in payload {data}")
        return web.Response(status=400, text="Invalid request: filename missing")

    try:
        current_settings = settings.get_eagle_settings()
        api, host_url = eagle_from_settings()

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

        await asyncio.to_thread(enqueue, {
            "path": image_path,
            "name": filename,
            "annotation": annotation,
            "tags": tags,
            "folder": folder_name,
            "library": library,
        })

        try:
            result = await asyncio.to_thread(flush_queue, api)
        except EagleUnavailable:
            return web.json_response({"status": "queued", "reason": "eagle_offline", "host": host_url})

        if image_path in result["sent"]:
            return web.json_response({"status": "sent"})
        if image_path in result["dropped"]:
            return web.json_response({"status": "failed"}, status=502)
        return web.json_response({"status": "queued", "reason": "library_mismatch" if result["current"] else "library_unknown", "current": result["current"]})
    except Exception as e:
        traceback.print_exc()
        return web.Response(status=500, text=f"Error sending to Eagle: {e}")
