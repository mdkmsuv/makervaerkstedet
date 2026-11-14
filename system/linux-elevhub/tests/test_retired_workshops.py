"""Portable retirement regression; no network, service or installation calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE=Path(__file__).parents[1]/"administration/template_activation.py"
SPEC=importlib.util.spec_from_file_location("retirement_activation",SOURCE)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

class RetiredWorkshops(unittest.TestCase):
    def test_existing_config_cannot_reactivate_retired_workshops(self):
        config=M.default_config()
        config["lokale_app_urler"]={**config["lokale_app_urler"],
            "datatraener":"http://127.0.0.1:8793/", "kredsloeb":"http://127.0.0.1:8794/",
            "skolegpt":"https://legacy.example/"}
        with tempfile.TemporaryDirectory() as root:
            urls=M.context(config,Path(root))["kiosk_lokale_app_urler"]
        self.assertFalse({"datatraener","kredsloeb","skolegpt"}&set(urls))
        for app in ("scratch","microbit","3dprint","laser","folie"):
            self.assertIn(app,urls)
        self.assertFalse({8793,8794}&set(M.default_config()["elev_porte"]))

if __name__=="__main__":unittest.main()
