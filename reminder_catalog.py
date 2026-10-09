"""Data-driven monitors sharing a Debuff region and a Qishu region."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    school: str
    kind: str
    cd: float
    icon: str
    engine: str = "generic"
    category: str = ""


def load_catalog(root: Path) -> list[Skill]:
    data = json.loads((root / 'skills.json').read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') not in (1, 2) or not isinstance(data.get('skills'), list):
        raise ValueError('skills.json 需要 version: 2 和 skills 数组')
    result, ids, engines = [], set(), set()
    for row in data['skills']:
        if data['version'] == 2 and 'category' not in row:
            raise ValueError('每项技能必须填写 category: debuff 或 qishu')
        skill = Skill(**{'school': '自定义',
                         'category': 'debuff' if row.get('kind') == 'buff' else 'qishu', **row})
        if skill.category not in ('debuff', 'qishu') or skill.category != ('debuff' if skill.kind == 'buff' else 'qishu'):
            raise ValueError(f'{skill.id}: category 与 kind 不一致')
        if not re.fullmatch(r'[a-z][a-z0-9_]*', skill.id) or skill.id in ids:
            raise ValueError('技能 id 必须唯一，且仅含小写字母、数字、下划线')
        if not isinstance(skill.name, str) or not skill.name.strip():
            raise ValueError('技能名不能为空')
        if skill.kind not in ('buff', 'qishu') or skill.engine not in ('generic', 'legacy_loss', 'legacy_dizi'):
            raise ValueError(f'{skill.id}: 不支持的 kind / engine')
        if isinstance(skill.cd, bool) or not isinstance(skill.cd, (int, float)) or not math.isfinite(skill.cd) or not 0 < skill.cd <= 3600:
            raise ValueError(f'{skill.id}: cd 必须为 0～3600 之间的正数（秒）')
        relative = Path(skill.icon)
        if relative.is_absolute() or '..' in relative.parts or relative.suffix.lower() != '.png':
            raise ValueError(f'{skill.id}: icon 必须是仓库内的 PNG 相对路径')
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            raise ValueError(f'{skill.id}: 图标不能指向仓库外')
        with Image.open(path) as image:
            if image.mode != 'RGBA' or image.getchannel('A').getextrema()[0] == 255:
                raise ValueError(f'{skill.id}: 图标需要透明背景 RGBA PNG')
        if skill.engine != 'generic':
            expected = ('loss', 'buff') if skill.engine == 'legacy_loss' else ('dizi', 'qishu')
            if (skill.id, skill.kind) != expected or skill.engine in engines:
                raise ValueError('内置引擎只供 loss / dizi 使用，新增项目请省略 engine')
            engines.add(skill.engine)
        ids.add(skill.id)
        result.append(skill)
    if not result:
        raise ValueError('监控列表不能为空')
    return result


class IconMatcher:
    """Match white transparent glyphs, retaining identity when they darken."""
    def __init__(self, path):
        alpha = np.asarray(Image.open(path).convert('RGBA'))[..., 3]
        ys, xs = np.nonzero(alpha >= 32)
        if len(xs) < 20:
            raise ValueError('图标有效像素不足')
        self.alpha = alpha[ys.min():ys.max()+1, xs.min():xs.max()+1]
        self._templates = {}

    def match(self, rgb, width=None):
        if rgb.ndim != 3 or min(rgb.shape[:2]) < 6:
            return None
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        if gray.std() < 3:
            return None
        fit = min(rgb.shape[1], rgb.shape[0] * self.alpha.shape[1] / self.alpha.shape[0])
        widths = (range(max(6, round(width*.9)), round(width*1.1)+1) if width else
                  range(12, min(160, int(fit))+1, 2))
        best = None
        for w in widths:
            h = round(w*self.alpha.shape[0]/self.alpha.shape[1])
            if min(w, h) < 6 or w > rgb.shape[1] or h > rgb.shape[0]:
                continue
            if w not in self._templates:
                template = cv2.resize(self.alpha.astype(np.float32), (w, h))
                self._templates[w] = template, template >= 220
            template, mask = self._templates[w]
            if mask.sum() < 12 or template.std() < 1:
                continue
            response = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(np.nan_to_num(response, nan=0, posinf=0, neginf=0))
            light = float(gray[y:y+h, x:x+w][mask].mean())
            if best is None or score > best['score']:
                best = dict(score=score, brightness=light, width=w, box=(x, y, x+w, y+h))
        return best



class SkillState:
    """Confirm transitions; absence/occlusion is never evidence of a cast."""
    def __init__(self, skill):
        self.skill = skill
        self.reset()

    def reset(self):
        self.visible = self.ready = False
        self.started = self.last_seen = self.last_sample = None
        self.on_count = self.off_count = self.refresh_count = 0
        self.candidate = None
        self.warned = False
        self.last_alert = None
        self.baseline = deque(maxlen=5)

    def update(self, present, ratio, now, warning=2, repeat_interval=None, settings=None):
        settings = settings or {}
        if self.last_sample is not None and now-self.last_sample > 2:
            self.reset()
        self.last_sample = now
        self.visible = present
        event = None
        if not present:
            self.on_count = self.off_count = self.refresh_count = 0
            self.candidate = None
            # For a skill bar disappearance, forget readiness to avoid an inferred cast.
            if self.skill.kind == 'qishu':
                self.ready = False
                self.last_alert = None
            if self.last_seen is not None and now-self.last_seen > 2:
                self.reset()
            return self.snapshot(now)
        self.last_seen = now
        if self.skill.kind == 'qishu':
            if not self.ready:
                self.on_count = self.on_count+1 if ratio >= float(settings.get('dizi_ready_on', .8)) else 0
                if self.on_count >= int(settings.get('dizi_ready_frames', 2)):
                    self.ready, self.started, self.on_count = True, None, 0
                    event = 'ready'
                    self.last_alert = now
            else:
                if ratio <= float(settings.get('dizi_ready_off', .45)):
                    if self.off_count == 0:
                        self.candidate = now
                    self.off_count += 1
                    if self.off_count >= int(settings.get('dizi_off_frames', 2)):
                        self.ready, self.started = False, self.candidate
                        self.off_count = 0
                        self.last_alert = None
                else:
                    self.off_count, self.candidate = 0, None
                if self.ready and not self.off_count and repeat_interval is not None and self.last_alert is not None and now-self.last_alert >= repeat_interval:
                    event, self.last_alert = 'repeat', now
        else:
            if self.started is None:
                if ratio >= .8:
                    if self.on_count == 0:
                        self.candidate = now
                    self.on_count += 1
                    if self.on_count >= 2:
                        self.started, self.warned = self.candidate, False
                else:
                    self.on_count = 0
            baseline = float(np.median(self.baseline)) if self.baseline else ratio
            if len(self.baseline) >= 3 and ratio-baseline >= .18:
                if self.refresh_count == 0:
                    self.candidate = now
                self.refresh_count += 1
                if self.refresh_count >= 2:
                    self.started, self.warned = self.candidate, False
                    self.baseline.clear()
                    self.refresh_count = 0
            else:
                self.refresh_count = 0
                self.baseline.append(ratio)
            remaining = None if self.started is None else self.skill.cd-(now-self.started)
            if remaining is not None and 0 < remaining <= warning and not self.warned:
                self.warned, event = True, 'expiring'
        return self.snapshot(now, event)

    def snapshot(self, now, event=None):
        remaining = None if self.started is None else max(0, self.skill.cd-(now-self.started))
        return dict(id=self.skill.id, visible=self.visible, ready=self.ready,
                    remaining=remaining, event=event, observed=now, cooldown_start=self.started, last_alert=self.last_alert)


class CatalogMonitor:
    def __init__(self, root, skills, cfg):
        self.skills, self.cfg = skills, cfg
        self.matchers = {s.id: IconMatcher(root/s.icon) for s in skills if s.engine != 'legacy_loss'}
        self.states = {s.id: SkillState(s) for s in skills if s.engine != 'legacy_loss'}
        self.latest = {}
        self.widths = {}
        self.next_sample = 0

    def enabled(self, skill):
        if skill.engine == 'legacy_loss':
            return bool(self.cfg.get('loss_enabled', True))
        if skill.engine == 'legacy_dizi':
            return bool(self.cfg.get('dizi_enabled', True))
        return bool(self.cfg.get('monitored_skills', {}).get(skill.id, False))

    def reset(self):
        for state in self.states.values():
            state.reset()
        self.latest = {}
        self.widths = {}
        self.next_sample = 0

    def region(self, category):
        key = {'debuff': 'loss_roi', 'qishu': 'qishu_roi'}[category]
        roi = self.cfg.get(key)
        if not isinstance(roi, (list, tuple)) or len(roi) != 4 or not all(isinstance(v, int) for v in roi):
            return None
        if not (6 <= roi[2]-roi[0] <= 8192 and 6 <= roi[3]-roi[1] <= 4320):
            return None
        return tuple(roi)

    def sample(self, capture, now, occluded_type):
        if now < self.next_sample:
            return None
        self.next_sample = now+1/max(1, float(self.cfg.get('catalog_hz', 10)))
        threshold = float(self.cfg.get('catalog_match_threshold', .8))
        results = []
        # Every enabled icon in a category sees the same frame, independent of slot order.
        for category in ('debuff', 'qishu'):
            skills = [s for s in self.skills if s.category == category
                      and s.engine != 'legacy_loss' and self.enabled(s)]
            if not skills:
                continue
            roi = self.region(category)
            try:
                if roi is None:
                    raise ValueError('请先框选 '+('Debuff 栏区' if category == 'debuff' else '奇术区'))
                rgb = capture.grab_rgb(roi)
            except (occluded_type, ValueError, OSError) as exc:
                for skill in skills:
                    results.append(dict(id=skill.id, visible=False, event=None, observed=now,
                                        error='浮层遮挡，暂缓检测' if isinstance(exc, occluded_type) else str(exc)))
                continue
            for skill in skills:
                matcher = self.matchers[skill.id]
                width = self.widths.get(skill.id)
                found = matcher.match(rgb, width)
                if width is not None and (found is None or found['score'] < threshold):
                    found = matcher.match(rgb)
                present = found is not None and found['score'] >= threshold and found['brightness'] >= 12
                if present:
                    self.widths[skill.id] = found['width']
                else:
                    self.widths.pop(skill.id, None)
                # White source glyphs need no per-skill screenshot or calibration mask.
                white = max(100, min(255, float(self.cfg.get('dizi_white_threshold', 225))))
                ratio = found['brightness']/white if present else 0
                repeat = float(self.cfg.get('dizi_repeat_interval', 25)) if skill.engine == 'legacy_dizi' else None
                result = self.states[skill.id].update(present, ratio, now,
                            float(self.cfg.get('warning_before', 2)), repeat_interval=repeat,
                            settings=self.cfg if skill.engine == 'legacy_dizi' else None)
                result['box'] = found['box'] if present else None
                results.append(result)
        return results
