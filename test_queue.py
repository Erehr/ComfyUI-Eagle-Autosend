"""Self-check for the offline/library queue. Run with ComfyUI's python: python test_queue.py"""
import os
import sys
import types
import tempfile
import importlib
from unittest import mock

tmp = tempfile.mkdtemp()
sys.modules["server"] = mock.MagicMock()
sys.modules["folder_paths"] = types.SimpleNamespace(get_user_directory=lambda: tmp)
pkg = types.ModuleType("eaglepkg")
pkg.__path__ = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "py")]
sys.modules["eaglepkg"] = pkg
importlib.import_module("eaglepkg.settings")  # same import order as __init__.py
ea = importlib.import_module("eaglepkg.eagle_autosend")
EagleUnavailable = importlib.import_module("eaglepkg.eagle_api").EagleUnavailable


class FakeEagle:
    def __init__(self, library, fail_after=None):
        self.library, self.fail_after, self.added = library, fail_after, []

    def current_library(self):
        return self.library

    def find_or_create_folder(self, name):
        return "F-" + name

    def add_item_from_path(self, data, folder_id=None):
        if self.fail_after is not None and len(self.added) >= self.fail_after:
            raise EagleUnavailable("x")
        self.added.append((data["path"], folder_id))
        return {"status": "success"}


def image(name):
    path = os.path.join(tmp, name)
    open(path, "wb").close()
    return path


def entry(path, library="", folder=""):
    return {"path": path, "name": os.path.basename(path), "annotation": "", "tags": ["a"], "folder": folder, "library": library}


a, b, c = image("a.png"), image("b.png"), image("c.png")
ea.enqueue(entry(a, "D:\\Lib One.library", "Gen"))
ea.enqueue(entry(b, "D:\\Other.library"))
ea.enqueue(entry(os.path.join(tmp, "missing.png")))
ea.enqueue(entry(a, "D:\\Lib One.library", "Gen"))  # re-queue of same path must not duplicate
assert len(ea.load_queue()) == 3

eagle = FakeEagle("d:\\lib one.library\\")
r = ea.flush_queue(eagle)
assert eagle.added == [(a, "F-Gen")], eagle.added
assert r["sent"] == {a} and len(r["dropped"]) == 1 and r["pending"] == 1
assert [e["path"] for e in ea.load_queue()] == [b]

# Eagle closes mid-flush: already-sent items leave the queue, the rest stay.
ea.enqueue(entry(c))
eagle = FakeEagle("D:\\Other.library", fail_after=1)
try:
    ea.flush_queue(eagle)
    raise AssertionError("expected EagleUnavailable")
except EagleUnavailable:
    pass
assert [e["path"] for e in ea.load_queue()] == [c]

# Unreadable queue is moved aside, not overwritten.
with open(ea._queue_file(), "w") as f:
    f.write("{broken")
assert ea.load_queue() == []
assert any(n.startswith("queue.json.bad-") for n in os.listdir(os.path.dirname(ea._queue_file())))

print("ok")
