"""Exercise reminder controls without a display, capture drivers or OpenCV."""
from collections import deque
import ast
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock
import wave
import shutil


SOURCE = Path(__file__).resolve().parents[1] / "buff_reminder_v2_dizi_hotkey.py"


class ReminderControlsTest(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ReminderApp")
        self.env = dict(
            tk=types.SimpleNamespace(Tk=object, TclError=RuntimeError), deque=deque,
            Path=Path, ROOT=Path("/nonexistent-test-assets"), Image=Mock(), math=math, sys=types.SimpleNamespace(platform="test"),
            clamp=lambda v, lo, hi: max(lo, min(hi, v)),
            save_config=Mock(), winsound=Mock(), threading=Mock(),
            messagebox=Mock(), filedialog=Mock(), wave=wave, shutil=shutil,
        )
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE), "exec"), self.env)
        self.app = self.env["ReminderApp"].__new__(self.env["ReminderApp"])
        self.app.root = Mock()
        self.app.cfg = {"audio_enabled": True, "custom_audio": "", "dizi_custom_audio": ""}
        self.app.overlay = Mock()
        self.app.overlay_kind = "text"
        self.app.overlay_preview = False
        self.app.overlay_drag = None
        self.app.overlay_timer = "timer"

    def test_drag_cancels_expiry_and_persists_release_position(self):
        win = self.app.overlay
        win.winfo_x.return_value, win.winfo_y.return_value = 100, 200
        self.app._position_overlay = Mock()
        self.app._start_overlay_drag(types.SimpleNamespace(x_root=110, y_root=220), win)
        self.app.root.after_cancel.assert_called_once_with("timer")
        self.app._move_overlay_drag(types.SimpleNamespace(x_root=410, y_root=520), win)
        self.app._position_overlay.assert_called_with(win, 400, 500)
        win.winfo_x.return_value, win.winfo_y.return_value = 400, 500
        self.app._end_overlay_drag(types.SimpleNamespace(x_root=410, y_root=520), win)
        self.assertEqual(self.app.cfg["overlay_position"], [400, 500])
        self.env["save_config"].assert_called_once_with(self.app.cfg)
        self.app.root.after.assert_called_once()

    def test_capture_option_keeps_overlap_guard_on_creation_and_remap(self):
        from reminder_capture import CaptureGuard, CaptureOccluded
        self.env['tk'].EventType = types.SimpleNamespace(Map='map')
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                self.app.cfg['overlay_allow_capture'] = allowed
                native = Mock(return_value=True)
                self.env['exclude_window_from_capture'] = native
                guard = CaptureGuard()
                self.app.capture = types.SimpleNamespace(guard=guard)
                win = Mock()
                win._capture_bounds = (10, 10, 194, 98)
                win.winfo_rootx.return_value = win.winfo_rooty.return_value = 10
                win.winfo_width.return_value = win.winfo_reqwidth.return_value = 184
                win.winfo_height.return_value = win.winfo_reqheight.return_value = 88
                self.app._protect_overlay(win)
                callbacks = {call.args[0]: call.args[1] for call in win.bind.call_args_list}
                for remap in (False, True):
                    if remap:
                        callbacks['<Map>'](types.SimpleNamespace(widget=win, type='map'))
                    if allowed:
                        native.assert_not_called()
                        with self.assertRaises(CaptureOccluded):
                            guard.check((0, 0, 300, 150))
                    else:
                        guard.check((0, 0, 300, 150))
                        self.assertEqual(native.call_count, 2 if remap else 1)
                    guard.check((300, 300, 400, 400))

    def test_missing_templates_still_allows_ui_startup(self):
        self.env.update(
            APP_VERSION="test", load_config=lambda: {}, ScreenCapture=Mock(),
            LossTracker=Mock(side_effect=FileNotFoundError("missing template")),
            RefreshDetector=Mock(), DiziDetector=Mock(), queue=Mock(),
        )
        self.app._build_ui = Mock()
        self.app._update_summary = Mock()
        self.app.__init__(Mock())
        self.assertIsNone(self.app.tracker)
        self.assertIn("missing template", self.app.template_error)
        self.app._build_ui.assert_called_once()
        self.app.root.protocol.assert_called_once()

    def test_preview_does_not_expire_after_drag(self):
        self.app.overlay_preview = True
        self.app.overlay_drag = (0, 0)
        self.app._move_overlay_drag = Mock()
        self.app.overlay.winfo_x.return_value = 10
        self.app.overlay.winfo_y.return_value = 20
        self.app._end_overlay_drag(Mock(), self.app.overlay)
        self.app.root.after.assert_not_called()

    def test_stale_hide_does_not_cancel_new_overlay(self):
        current = self.app.overlay
        self.app._hide_overlay(Mock())
        self.assertIs(self.app.overlay, current)
        self.app.root.after_cancel.assert_not_called()
        self.app._hide_overlay(current)
        self.assertIsNone(self.app.overlay)
        self.app.root.after_cancel.assert_called_once()

    def test_position_is_kept_on_screen(self):
        win = self.app.overlay
        win.winfo_screenwidth.return_value = 1920
        win.winfo_screenheight.return_value = 1080
        win.winfo_reqwidth.return_value = 300
        win.winfo_reqheight.return_value = 80
        self.app._position_overlay(win, 9000, -400)
        win.geometry.assert_called_with("+1620+0")

    def test_sound_selection_mute_and_missing_file_fallback(self):
        sound = self.env["winsound"]
        sound.SND_FILENAME, sound.SND_ASYNC, sound.SND_NODEFAULT = 1, 2, 4
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "dizi.wav"
            audio.touch()
            self.app.cfg["dizi_custom_audio"] = str(audio)
            self.app.cfg["audio_enabled"] = False
            self.app._dizi_audio()
            sound.PlaySound.assert_not_called()
            self.app._play_audio("dizi_custom_audio", preview=True)
            sound.PlaySound.assert_called_once_with(str(audio), 7)
            audio.unlink()
            self.app.cfg["audio_enabled"] = True
            self.app._dizi_audio()
            self.env["threading"].Thread.return_value.start.assert_called_once()

    def test_per_reminder_mute_stops_voice_and_blocks_events(self):
        self.app._set_reminder_audio("dizi", False)
        self.env["winsound"].PlaySound.assert_called_once_with(None, 0)
        self.env["winsound"].PlaySound.reset_mock()
        self.app._dizi_audio()
        self.env["winsound"].PlaySound.assert_not_called()
        self.env["threading"].Thread.assert_not_called()
        self.assertFalse(self.app.cfg["dizi_audio_enabled"])
        self.env["save_config"].assert_called_once()

    def test_live_monitoring_replaces_icon_preview_with_real_state(self):
        self.app.overlay_kind = "icon"
        self.app.overlay_preview = True
        self.app.worker = Mock()
        self.app.worker.is_alive.return_value = True
        self.app._ensure_icon_overlay()
        self.assertFalse(self.app.overlay_preview)

    def test_global_mute_works_with_incomplete_warning_entry(self):
        self.app.warning_var = Mock()
        self.app.warning_var.get.side_effect = ValueError("incomplete")
        self.app.audio_var = Mock()
        self.app.audio_var.get.return_value = False
        self.app.visual_var = Mock()
        self.app.visual_var.get.return_value = True
        self.app._update_summary = Mock()
        self.app.apply_quick()
        self.assertFalse(self.app.cfg["audio_enabled"])
        self.env["winsound"].PlaySound.assert_called_once_with(None, 0)
        self.env["save_config"].assert_called_once()

    def test_import_validates_wav_and_preserves_other_sound(self):
        with tempfile.TemporaryDirectory() as directory:
            self.env["AUDIO_DIR"] = Path(directory) / "audio"
            source = Path(directory) / "source.wav"
            self.app._update_summary = Mock()
            self.env["filedialog"].askopenfilename.return_value = str(source)
            source.write_text("not audio")
            self.app.choose_audio("dizi_custom_audio")
            self.env["save_config"].assert_not_called()
            with wave.open(str(source), "wb") as output:
                output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                output.writeframes(b"\0\0" * 80)
            self.app.choose_audio("dizi_custom_audio")
            dest = Path(self.app.cfg["dizi_custom_audio"])
            self.assertEqual(dest.read_bytes(), source.read_bytes())
            self.assertEqual(self.app.cfg["custom_audio"], "")
            self.env["filedialog"].askopenfilename.return_value = str(dest)
            self.app.choose_audio("dizi_custom_audio")  # Selecting stored WAV is safe.
            self.assertEqual(self.env["save_config"].call_count, 2)


if __name__ == "__main__":
    unittest.main()
