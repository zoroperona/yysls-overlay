import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

try:
    import numpy as np
    from PIL import Image
    import buff_reminder_v2_dizi_hotkey as app
    from reminder_presentation import format_reminder, cooldown_icon, dizi_icon_state
except ImportError:
    app = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(app is None, 'Install requirements.txt')
class PresentationTest(unittest.TestCase):
    def test_text_placeholders_and_rounding(self):
        self.assertEqual(format_reminder('{%s}：{%d} 秒 / {%d}', 2.9, '打响指'), '打响指：2 秒 / 2')
        self.assertEqual(format_reminder('{%s}好了', 0, '笛子'), '笛子好了')
        self.assertEqual(format_reminder('{%d}', -2, '技能'), '0')
        self.assertEqual(format_reminder('纯文字 {unknown}', 3, '技能'), '纯文字 {unknown}')

    def test_clockwise_wipe_and_number_toggle(self):
        source = Image.new('RGBA', (10, 10), (0, 0, 0, 0))
        quarter = cooldown_icon(source, '#cc3311', 75, 100, False)
        gray = quarter.getpixel((65, 20))
        self.assertEqual(gray[0], gray[1])
        self.assertEqual(quarter.getpixel((65, 65))[:3], (204, 51, 17))
        self.assertEqual(quarter.getpixel((20, 20))[:3], (204, 51, 17))
        end = cooldown_icon(source, '#cc3311', 0, 100, False)
        self.assertEqual(len(set(end.getpixel((20, 20))[:3])), 1)
        ready = cooldown_icon(source, '#cc3311', None, 0, True, ready=True)
        self.assertEqual(ready.getpixel((20, 20))[:3], (204, 51, 17))
        numbered = cooldown_icon(source, '#cc3311', 75, 100, True)
        self.assertNotEqual(numbered.tobytes(), quarter.tobytes())
        self.assertEqual(quarter.getpixel((0, 0))[3], 0)
        self.assertEqual(quarter.getpixel((2, 2))[3], 0)
        strip = Image.new('RGBA', (184, 88), (0,0,0,0))
        strip.paste(quarter, (0,0))
        strip.paste(ready, (96,0))
        self.assertTrue(all(strip.getpixel((x,44))[3] == 0 for x in range(88,96)))

    def test_dizi_uses_cooldown_only_even_with_legacy_repeat_setting(self):
        cfg = {'dizi_icon_timer':'repeat', 'dizi_cooldown_seconds':30, 'dizi_repeat_interval':25}
        self.assertEqual(dizi_icon_state(cfg, True, 100, None, 107), (None,30,True))
        self.assertEqual(dizi_icon_state(cfg, False, 90, 100, 107), (23,30,False))
        self.assertEqual(dizi_icon_state(cfg, False, 90, None, 107), (None,30,False))
        self.assertEqual(dizi_icon_state({}, False, 90, 100, 107), (None,0,False))

    def test_confirmed_24_second_default_and_legacy_config_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(app, 'CONFIG_DIR', path), patch.object(app, 'CONFIG_PATH', path/'config.json'):
                self.assertEqual(app.load_config()['dizi_cooldown_seconds'], 24)
                app.save_config({'dizi_cooldown_seconds': 0})
                cfg = app.load_config()
                self.assertEqual(cfg['dizi_cooldown_seconds'], 24)
                self.assertEqual(dizi_icon_state(cfg, True, None, None, 100), (None,24,True))
                self.assertEqual(dizi_icon_state(cfg, False, None, 100, 107), (17,24,False))
                cfg['dizi_cooldown_seconds'] = 0
                app.save_config(cfg)
                self.assertEqual(app.load_config()['dizi_cooldown_seconds'], 0)
                app.save_config({'dizi_cooldown_seconds': 30})
                self.assertEqual(app.load_config()['dizi_cooldown_seconds'], 30)

    def test_both_regions_and_options_survive_config_reload_without_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cfg = dict(app.DEFAULT_CONFIG)
            cfg['loss_roi'] = [-900, 30, -100, 140]
            cfg['dizi_calibration'] = {'roi': [400, 500, 410, 510], 'mask': np.ones((10,10),np.uint8).tolist()}
            cfg['loss_text'] = '打响指：{%d}'
            cfg['overlay_mode'] = 'icon'
            with patch.object(app, 'CONFIG_DIR', path), patch.object(app, 'CONFIG_PATH', path/'config.json'), patch.object(app, 'DIZI_TEMPLATE_PATH', path/'not-present.npz'):
                app.save_config(cfg)
                loaded = app.load_config()
                self.assertEqual(loaded['loss_roi'], cfg['loss_roi'])
                self.assertEqual(loaded['loss_text'], cfg['loss_text'])
                self.assertEqual(loaded['overlay_mode'], 'icon')
                detector = app.DiziDetector(loaded)
                self.assertTrue(detector.configured)
                self.assertEqual(detector.roi, (400,500,410,510))
                self.assertEqual(list(path.glob('*.tmp')), [])

    def test_screen_selection_maps_scaled_preview_and_negative_origin(self):
        from types import SimpleNamespace
        selector = SimpleNamespace(source_size=(3840,1080), display_size=(1920,1080), screen_origin=(-1920,0))
        self.assertEqual(app.RegionSelector.screen_box(selector, (100,100,200,200)), [-1720,100,-1520,200])

    def test_default_wavs_have_audio(self):
        for kind in ('loss','dizi'):
            for preset in ('electronic','baidu'):
                with self.subTest(kind=kind,preset=preset):
                    with wave.open(str(ROOT/'assets/audio'/f'{kind}_{preset}.wav')) as audio:
                        self.assertEqual(audio.getsampwidth(), 2)
                        self.assertGreater(audio.getnframes()/audio.getframerate(), .3)
                        self.assertTrue(any(audio.readframes(audio.getnframes())))

if __name__ == '__main__':
    unittest.main()
