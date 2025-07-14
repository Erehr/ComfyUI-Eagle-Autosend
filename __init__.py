import server
from .py import settings
from .py import eagle_autosend

# Define what is exposed to the web
WEB_DIRECTORY = "js"

# Node class mappings and display name mappings are not used in this node,
# but are required by ComfyUI.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
