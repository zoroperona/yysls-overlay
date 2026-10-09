import queue
from types import SimpleNamespace
import unittest
try:
    import buff_reminder_v2_dizi_hotkey as app
except ImportError:
    app = None


@unittest.skipIf(app is None, 'Install requirements.txt')
class TimingTest(unittest.TestCase):
    def make_loss(self):
        target = app.ReminderApp.__new__(app.ReminderApp)
        target.cfg = dict(app.DEFAULT_CONFIG)
        target.tracker = SimpleNamespace(frames_since_reacq=5, reacquire_count=1)
        target.refresh_detector = app.RefreshDetector(target.cfg)
        target.last_loss_seen = None
        target.axis_start = None
        target.axis_warned = False
        target.birth_count = 0
        target.birth_first_t = None
        target.messages = queue.Queue()
        return target

    def feed(self, target, value, start, frames=10):
        events = []
        for i in range(frames):
            events.append(target._observe_loss(value, round(start+i*.1, 3)))
        return events

    def test_repeated_refresh_above_old_low_threshold_restarts_axis_and_warning(self):
        target = self.make_loss()
        self.feed(target, 93, 10)
        first = target.axis_start
        self.feed(target, 78, 11)
        target.axis_warned = True
        events = self.feed(target, 94, 12)
        self.assertEqual(sum(e['refresh'] for e in events), 1)
        second = target.axis_start
        self.assertGreater(second, first)
        self.assertLess(abs(second-12), .25)
        self.assertFalse(target.axis_warned)
        self.feed(target, 80, 13)
        events = self.feed(target, 96, 14)
        self.assertEqual(sum(e['refresh'] for e in events), 1)
        self.assertGreater(target.axis_start, second)

    def test_refresh_after_zero_and_reapplication_after_gap(self):
        target = self.make_loss()
        self.feed(target, 93, 0, 10)
        first = target.axis_start
        self.feed(target, 78, 1, 240)
        self.assertEqual(target.axis_start, first)  # Expiration alone must not loop back to 21.
        events = self.feed(target, 94, 25)
        self.assertTrue(any(e['refresh'] for e in events))
        self.assertGreater(target.axis_start, 24)
        self.feed(target, 78, 26, 240)
        self.feed(target, 94, 52, 6)  # Absent, expired, then acquired again.
        self.assertGreaterEqual(target.axis_start, 52)

    def test_one_frame_flash_and_long_capture_gap_do_not_confirm_refresh(self):
        target = self.make_loss()
        self.feed(target, 78, 10)
        first = target.axis_start
        events = self.feed(target, 98, 11, 1)+self.feed(target, 78, 11.1)
        self.assertFalse(any(e['refresh'] for e in events))
        self.assertEqual(target.axis_start, first)
        events = self.feed(target, 96, 15)
        self.assertFalse(any(e['refresh'] for e in events))
        self.assertEqual(target.axis_start, first)

    def test_small_brightness_refresh_uses_confirmed_phase_jump(self):
        target = self.make_loss()
        target.tracker.last_match = SimpleNamespace(visual_fraction=.5)
        self.feed(target, 81, 10)
        first = target.axis_start
        target.tracker.last_match.visual_fraction = 1.
        events = self.feed(target, 84, 11)  # Only 3 brightness points, below the old 8-point gate.
        self.assertEqual(sum(e['refresh'] for e in events), 1)
        self.assertGreater(target.axis_start, first)
        self.assertEqual(next(e['evidence'] for e in events if e['refresh']), 'phase')

    def test_refresh_during_reacquisition_does_not_pollute_baseline(self):
        target = self.make_loss()
        target.cfg['refresh_baseline_n'] = 3
        target.refresh_detector = app.RefreshDetector(target.cfg)
        self.feed(target, 78, 10)
        first = target.axis_start
        target.tracker.frames_since_reacq = 0
        self.feed(target, 95, 11, 6)
        target.tracker.frames_since_reacq = 3
        events = self.feed(target, 95, 11.6)
        self.assertTrue(any(e['refresh'] for e in events))
        self.assertGreater(target.axis_start, first)

    def test_loss_disappearance_hides_and_clears_stale_axis(self):
        target = self.make_loss()
        target.tracker.active = True
        self.feed(target, 93, 10)
        self.assertTrue(target._loss_icon_visible(11))
        target._loss_missing(11)
        self.assertFalse(target._loss_icon_visible(11))
        self.assertIsNotNone(target.axis_start)  # Short misses preserve continuity, but not visibility.
        target._loss_missing(14)
        self.assertIsNone(target.axis_start)
        target.tracker.active = True
        self.feed(target, 93, 15)
        self.assertTrue(target._loss_icon_visible(15.9))
        self.assertGreaterEqual(target.axis_start, 15)

    def test_dizi_hud_disappearance_never_starts_cooldown(self):
        detector = app.DiziDetector(dict(app.DEFAULT_CONFIG, dizi_calibration=None))
        detector.update(1, 10)
        detector.update(1, 10.1)
        self.assertTrue(detector.is_ready)
        detector.update(0, 11, present=False)
        detector.update(0, 11.1, present=False)
        self.assertFalse(detector.visible)
        self.assertIsNone(detector.cooldown_start)
        detector.update(0, 12, present=True)
        detector.update(0, 12.1, present=True)
        self.assertIsNone(detector.cooldown_start)  # Cannot infer a cast while HUD was hidden.
        detector.update(1, 13)
        detector.update(1, 13.1)
        detector.update(0, 14)
        detector.update(0, 14.1)
        self.assertEqual(detector.cooldown_start, 14)
        detector.update(0, 15, present=False)
        self.assertEqual(detector.cooldown_start, 14)  # Does not restart an already known cooldown.

    def test_dizi_unused_ready_does_not_start_cooldown_then_used_starts_once(self):
        detector = app.DiziDetector(dict(app.DEFAULT_CONFIG, dizi_calibration=None))
        detector.reset_state()
        detector.update(1, 10)
        detector.update(1, 10.1)
        detector.update(1, 40)  # Repeated reminder is not a cast.
        self.assertTrue(detector.is_ready)
        self.assertIsNone(detector.cooldown_start)
        detector.update(.1, 41)
        state = detector.update(.1, 41.1)
        self.assertEqual(state['event'], 'off')
        self.assertEqual(detector.cooldown_start, 41)
        detector.update(.1, 50)
        self.assertEqual(detector.cooldown_start, 41)
        detector.update(1, 70)
        detector.update(1, 70.1)
        self.assertIsNone(detector.cooldown_start)
        detector.update(.1, 71)
        detector.update(.1, 71.1)
        self.assertEqual(detector.cooldown_start, 71)

    def test_dizi_initial_dark_glitch_and_repeat_boundary(self):
        detector = app.DiziDetector(dict(app.DEFAULT_CONFIG, dizi_calibration=None))
        detector.reset_state()
        detector.update(0, 10)
        detector.update(0, 10.1)
        self.assertIsNone(detector.cooldown_start)
        detector.update(1, 11)
        detector.update(1, 11.1)
        detector.update(0, 12)
        detector.update(1, 12.1)
        self.assertTrue(detector.is_ready)
        self.assertIsNone(detector.cooldown_start)
        # A use coinciding with the next repeat must not announce 'ready'.
        first = detector.update(0, 36.1)
        second = detector.update(0, 36.2)
        self.assertNotEqual(first['event'], 'repeat')
        self.assertEqual(second['event'], 'off')


if __name__ == '__main__':
    unittest.main()
