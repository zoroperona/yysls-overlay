import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32', 'Native Tk smoke test runs on Windows CI')
class CatalogUISmokeTest(unittest.TestCase):
    def test_startup_multi_select_preview_and_settings_persistence(self):
        import tkinter as tk
        import buff_reminder_v2_dizi_hotkey as module
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            root = tk.Tk()
            root.withdraw()
            app = None
            with patch.object(module, 'CONFIG_DIR', path), patch.object(module, 'CONFIG_PATH', path/'config.json'):
                try:
                    app = module.ReminderApp(root)
                    app.catalog.open()
                    self.assertEqual(len(app.catalog.labels), 6)
                    for skill in app.catalog.skills:
                        app.catalog.set_enabled(skill, True)
                    app.cfg['overlay_mode'] = 'icon'
                    app.show_visual_reminder('preview', preview=True)
                    root.update_idletasks()
                    self.assertEqual(app._icon_photo.width(), 6*96-8)
                    self.assertEqual(app._icon_photo.height(), 88)
                    persisted = json.loads((path/'config.json').read_text(encoding='utf-8'))
                    self.assertEqual(sum(persisted['monitored_skills'].values()), 4)
                    app.catalog.dialog.destroy()
                    app.catalog.dialog = None
                    app.catalog.open()
                    self.assertEqual(len(app.catalog.labels), 6)
                finally:
                    if app is not None:
                        app.on_close()
                    else:
                        root.destroy()


if __name__ == '__main__':
    unittest.main()
