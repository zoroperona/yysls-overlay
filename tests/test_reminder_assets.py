from pathlib import Path
import unittest

try:
    import cv2
    import numpy as np
    from PIL import Image
    import buff_reminder_v2_dizi_hotkey as reminder
    from reminder_assets import OriginalAssets
except ImportError:
    reminder = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(reminder is None, 'Install requirements.txt for image regression tests')
class OriginalAssetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = OriginalAssets(ROOT)

    def test_clean_checkout_initializes_without_old_templates(self):
        tracker = reminder.LossTracker(dict(reminder.DEFAULT_CONFIG))
        self.assertIsNotNone(tracker.original_assets)
        self.assertEqual(tracker.brightness_mask.shape, (63, 43))
        self.assertGreater(int(tracker.brightness_mask.sum()), 20)
        white = np.full((63, 43, 3), 255, np.uint8)
        self.assertAlmostEqual(tracker.brightness_percent(white), 100, places=4)
        self.assertAlmostEqual(tracker.brightness_percent(white // 2), 127 / 255 * 100, places=4)
        white[tracker.brightness_mask] = 0
        self.assertEqual(tracker.brightness_percent(white), 0)


    def test_user_dim_crop_and_brightness_scale_variants(self):
        original = np.array(Image.open(ROOT / 'tests/fixtures/loss_user_dim.png').convert('RGB'))
        for scale in (.9, 1, 1.1):
            rgb = cv2.resize(original, None, fx=scale, fy=scale)
            for brightness in (.55, .8, 1):
                with self.subTest(scale=scale, brightness=brightness):
                    found = self.assets.find_loss((rgb * brightness).astype(np.uint8))
                    self.assertIsNotNone(found)
                    self.assertGreaterEqual(found[0], .9)

    def test_duplicate_slots_are_ambiguous_and_lost_target_unlocks(self):
        glyph = np.array(Image.open(ROOT / 'tests/fixtures/loss_user_dim.png').convert('RGB'))
        row = np.zeros((100, 200, 3), np.uint8)
        h, w = glyph.shape[:2]
        row[20:20+h, 20:20+w] = glyph
        row[20:20+h, 110:110+w] = glyph
        self.assertIsNone(self.assets.find_loss(row))
        row[20:20+h, 110:110+w] = 0
        tracker = reminder.LossTracker(dict(reminder.DEFAULT_CONFIG, loss_roi=[0,0,200,100]))
        class Capture:
            def grab_rgb(self, box):
                x0,y0,x1,y1 = box
                return row[y0:y1,x0:x1].copy()
        crop, _ = tracker.global_acquire(Capture())
        self.assertIsNotNone(crop)
        self.assertTrue(tracker.active)
        row[:] = 0
        crop, _ = tracker.track(Capture())
        self.assertIsNone(crop)
        self.assertFalse(tracker.active)

    def test_matching_threshold_is_configurable(self):
        rgb = np.array(Image.open(ROOT / 'tests/fixtures/loss_user_dim.png').convert('RGB'))
        self.assertIsNotNone(self.assets.find_loss(rgb, threshold=.90))
        self.assertIsNone(self.assets.find_loss(rgb, threshold=.99))
        self.assertEqual(self.assets.last_diagnostics['reason'], 'below_threshold')


    def test_blank_does_not_lock(self):
        self.assertIsNone(self.assets.find_loss(np.zeros((120, 500, 3), np.uint8)))

    def test_dizi_source_alignment_and_cooldown_response(self):
        alpha = self.assets.dizi.alpha
        ys, xs = np.nonzero(alpha >= 128)
        glyph = alpha[ys.min():ys.max()+1, xs.min():xs.max()+1]
        glyph = cv2.resize(glyph, (40, 48))
        screen = np.zeros((68, 64, 3), np.uint8)
        screen[10:58, 12:52] = glyph[..., None]
        mask = self.assets.calibrate_dizi(screen)
        self.assertGreater(mask.sum(), 20)
        detector = reminder.DiziDetector(dict(reminder.DEFAULT_CONFIG))
        detector.template_mask = mask
        self.assertTrue(detector.present_in_rgb(screen))
        self.assertTrue(detector.present_in_rgb(screen // 3))
        self.assertFalse(detector.present_in_rgb(np.zeros_like(screen)))
        self.assertFalse(detector.present_in_rgb(np.full_like(screen, 80)))
        self.assertFalse(detector.present_in_rgb(np.random.default_rng(7).integers(0,255,screen.shape,dtype=np.uint8)))
        self.assertGreater(detector.ratio_from_rgb(screen), .8)
        self.assertEqual(detector.ratio_from_rgb(screen // 3), 0)
        with self.assertRaises(ValueError):
            self.assets.calibrate_dizi(np.full_like(screen, 255))
        with self.assertRaises(ValueError):
            self.assets.calibrate_dizi(np.zeros_like(screen))


if __name__ == '__main__':
    unittest.main()
