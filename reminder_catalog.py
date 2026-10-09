"""Data-driven, independently calibrated icon monitors. No screenshots are persisted."""
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


def load_catalog(root: Path) -> list[Skill]:
    data = json.loads((root / 'skills.json').read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('skills'), list):
        raise ValueError('skills.json 需要 version: 1 和 skills 数组')
    result, ids, engines = [], set(), set()
    for row in data['skills']:
        skill = Skill(**{'school': '自定义', **row})
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
                  sorted(set(round(fit*s) for s in np.linspace(.45, 1, 24))))
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
                best = dict(score=score, brightness=light, width=w)
        return best

    def calibrate(self, rgb, roi):
        found = self.match(rgb)
        if found is None or found['score'] < .80 or found['brightness'] < 100:
            raise ValueError('未匹配到亮起的原图。请紧贴完整图案框选，避开数字、边框与其他技能。')
        return dict(roi=list(roi), width=found['width'], brightness=found['brightness'])


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
        self.baseline = deque(maxlen=5)

    def update(self, present, ratio, now, warning=2):
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
            if self.last_seen is not None and now-self.last_seen > 2:
                self.reset()
            return self.snapshot(now)
        self.last_seen = now
        if self.skill.kind == 'qishu':
            if not self.ready:
                self.on_count = self.on_count+1 if ratio >= .8 else 0
                if self.on_count >= 2:
                    self.ready, self.started, self.on_count = True, None, 0
                    event = 'ready'
            else:
                if ratio <= .45:
                    if self.off_count == 0:
                        self.candidate = now
                    self.off_count += 1
                    if self.off_count >= 2:
                        self.ready, self.started = False, self.candidate
                        self.off_count = 0
                else:
                    self.off_count, self.candidate = 0, None
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
                    remaining=remaining, event=event, observed=now)


class CatalogMonitor:
    def __init__(self, root, skills, cfg):
        self.skills, self.cfg = skills, cfg
        self.matchers = {s.id: IconMatcher(root/s.icon) for s in skills if s.engine == 'generic'}
        self.states = {s.id: SkillState(s) for s in skills if s.engine == 'generic'}
        self.latest = {}
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
        self.next_sample = 0

    def calibration(self, skill):
        value = self.cfg.get('skill_calibrations', {}).get(skill.id)
        if not isinstance(value, dict) or value.get('icon') != skill.icon:
            return None
        try:
            roi = value['roi']
            if len(roi) != 4 or not all(isinstance(v, int) for v in roi):
                return None
            w, h = roi[2]-roi[0], roi[3]-roi[1]
            if not (6 <= w <= 4096 and 6 <= h <= 4096 and 6 <= value['width'] <= w
                    and 100 <= value['brightness'] <= 255):
                return None
        except (KeyError, TypeError):
            return None
        return value

    def sample(self, capture, now, occluded_type):
        if now < self.next_sample:
            return None
        self.next_sample = now+.1
        results = []
        for skill in self.skills:
            if skill.engine != 'generic' or not self.enabled(skill):
                continue
            calibration = self.calibration(skill)
            if calibration is None:
                continue
            try:
                rgb = capture.grab_rgb(calibration['roi'])
                found = self.matchers[skill.id].match(rgb, calibration['width'])
                present = found is not None and found['score'] >= .70 and found['brightness'] >= 12
                ratio = found['brightness']/calibration['brightness'] if present else 0
                result = self.states[skill.id].update(present, ratio, now, float(self.cfg.get('warning_before', 2)))
            except occluded_type:
                result = dict(id=skill.id, visible=False, event=None, observed=now, error='浮层遮挡，暂缓检测')
            except (ValueError, OSError) as exc:
                result = dict(id=skill.id, visible=False, event=None, observed=now, error=str(exc))
            results.append(result)
        return results
