"""Refresh confirmation based on local changes, independent of an absolute dark gate."""
from collections import deque
from statistics import median


class RelativeRefreshDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.raw = deque(maxlen=max(1, int(cfg['refresh_median_n'])))
        self.stable = deque(maxlen=max(3, int(cfg['refresh_baseline_n'])))
        self.phases = deque(maxlen=max(3, int(cfg['refresh_baseline_n'])))
        self.phase_baseline = None
        self.last_seen = None
        self.last_refresh = None
        self.candidate_start = None
        self.candidate_count = 0
        self.baseline = None

    def update(self, value, now, tracker_frames, reacquire_count, visual_fraction=None):
        if self.last_seen is not None and now-self.last_seen > float(self.cfg.get("refresh_observation_gap", 2.0)):
            self.raw.clear()
            self.stable.clear()
            self.phases.clear()
            self.candidate_start = None
            self.candidate_count = 0
        self.last_seen = now
        self.raw.append(value)
        smooth = float(median(self.raw))
        baseline = float(self.baseline if self.candidate_start is not None else
                         median(self.stable) if self.stable else smooth)
        jump = smooth-baseline
        phase_base = (self.phase_baseline if self.candidate_start is not None else
                      float(median(self.phases)) if len(self.phases) >= 3 else None)
        phase_jump = (visual_fraction-phase_base if visual_fraction is not None and phase_base is not None else 0.)
        phase_reset = phase_jump >= float(self.cfg.get("refresh_phase_jump", .25))
        blocked = tracker_frames < int(self.cfg['reacquire_block_frames'])
        warmed = len(self.stable) >= 3
        refractory = self.last_refresh is not None and now-self.last_refresh < .5
        qualifies = warmed and not blocked and not refractory and (jump >= float(self.cfg['refresh_min_jump']) or phase_reset)
        result = dict(raw=value, smooth=smooth, baseline=baseline, jump=jump,
                      visual_fraction=visual_fraction, phase_jump=phase_jump, phase_baseline=phase_base,
                      evidence="phase" if phase_reset else "brightness", armed=warmed, candidate=False, refresh=False, fill_time=None,
                      blocked=blocked, state='观察图案亮度变化')
        if qualifies:
            if self.candidate_start is None:
                self.candidate_start = now
                self.candidate_count = 0
                self.baseline = baseline
                self.phase_baseline = phase_base
            self.candidate_count += 1
            result.update(candidate=True, state='确认倒计时扇区回跳' if phase_reset else '确认刷新亮度跃升')
            if (self.candidate_count >= int(self.cfg['refresh_confirm_n'])
                    and (now-self.candidate_start)*1000 >= float(self.cfg['refresh_hold_ms'])):
                result.update(refresh=True, fill_time=self.candidate_start, candidate=False, state='刷新确认')
                self.last_refresh = now
                self.candidate_start = None
                self.candidate_count = 0
                self.stable.clear()
                self.stable.append(smooth)
                self.phases.clear()
                if visual_fraction is not None:
                    self.phases.append(visual_fraction)
        else:
            self.candidate_start = None
            self.candidate_count = 0
            # Do not overwrite the pre-refresh baseline with reacquisition frames.
            if not blocked:
                self.stable.append(smooth)
                if visual_fraction is not None:
                    self.phases.append(visual_fraction)
            if blocked:
                result['state'] = '重捕获保护'
        return result
