import requests
from urllib.parse import urlparse
from typing import Dict, Optional, TypedDict, List


class EagleUnavailable(Exception):
    """
    Raised when the Eagle application cannot be reached at all - not running,
    wrong host/port, or blocked by a firewall.

    Deliberately NOT a subclass of requests.RequestException so that the
    best-effort `except requests.RequestException` handlers below don't swallow
    it: "Eagle is closed" has to reach the caller so it can be reported once,
    quietly, instead of surfacing as a traceback per image.
    """

    def __init__(self, base_url, original=None):
        self.base_url = base_url
        self.original = original
        super().__init__(f"Eagle is not reachable at {base_url}")


class FolderInfo(TypedDict):
    id: str
    name: str

class EagleAPI:
    # (connect, read) timeout in seconds. A short connect timeout keeps a
    # closed Eagle from stalling the ComfyUI request handler.
    TIMEOUT = (3, 30)

    def __init__(self, base_url="http://localhost:41595", token=None):
        self.base_url = base_url
        self.token = token
        self.folder_list: Optional[List[FolderInfo]] = None

    def add_item_from_path(self, data, folder_id=None):
        if folder_id:
            data["folderId"] = folder_id
        return self._send_request("/api/item/addFromPath", method="POST", data=data)

    def current_library(self) -> Optional[str]:
        """Path of the library open in Eagle, or None if this Eagle version does not report it (pre-4.0)."""
        data = self._send_request("/api/library/info").get("data", {})
        return (data.get("library") or {}).get("path")

    def library_history(self) -> List[str]:
        return self._send_request("/api/library/history").get("data", [])

    def find_or_create_folder(self, name_or_id:str) -> str:
        folder = self._find_folder(name_or_id)
        if folder:
            return folder.get("id", "")
        return self._create_folder(name_or_id)

    def _find_folder(self, name_or_id:str) -> Optional[FolderInfo]:
        self._ensure_folder_list()
        if self.folder_list is not None:
            for folder in self.folder_list:
                if folder["name"] == name_or_id or folder["id"] == name_or_id:
                    return folder
        return None

    def _create_folder(self, name:str) -> str:
        if not name:
            return ""
        try:
            data = {"folderName": name}
            response = self._send_request("/api/folder/create", method="POST", data=data)
            new_folder_id = response.get("data", {}).get("id", "")
            if new_folder_id and self.folder_list is not None:
                self.folder_list.append({"id": new_folder_id, "name": name})
            return new_folder_id
        except requests.RequestException:
            return ""

    def _ensure_folder_list(self):
        if self.folder_list is None:
            self._get_all_folder_list()

    def _get_all_folder_list(self):
        try:
            json_data = self._send_request("/api/folder/list")
            self.folder_list = self._extract_id_name_pairs(json_data["data"])
        except requests.RequestException:
            self.folder_list = []

    def _send_request(self, endpoint, method="GET", data=None):
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=self.TIMEOUT)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=self.TIMEOUT)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            # Eagle is closed / unreachable. Raise a clean, quiet exception -
            # `from None` drops the noisy urllib3 chain from any traceback.
            raise EagleUnavailable(self.base_url, e) from None
        except requests.RequestException as e:
            print(f"[Eagle Autosend] Eagle request failed: {e}")
            raise

    def _extract_id_name_pairs(self, data):
        result = []
        def recursive_extract(item):
            if isinstance(item, dict):
                if 'id' in item and 'name' in item:
                    result.append({'id': item['id'], 'name': item['name']})
                if 'children' in item and isinstance(item['children'], list):
                    for child in item['children']:
                        recursive_extract(child)
            elif isinstance(item, list):
                for element in item:
                    recursive_extract(element)
        recursive_extract(data)
        return result
