import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch


SCRIPT_DIR = Path("scripts/firmware")


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR/f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with patch("sys.path", [str(SCRIPT_DIR), *__import__("sys").path]):
        spec.loader.exec_module(module)
    return module


def test_active_wifi_connection_preserves_names_with_colons():
    module = load_module("update_esp_via_cubey_wifi")
    with patch.object(module, "nmcli", return_value="My:Home:802-11-wireless\nwired:802-3-ethernet"):
        assert module.active_wifi_connection() == "My:Home"


def test_wait_for_updater_retries_then_reads_status():
    module = load_module("update_esp_via_cubey_wifi")
    with patch.object(module, "request", side_effect=[OSError("not yet"), (200, '{"version":"ota"}')]), \
         patch.object(module.time, "sleep"):
        assert module.wait_for_updater("192.168.4.1", "pw") == {"version": "ota"}
