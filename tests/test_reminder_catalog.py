import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from reminder_catalog import CatalogMonitor, IconMatcher, Skill, SkillState, load_catalog
from reminder_capture import CaptureOccluded


ROOT = Path(__file__).resolve().parents[1]


def synthetic(matcher, brightness=1):
    alpha = matcher.alpha
    h = round(48*alpha.shape[0]/alpha.shape[1])
    glyph = cv2.resize(alpha, (48, h))
    rgb = np.zeros((h+12, 60, 3), np.uint8)
    rgb[6:6+h, 6:54] = (glyph[..., None]*brightness).astype(np.uint8)
    return rgb


class CatalogTest(unittest.TestCase):
    def test_other_bundled_skills_cannot_be_calibrated_as_this_skill(self):
        skills = [s for s in load_catalog(ROOT) if s.engine == 'generic']
        matchers = {s.id: IconMatcher(ROOT/s.icon) for s in skills}
        for name, matcher in matchers.items():
            for other, source in matchers.items():
                if other == name:
                    continue
                with self.subTest(target=name, wrong_icon=other), self.assertRaises(ValueError):
                    rgb = synthetic(source)
                    matcher.calibrate(rgb, [0, 0, rgb.shape[1], rgb.shape[0]])

    def test_all_requested_assets_load_and_calibrate_without_raw_materials(self):
        skills = load_catalog(ROOT)
        self.assertEqual({s.name: s.cd for s in skills if s.engine == 'generic'},
                         {'威威易伤': 16, '真气失衡': 15, '骑龙回马': 12, '千灯一月': 40})
        for skill in skills:
            with self.subTest(skill=skill.name):
                matcher = IconMatcher(ROOT/skill.icon)
                rgb = synthetic(matcher)
                calibration = matcher.calibrate(rgb, [0, 0, rgb.shape[1], rgb.shape[0]])
                self.assertAlmostEqual(calibration['width'], 48, delta=2)
                dark = matcher.match(synthetic(matcher, .3), calibration['width'])
                self.assertGreater(dark['score'], .9)
                self.assertLess(dark['brightness']/calibration['brightness'], .4)
                with self.assertRaises(ValueError):
                    matcher.calibrate(np.zeros_like(rgb), [0, 0, 60, 60])
                with self.assertRaises(ValueError):
                    matcher.calibrate(np.full_like(rgb, 255), [0, 0, 60, 60])

    def test_config_rejects_paths_outside_repo_duplicates_and_invalid_durations(self):
        source = json.loads((ROOT/'skills.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for change in ({'icon': '../secret.png'}, {'icon': '/tmp/secret.png'},
                           {'cd': 0}, {'cd': float('nan')}, {'id': '../unsafe'}):
                row = dict(source['skills'][0], **change)
                (root/'skills.json').write_text(json.dumps({'version': 1, 'skills': [row]}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_catalog(root)

    def test_independent_regions_and_occlusion_do_not_cast_other_skills(self):
        skills = [s for s in load_catalog(ROOT) if s.kind == 'qishu' and s.engine == 'generic']
        cfg = {'monitored_skills': {s.id: True for s in skills}, 'skill_calibrations': {}}
        engine = CatalogMonitor(ROOT, skills, cfg)
        frames = {}
        for i, skill in enumerate(skills):
            rgb = synthetic(engine.matchers[skill.id])
            roi = [i*100, 0, i*100+rgb.shape[1], rgb.shape[0]]
            calibration = engine.matchers[skill.id].calibrate(rgb, roi)
            cfg['skill_calibrations'][skill.id] = dict(calibration, icon=skill.icon)
            frames[roi[0]] = rgb
        class Capture:
            def grab_rgb(self, roi):
                if frames[roi[0]] is None:
                    raise CaptureOccluded('blocked')
                return frames[roi[0]]
        engine.sample(Capture(), 1, CaptureOccluded)
        states = engine.sample(Capture(), 1.2, CaptureOccluded)
        self.assertEqual([s['event'] for s in states], ['ready', 'ready'])
        frames[0] = None
        frames[100] = (frames[100]*.3).astype(np.uint8)
        engine.sample(Capture(), 1.4, CaptureOccluded)
        states = engine.sample(Capture(), 1.6, CaptureOccluded)
        self.assertIn('error', states[0])
        self.assertIsNone(engine.states[skills[0].id].started)
        self.assertAlmostEqual(states[1]['remaining'], 39.8)


class StateTest(unittest.TestCase):
    def state(self, kind, duration):
        return SkillState(Skill('test', '测试', '测试', kind, duration, 'unused.png'))

    def test_qishu_starts_only_on_confirmed_use_not_initial_dark_or_disappearance(self):
        for cd in (12, 40):
            state = self.state('qishu', cd)
            for i in range(4):
                self.assertIsNone(state.update(True, .3, i*.2)['remaining'])
            state.update(True, 1, 1)
            self.assertEqual(state.update(True, 1, 1.2)['event'], 'ready')
            state.update(True, .3, 1.4)
            self.assertIsNone(state.started)
            state.update(True, 1, 1.6)  # a single dim frame is ignored
            state.update(False, 0, 1.8)
            state.update(True, .3, 2)
            self.assertIsNone(state.started)
            state.update(True, 1, 2.2)
            state.update(True, 1, 2.4)
            state.update(True, .3, 2.6)
            result = state.update(True, .3, 2.8)
            self.assertAlmostEqual(result['remaining'], cd-.2)
            state.update(True, 1, 3)
            self.assertEqual(state.update(True, 1, 3.2)['event'], 'ready')
            self.assertIsNone(state.started)

    def test_buff_duration_warning_refresh_and_no_auto_restart_at_zero(self):
        for cd in (16, 15):
            state = self.state('buff', cd)
            state.update(True, 1, 0)
            self.assertAlmostEqual(state.update(True, 1, .2)['remaining'], cd-.2)
            events = []
            for i in range(2, (cd+2)*5):
                events.append(state.update(True, .7, i*.2)['event'])
            self.assertEqual(events.count('expiring'), 1)
            self.assertEqual(state.snapshot(cd+2)['remaining'], 0)
            state.update(True, 1, cd+2)
            result = state.update(True, 1, cd+2.2)
            self.assertAlmostEqual(result['remaining'], cd-.2)
            self.assertFalse(state.warned)

    def test_long_capture_gap_requires_new_evidence(self):
        state = self.state('qishu', 12)
        state.update(True, 1, 0)
        state.update(True, 1, .2)
        state.update(True, .3, 5)
        state.update(True, .3, 5.2)
        self.assertIsNone(state.started)
        state.reset()
        self.assertFalse(state.visible)


if __name__ == '__main__':
    unittest.main()
