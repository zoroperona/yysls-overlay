import unittest
from unittest.mock import Mock, patch
import reminder_capture as capture


class CaptureGuardTest(unittest.TestCase):
    def test_windows_root_handle_and_exclusion(self):
        native = Mock()
        native.GetAncestor.return_value = 0x100001234
        native.SetWindowDisplayAffinity.return_value = 1
        with patch.object(capture.sys, 'platform', 'win32'), patch.object(capture.ctypes, 'WinDLL', return_value=native, create=True):
            self.assertTrue(capture.exclude_window_from_capture(0x100005678))
        native.GetAncestor.assert_called_once_with(0x100005678, 2)
        native.SetWindowDisplayAffinity.assert_called_once_with(0x100001234, 0x11)
        self.assertIsNotNone(native.GetAncestor.restype)

    def test_api_failure_and_other_platform_fail_closed(self):
        native = Mock()
        native.SetWindowDisplayAffinity.return_value = 0
        with patch.object(capture.sys, 'platform', 'win32'), patch.object(capture.ctypes, 'WinDLL', return_value=native, create=True):
            self.assertFalse(capture.exclude_window_from_capture(10))
        with patch.object(capture.sys, 'platform', 'darwin'):
            self.assertFalse(capture.exclude_window_from_capture(10))

    def test_native_excluded_overlay_does_not_block_game_capture(self):
        guard = capture.CaptureGuard()
        guard.set_window('overlay', (0,0,184,88), True)
        version = guard.check((0,0,200,100))
        guard.check((0,0,200,100), version)
        guard.check()

    def test_fallback_blocks_overlap_and_fullscreen_only(self):
        guard = capture.CaptureGuard()
        guard.set_window('overlay', (-100,0,84,88), False)
        with self.assertRaises(capture.CaptureOccluded):
            guard.check((-110,-10,200,100))
        with self.assertRaises(capture.CaptureOccluded):
            guard.check()
        guard.check((84,0,200,100))
        guard.remove('overlay')
        guard.check((-110,-10,200,100))

    def test_capture_during_move_or_destroy_is_discarded(self):
        guard = capture.CaptureGuard()
        guard.set_window('overlay', (500,0,684,88), False)
        version = guard.check((0,0,184,88))
        guard.moving('overlay', (0,0,184,88))
        with self.assertRaises(capture.CaptureOccluded):
            guard.check((0,0,184,88), version)
        with self.assertRaises(capture.CaptureOccluded):
            guard.check((500,0,684,88))  # Old geometry is protected until Configure arrives.
        guard.set_window('overlay', (0,0,184,88), False)
        guard.check((500,0,684,88))
        version = guard.check((500,0,684,88))
        guard.remove('overlay')
        with self.assertRaises(capture.CaptureOccluded):
            guard.check((500,0,684,88), version)



class CaptureBackendTest(unittest.TestCase):
    def test_backend_is_not_called_for_overlay_overlap(self):
        try:
            import buff_reminder_v2_dizi_hotkey as app
        except ImportError:
            self.skipTest('Install requirements.txt')
        camera = app.ScreenCapture()
        camera.guard.set_window('overlay', (10,10,194,98), False)
        with patch.object(app, 'mss') as backend:
            with self.assertRaises(capture.CaptureOccluded):
                camera.grab_rgb((0,0,300,150))
            backend.mss.assert_not_called()
        with patch.object(app.ImageGrab, 'grab') as backend:
            with self.assertRaises(capture.CaptureOccluded):
                camera.full_screen()
            backend.assert_not_called()

    def test_fallback_capture_discards_frame_if_overlay_moves_during_grab(self):
        try:
            import buff_reminder_v2_dizi_hotkey as app
            from PIL import Image
        except ImportError:
            self.skipTest('Install requirements.txt')
        camera = app.ScreenCapture()
        camera.guard.set_window('overlay', (500,0,684,88), False)
        def grab(**kwargs):
            camera.guard.moving('overlay', (0,0,184,88))
            return Image.new('RGB', (200,100), 'white')
        with patch.object(app, 'mss', None), patch.object(app.ImageGrab, 'grab', side_effect=grab):
            with self.assertRaises(capture.CaptureOccluded):
                camera.grab_rgb((0,0,200,100))


if __name__ == '__main__':
    unittest.main()
