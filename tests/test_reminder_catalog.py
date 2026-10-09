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


def icon_row(matchers, order, brightness=None):
    row = np.zeros((128, 480, 3), np.uint8)
    for slot, name in enumerate(order):
        rgb = synthetic(matchers[name], (brightness or {}).get(name, 1))
        h, w = rgb.shape[:2]
        row[10:10+h, 10+slot*100:10+slot*100+w] = rgb
    return row


class CatalogTest(unittest.TestCase):
    def test_catalog_categories_and_durations(self):
        skills = load_catalog(ROOT)
        self.assertEqual({s.name: (s.cd, s.category) for s in skills if s.engine == 'generic'},
                         {'威威易伤': (16, 'debuff'), '真气失衡': (15, 'debuff'),
                          '骑龙回马': (12, 'qishu'), '千灯一月': (40, 'qishu')})
        self.assertEqual(next(s.category for s in skills if s.id == 'loss'), 'debuff')
        self.assertEqual(next(s.category for s in skills if s.id == 'dizi'), 'qishu')

    def test_multiscale_source_matching_without_calibration(self):
        for skill in load_catalog(ROOT):
            with self.subTest(skill=skill.name):
                matcher = IconMatcher(ROOT/skill.icon)
                found = matcher.match(synthetic(matcher))
                self.assertGreater(found['score'], .9)
                self.assertEqual(found['width'], 48)
                dark = matcher.match(synthetic(matcher, .3), 48)
                self.assertGreater(dark['score'], .9)
                self.assertLess(dark['brightness']/225, .4)
                self.assertIsNone(matcher.match(np.zeros((128, 400, 3), np.uint8)))
                self.assertIsNone(matcher.match(np.full((128, 400, 3), 255, np.uint8)))

    def test_two_frames_shared_by_category_search_all_icons_after_slot_reordering(self):
        skills = load_catalog(ROOT)
        cfg = {'monitored_skills': {s.id: True for s in skills},
               'loss_roi': [-480, 0, 0, 128], 'qishu_roi': [0, 500, 480, 628],
               # Old single-icon regions must never be captured.
               'skill_calibrations': {'qilong': {'roi': [900,900,910,910]}},
               'dizi_calibration': {'roi': [900,900,910,910]}}
        engine = CatalogMonitor(ROOT, skills, cfg)
        matchers = {s.id: IconMatcher(ROOT/s.icon) for s in skills}
        debuff = icon_row(matchers, ['loss','weiwei','imbalance'])
        qishu = icon_row(matchers, ['dizi','qilong','qiandeng'])
        calls = []
        class Capture:
            blocked = False
            def grab_rgb(self, roi):
                calls.append(tuple(roi))
                if roi[0] == -480:
                    if self.blocked:
                        raise CaptureOccluded('blocked')
                    return debuff
                return qishu
        capture = Capture()
        first = engine.sample(capture, 1, CaptureOccluded)
        self.assertTrue(all(s['visible'] for s in first), first)
        self.assertEqual(calls, [tuple(cfg['loss_roi']), tuple(cfg['qishu_roi'])])
        second = engine.sample(capture, 1.2, CaptureOccluded)
        ready = {s['id'] for s in second if s['event'] == 'ready'}
        self.assertEqual(ready, {'dizi','qilong','qiandeng'})
        # The same shared region is searched again, even when every icon changes slots.
        debuff = icon_row(matchers, ['imbalance','loss','weiwei'])
        qishu = icon_row(matchers, ['qiandeng','dizi','qilong'], {'qilong':.3})
        engine.sample(capture, 1.4, CaptureOccluded)
        moved = {s['id']:s for s in engine.sample(capture, 1.6, CaptureOccluded)}
        self.assertTrue(all(s['visible'] for s in moved.values()), moved)
        self.assertAlmostEqual(moved['qilong']['remaining'], 11.8)
        self.assertTrue(moved['dizi']['ready'])
        self.assertTrue(moved['qiandeng']['ready'])
        self.assertIsNone(moved['qiandeng']['remaining'])
        # A blocked Debuff region does not block the Qishu region or infer any cast.
        capture.blocked = True
        blocked = {s['id']:s for s in engine.sample(capture, 1.8, CaptureOccluded)}
        self.assertIn('error', blocked['weiwei'])
        self.assertTrue(blocked['dizi']['visible'])
        # Blank HUD is missing, not skill usage; reacquiring dark also cannot infer usage.
        qishu = np.zeros_like(qishu)
        missing = engine.sample(capture, 2, CaptureOccluded)
        self.assertTrue(all(not s['visible'] for s in missing))
        qishu = icon_row(matchers, ['dizi','qilong','qiandeng'], {'dizi':.3,'qilong':.3,'qiandeng':.3})
        engine.sample(capture, 2.2, CaptureOccluded)
        reacquired = {s['id']:s for s in engine.sample(capture, 2.4, CaptureOccluded)}
        self.assertIsNone(reacquired['dizi']['remaining'])
        self.assertIsNone(reacquired['qiandeng']['remaining'])
        # The legacy loss tracker uses exactly the same Debuff selection, not a third ROI.
        import buff_reminder_v2_dizi_hotkey as app
        tracker = app.LossTracker(dict(app.DEFAULT_CONFIG, loss_roi=cfg['loss_roi']))
        capture.blocked = False
        crop, found = tracker.global_acquire(capture)
        self.assertIsNotNone(crop)
        self.assertIsNotNone(found)
        self.assertEqual(calls[-1], tuple(cfg['loss_roi']))

    def test_missing_category_region_does_not_use_obsolete_per_skill_regions(self):
        skills = load_catalog(ROOT)
        cfg = {'loss_enabled':False, 'dizi_enabled':False, 'monitored_skills':{'qilong':True},
               'skill_calibrations':{'qilong':{'roi':[0,0,100,100]}}}
        engine = CatalogMonitor(ROOT, skills, cfg)
        class Capture:
            def grab_rgb(self, roi):
                raise AssertionError('must not capture an obsolete ROI')
        result = engine.sample(Capture(), 1, CaptureOccluded)
        self.assertIn('error', result[0])
        self.assertIsNone(engine.region('qishu'))

    def test_config_rejects_external_paths_and_inconsistent_categories(self):
        source = json.loads((ROOT/'skills.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for change in ({'icon': '../secret.png'}, {'icon': '/tmp/secret.png'},
                           {'cd': 0}, {'cd': float('nan')}, {'id': '../unsafe'},
                           {'category':'qishu'}, {'category':'other'}):
                row = dict(source['skills'][0], **change)
                (root/'skills.json').write_text(json.dumps({'version': 2, 'skills': [row]}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_catalog(root)
            row = dict(source['skills'][0]); del row['category']
            (root/'skills.json').write_text(json.dumps({'version':2, 'skills':[row]}))
            with self.assertRaises(ValueError):
                load_catalog(root)


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
