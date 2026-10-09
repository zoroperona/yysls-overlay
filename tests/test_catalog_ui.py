import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace


class SharedRegionConfigTest(unittest.TestCase):
    def test_only_enabled_categories_need_regions_and_two_selections_cover_all_skills(self):
        from reminder_catalog_ui import CatalogUI
        root = Path(__file__).resolve().parents[1]
        cfg = {'monitored_skills': {'weiwei': True, 'imbalance': True, 'qilong': True, 'qiandeng': True}}
        app = SimpleNamespace(cfg=cfg, _update_summary=Mock())
        save = Mock()
        ui = CatalogUI(app, root, save, Mock())
        with self.assertRaisesRegex(ValueError, 'Debuff.*奇术'):
            ui.validate_start()
        ui.set_region('debuff', [-480, 0, 0, 128])
        with self.assertRaisesRegex(ValueError, '奇术区'):
            ui.validate_start()
        ui.set_region('qishu', [0, 500, 480, 628])
        ui.validate_start()
        self.assertEqual(save.call_count, 2)
        self.assertNotIn('skill_calibrations', cfg)
        cfg['qishu_roi'] = []
        cfg['dizi_enabled'] = False
        cfg['monitored_skills'].update(qilong=False, qiandeng=False)
        ui.validate_start()


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
                    def widgets(parent):
                        for child in parent.winfo_children():
                            yield child
                            yield from widgets(child)
                    buttons = [w.cget('text') for w in widgets(app.catalog.dialog) if w.winfo_class() == 'TButton']
                    self.assertEqual(buttons, ['框选Debuff 栏区', '框选奇术区'])
                    for skill in app.catalog.skills:
                        app.catalog.set_enabled(skill, True)
                    app.catalog.set_region('debuff', [0, 0, 480, 128])
                    app.catalog.set_region('qishu', [0, 500, 480, 628])
                    app.catalog.validate_start()
                    app.cfg['overlay_mode'] = 'icon'
                    app.show_visual_reminder('preview', preview=True)
                    root.update_idletasks()
                    self.assertEqual(app._icon_photo.width(), 6*96-8)
                    self.assertEqual(app._icon_photo.height(), 88)
                    persisted = json.loads((path/'config.json').read_text(encoding='utf-8'))
                    self.assertEqual(sum(persisted['monitored_skills'].values()), 4)
                    self.assertEqual(persisted['qishu_roi'], [0, 500, 480, 628])
                    self.assertNotIn('skill_calibrations', persisted)
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
