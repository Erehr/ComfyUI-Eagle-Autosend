import server
from .py.settings import get_eagle_settings, save_eagle_settings, list_csv_files_endpoint
from .py.eagle_autosend import send_to_eagle_endpoint

# Define what is exposed to the web
WEB_DIRECTORY = "js"

# Node class mappings and display name mappings are not used in this node,
# but are required by ComfyUI.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
