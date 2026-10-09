from __future__ import annotations

from collections import deque
import ctypes
import json
import math
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageGrab, ImageTk

from reminder_assets import OriginalAssets
from reminder_presentation import format_reminder, cooldown_icon, dizi_icon_state
from reminder_capture import CaptureGuard, CaptureOccluded, exclude_window_from_capture
from reminder_timing import RelativeRefreshDetector
from reminder_catalog_ui import CatalogUI

try:
    import mss
except Exception:
    mss = None

try:
    import winsound
except Exception:
    winsound = None

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk


APP_NAME = "尘Buff提醒器_追踪明度版"
APP_VERSION = "v2.6 多技能监控"
ICON_W = 43
ICON_H = 63


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


ROOT = resource_dir()
TEMPLATES = ROOT / "templates"
CONFIG_DIR = Path(os.getenv("APPDATA", Path.home())) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
AUDIO_DIR = CONFIG_DIR / "audio"
DEBUG_DIR = CONFIG_DIR / "debug"
DIZI_TEMPLATE_PATH = CONFIG_DIR / "dizi_template.npz"


# Windows virtual-key codes used for the global pause/resume hotkey.
# GetAsyncKeyState only observes the key; it does not swallow it from the game.
HOTKEY_VK = {
    "NumLock": 0x90,
    "ScrollLock": 0x91,
    "Pause": 0x13,
    "Insert": 0x2D,
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "Numpad0": 0x60,
    "Numpad1": 0x61,
    "Numpad2": 0x62,
    "Numpad3": 0x63,
    "Numpad4": 0x64,
    "Numpad5": 0x65,
    "Numpad6": 0x66,
    "Numpad7": 0x67,
    "Numpad8": 0x68,
    "Numpad9": 0x69,
}
HOTKEY_CHOICES = tuple(HOTKEY_VK.keys())


DEFAULT_CONFIG = {
    # Screen / basic
    "loss_roi": [350, 10, 1000, 130],
    "global_fps": 3,
    "global_step": 2,
    "loss_icon_width": 48,
    "loss_match_threshold": .90,
    "loss_match_margin": .04,
    "refresh_phase_jump": .25,
    "refresh_observation_gap": 2.0,

    # Sobel / Directional Chamfer identity
    "normalize": True,
    "low_pct": 18.0,
    "high_pct": 96.0,
    "sobel_gain": 0.5,
    "sobel_binary": 103,
    "max_dist": 5.0,
    "pos_weight": 0.65,
    "dir_weight": 0.35,
    "orient_radius": 2,
    "anis_threshold": 0.20,
    "template_stride": 1,
    "max_mean_dist": 0.80,
    "max_mean_dir": 35.0,
    "point_dir_tol": 30.0,
    "min_dir_pass": 70.0,

    # Tracker — defaults use the stable values from the latest validation
    "track_hz": 25,
    "track_r1": 8,
    "track_r2": 25,
    "track_r3": 64,
    "track_step1": 1,
    "track_step2": 6,
    "track_step3": 4,
    "track_refine_r": 3,
    "track_move_weight": 0.03,
    "track_predict": False,

    # Brightness / refresh, all brightness values are percentages 0..100
    "refresh_high": 58.0,
    "refresh_low": 50.0,
    "refresh_min_jump": 8.0,
    "refresh_median_n": 3,
    "refresh_baseline_n": 7,
    "refresh_confirm_n": 2,
    "refresh_hold_ms": 80,
    "reacquire_block_frames": 2,
    "mask_offset_x": 0,
    "mask_offset_y": 0,

    # Timer / reminder
    "loss_duration": 21.0,
    "warning_before": 2.0,
    "auto_birth_axis": True,
    "birth_confirm_n": 2,
    "audio_enabled": True,
    "visual_enabled": True,
    "custom_audio": "",
    "dizi_custom_audio": "",
    "overlay_position": None,
    "loss_text": "{%s} 剩余 {%d} 秒",
    "dizi_text": "{%s}好了",
    "loss_skill_name": "失魂落魄",
    "dizi_skill_name": "笛子",
    "loss_sound": "baidu",
    "dizi_sound": "baidu",
    "overlay_mode": "text",
    "overlay_allow_capture": False,
    "icon_color": "#b95739",
    "icon_numbers": True,
    "dizi_cooldown_seconds": 24,
    "dizi_icon_timer": "cooldown",
    "loss_audio_enabled": True,
    "dizi_audio_enabled": True,
    "dizi_calibration": None,

    # Dizi reminder — fixed ROI, white-pixel template
    "dizi_enabled": True,
    "dizi_repeat_interval": 25.0,
    "dizi_white_threshold": 225,
    "dizi_presence_threshold": .30,
    "dizi_ready_on": 0.80,
    "dizi_ready_off": 0.35,
    "dizi_ready_frames": 2,
    "dizi_off_frames": 2,
    "dizi_hz": 20,

    # Global pause / resume hotkey
    "pause_hotkey": "NumLock",

    # Template overrides. Empty = bundled.
    "identity_template": "",
    "tracking_template": "",
    "brightness_mask": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    cfg["dizi_icon_timer"] = "cooldown"
    if not cfg.get("dizi_cooldown_24_migrated", False):
        if cfg.get("dizi_cooldown_seconds") in (None, 0, "0", ""):
            cfg["dizi_cooldown_seconds"] = 24
        cfg["dizi_cooldown_24_migrated"] = True
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_DIR,
                                     suffix=".tmp", delete=False) as output:
        json.dump(cfg, output, ensure_ascii=False, indent=2)
        temp_path = Path(output.name)
    try:
        os.replace(temp_path, CONFIG_PATH)
    finally:
        temp_path.unlink(missing_ok=True)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def angle_diff_pi(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = np.mod(np.abs(a - b), np.pi)
    return np.where(d > np.pi / 2, np.pi - d, d)


def percentile_normalize(gray: np.ndarray, low_pct: float, high_pct: float) -> np.ndarray:
    lo = float(np.percentile(gray, low_pct))
    hi = float(np.percentile(gray, high_pct))
    if hi <= lo + 1.0:
        return gray.astype(np.float32, copy=True)
    return np.clip((gray - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.float32)


def image_to_template_binary(path: Path, threshold: int) -> np.ndarray:
    im = Image.open(path).convert("RGBA").resize((ICON_W, ICON_H), Image.Resampling.NEAREST)
    rgba = np.asarray(im, dtype=np.uint8)
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgb * alpha
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])
    return (gray >= threshold).astype(np.uint8)


@dataclass
class TemplatePoints:
    x: np.ndarray
    y: np.ndarray
    theta: np.ndarray
    valid: np.ndarray

    @property
    def count(self) -> int:
        return int(self.x.size)

    @property
    def valid_count(self) -> int:
        return int(np.count_nonzero(self.valid))


def estimate_template_points(
    binary: np.ndarray,
    orient_radius: int,
    anis_threshold: float,
    stride: int,
) -> TemplatePoints:
    ys, xs = np.nonzero(binary)
    out_x = []
    out_y = []
    out_t = []
    out_v = []
    stride = max(1, int(stride))
    serial = 0
    h, w = binary.shape

    for y, x in zip(ys.tolist(), xs.tolist()):
        if serial % stride:
            serial += 1
            continue
        serial += 1

        sxx = sxy = syy = 0.0
        n = 0
        r = int(orient_radius)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx == 0 and dy == 0:
                    continue
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and binary[yy, xx]:
                    sxx += dx * dx
                    sxy += dx * dy
                    syy += dy * dy
                    n += 1

        theta = 0.0
        valid = False
        if n >= 2:
            den = sxx + syy + 1e-6
            anis = math.sqrt((sxx - syy) ** 2 + 4 * sxy * sxy) / den
            theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
            if theta < 0:
                theta += math.pi
            valid = anis >= anis_threshold

        out_x.append(x)
        out_y.append(y)
        out_t.append(theta)
        out_v.append(valid)

    return TemplatePoints(
        x=np.asarray(out_x, dtype=np.int32),
        y=np.asarray(out_y, dtype=np.int32),
        theta=np.asarray(out_t, dtype=np.float32),
        valid=np.asarray(out_v, dtype=bool),
    )


@dataclass
class Preprocessed:
    rgb: np.ndarray
    binary: np.ndarray
    theta: np.ndarray
    dist: np.ndarray
    near_x: np.ndarray
    near_y: np.ndarray
    near_theta: np.ndarray


def preprocess_rgb(rgb: np.ndarray, cfg: dict) -> Preprocessed:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    if bool(cfg["normalize"]):
        gray = percentile_normalize(gray, float(cfg["low_pct"]), float(cfg["high_pct"]))

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy) * float(cfg["sobel_gain"])
    binary = (mag >= int(cfg["sobel_binary"])).astype(np.uint8)
    theta = np.mod(np.arctan2(gy, gx) + np.pi / 2, np.pi).astype(np.float32)

    if not np.any(binary):
        shape = binary.shape
        return Preprocessed(
            rgb=rgb,
            binary=binary,
            theta=theta,
            dist=np.full(shape, 1e6, dtype=np.float32),
            near_x=np.full(shape, -1, dtype=np.int32),
            near_y=np.full(shape, -1, dtype=np.int32),
            near_theta=np.full(shape, np.nan, dtype=np.float32),
        )

    # OpenCV calculates distance to zero pixels. Edge pixels are therefore zeros.
    src = np.where(binary > 0, 0, 255).astype(np.uint8)
    dist, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL
    )
    labels = labels.astype(np.int32, copy=False)

    edge_y, edge_x = np.nonzero(binary)
    edge_labels = labels[edge_y, edge_x]
    max_label = int(labels.max())

    label_x = np.full(max_label + 1, -1, dtype=np.int32)
    label_y = np.full(max_label + 1, -1, dtype=np.int32)
    label_theta = np.full(max_label + 1, np.nan, dtype=np.float32)

    label_x[edge_labels] = edge_x
    label_y[edge_labels] = edge_y
    label_theta[edge_labels] = theta[edge_y, edge_x]

    near_x = label_x[labels]
    near_y = label_y[labels]
    near_theta = label_theta[labels]

    return Preprocessed(
        rgb=rgb,
        binary=binary,
        theta=theta,
        dist=dist.astype(np.float32, copy=False),
        near_x=near_x,
        near_y=near_y,
        near_theta=near_theta,
    )


@dataclass
class MatchResult:
    cost: float
    x: int
    y: int
    mean_dist: float
    mean_dir_deg: float
    dir_pass_ratio: float
    move_dist: float = 0.0
    total_score: float = 0.0
    tier: str = ""
    identity_score: Optional[float] = None
    visual_fraction: Optional[float] = None

    def accepted(self, cfg: dict) -> bool:
        return (
            self.mean_dist <= float(cfg["max_mean_dist"])
            and self.mean_dir_deg <= float(cfg["max_mean_dir"])
            and self.dir_pass_ratio * 100.0 >= float(cfg["min_dir_pass"])
        )


def candidate_metrics_at(
    pre: Preprocessed,
    points: TemplatePoints,
    x: int,
    y: int,
    cfg: dict,
) -> MatchResult:
    if points.count == 0:
        return MatchResult(1e9, x, y, 999, 999, 0)

    px = x + points.x
    py = y + points.y
    max_d = float(cfg["max_dist"])

    d = pre.dist[py, px]
    nx = pre.near_x[py, px]
    ny = pre.near_y[py, px]
    inside = (nx >= x) & (nx < x + ICON_W) & (ny >= y) & (ny < y + ICON_H)
    dc = np.where(inside, np.minimum(d, max_d), max_d)
    mean_pos = float(np.mean(dc / max_d))

    valid = points.valid
    if np.any(valid):
        nth = pre.near_theta[py[valid], px[valid]]
        diff = angle_diff_pi(points.theta[valid], nth)
        ok = inside[valid] & (dc[valid] < max_d) & np.isfinite(nth)
        dir_cost = np.where(ok, diff / (np.pi / 2), 1.0)
        mean_dir = float(np.mean(dir_cost))
        tol = math.radians(float(cfg["point_dir_tol"]))
        pass_ratio = float(np.mean(ok & (diff <= tol)))
    else:
        mean_dir = 1.0
        pass_ratio = 0.0

    pw = float(cfg["pos_weight"])
    dw = float(cfg["dir_weight"])
    cost = (pw * mean_pos + dw * mean_dir) / max(1e-6, pw + dw)

    return MatchResult(
        cost=cost,
        x=int(x),
        y=int(y),
        mean_dist=mean_pos * max_d,
        mean_dir_deg=mean_dir * 90.0,
        dir_pass_ratio=pass_ratio,
        total_score=cost,
    )


def coarse_best(
    pre: Preprocessed,
    points: TemplatePoints,
    cfg: dict,
    step: int,
    prior_xy: Optional[tuple[float, float]] = None,
    move_weight: float = 0.0,
    move_radius: float = 1.0,
) -> Optional[MatchResult]:
    h, w = pre.binary.shape
    max_x = w - ICON_W
    max_y = h - ICON_H
    if max_x < 0 or max_y < 0 or points.count == 0:
        return None

    step = max(1, int(step))
    xs = np.arange(0, max_x + 1, step, dtype=np.int32)
    ys = np.arange(0, max_y + 1, step, dtype=np.int32)
    X, Y = np.meshgrid(xs, ys)

    pos_sum = np.zeros(X.shape, dtype=np.float32)
    dir_sum = np.zeros(X.shape, dtype=np.float32)
    dir_pass = np.zeros(X.shape, dtype=np.float32)

    max_d = float(cfg["max_dist"])
    tol = math.radians(float(cfg["point_dir_tol"]))
    valid_count = max(1, points.valid_count)

    for i in range(points.count):
        px = X + points.x[i]
        py = Y + points.y[i]

        d = pre.dist[py, px]
        nx = pre.near_x[py, px]
        ny = pre.near_y[py, px]
        inside = (nx >= X) & (nx < X + ICON_W) & (ny >= Y) & (ny < Y + ICON_H)

        dc = np.where(inside, np.minimum(d, max_d), max_d)
        pos_sum += dc / max_d

        if points.valid[i]:
            nth = pre.near_theta[py, px]
            diff = angle_diff_pi(points.theta[i], nth)
            ok = inside & (dc < max_d) & np.isfinite(nth)
            dir_sum += np.where(ok, diff / (np.pi / 2), 1.0)
            dir_pass += (ok & (diff <= tol)).astype(np.float32)

    mean_pos = pos_sum / max(1, points.count)
    mean_dir = dir_sum / valid_count
    pass_ratio = dir_pass / valid_count

    pw = float(cfg["pos_weight"])
    dw = float(cfg["dir_weight"])
    score = (pw * mean_pos + dw * mean_dir) / max(1e-6, pw + dw)

    move_dist_map = np.zeros_like(score, dtype=np.float32)
    if prior_xy is not None and move_weight > 0:
        dx = X.astype(np.float32) - float(prior_xy[0])
        dy = Y.astype(np.float32) - float(prior_xy[1])
        move_dist_map = np.hypot(dx, dy)
        score = score + float(move_weight) * (
            move_dist_map / max(1.0, float(move_radius))
        )

    iy, ix = np.unravel_index(int(np.argmin(score)), score.shape)
    x = int(xs[ix])
    y = int(ys[iy])
    result = MatchResult(
        cost=float(
            (pw * mean_pos[iy, ix] + dw * mean_dir[iy, ix])
            / max(1e-6, pw + dw)
        ),
        x=x,
        y=y,
        mean_dist=float(mean_pos[iy, ix] * max_d),
        mean_dir_deg=float(mean_dir[iy, ix] * 90.0),
        dir_pass_ratio=float(pass_ratio[iy, ix]),
        move_dist=float(move_dist_map[iy, ix]),
        total_score=float(score[iy, ix]),
    )
    return result


def refine_best(
    pre: Preprocessed,
    points: TemplatePoints,
    coarse: MatchResult,
    cfg: dict,
    radius: int,
    prior_xy: Optional[tuple[float, float]] = None,
    move_weight: float = 0.0,
    move_radius: float = 1.0,
) -> MatchResult:
    h, w = pre.binary.shape
    max_x = w - ICON_W
    max_y = h - ICON_H
    rr = max(1, int(radius))
    best = coarse

    for y in range(max(0, coarse.y - rr), min(max_y, coarse.y + rr) + 1):
        for x in range(max(0, coarse.x - rr), min(max_x, coarse.x + rr) + 1):
            r = candidate_metrics_at(pre, points, x, y, cfg)
            if prior_xy is not None and move_weight > 0:
                r.move_dist = math.hypot(x - prior_xy[0], y - prior_xy[1])
                r.total_score = r.cost + float(move_weight) * (
                    r.move_dist / max(1.0, float(move_radius))
                )
            else:
                r.total_score = r.cost
            if r.total_score < best.total_score:
                best = r
    return best


class ScreenCapture:
    def __init__(self):
        self.local = threading.local()
        self.guard = CaptureGuard()

    def grab_rgb(self, box: tuple[int, int, int, int]) -> np.ndarray:
        x0, y0, x1, y1 = map(int, box)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("无效截图区域")
        generation = self.guard.check((x0, y0, x1, y1))
        if mss is not None:
            cap = getattr(self.local, "cap", None)
            if cap is None:
                cap = mss.mss()
                self.local.cap = cap
            shot = cap.grab(
                {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
            )
            bgrx = np.asarray(shot, dtype=np.uint8)
            rgb = cv2.cvtColor(bgrx, cv2.COLOR_BGRA2RGB)
        else:
            im = ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True).convert("RGB")
            rgb = np.asarray(im, dtype=np.uint8)
        self.guard.check((x0, y0, x1, y1), generation)
        return rgb

    def full_screen(self) -> Image.Image:
        generation = self.guard.check()
        shot = ImageGrab.grab(all_screens=True).convert("RGB")
        self.guard.check(generation=generation)
        shot.info["screen_origin"] = (ctypes.windll.user32.GetSystemMetrics(76),
                                      ctypes.windll.user32.GetSystemMetrics(77)) if sys.platform == "win32" else (0, 0)
        return shot


class LossTracker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.identity_points = TemplatePoints(
            np.empty(0, np.int32), np.empty(0, np.int32),
            np.empty(0, np.float32), np.empty(0, bool)
        )
        self.tracking_points = self.identity_points
        self.brightness_mask = np.ones((ICON_H, ICON_W), dtype=bool)
        self.active = False
        self.bbox: Optional[tuple[int, int, int, int]] = None
        self.dx = 0.0
        self.dy = 0.0
        self.frames_since_reacq = 0
        self.reacquire_count = 0
        self.last_tier = "-"
        self.last_match: Optional[MatchResult] = None
        self.last_track_ms = 0.0
        self.reload_assets()

    def _path(self, cfg_key: str, bundled_name: str) -> Path:
        custom = str(self.cfg.get(cfg_key, "") or "").strip()
        if custom and Path(custom).exists():
            return Path(custom)
        return TEMPLATES / bundled_name

    def reload_assets(self):
        self.original_assets = None
        if not any(str(self.cfg.get(key, "") or "").strip() for key in
                   ("identity_template", "tracking_template", "brightness_mask")):
            self.original_assets = OriginalAssets(ROOT)
            self.brightness_mask = self.original_assets.brightness_mask((ICON_W, ICON_H))
            return
        binary_thr = int(self.cfg["sobel_binary"])

        identity_bin = image_to_template_binary(
            self._path("identity_template", "loss_identity_sobel.png"), binary_thr
        )
        tracking_bin = image_to_template_binary(
            self._path("tracking_template", "loss_tracking_sobel.png"), binary_thr
        )

        args = (
            int(self.cfg["orient_radius"]),
            float(self.cfg["anis_threshold"]),
            int(self.cfg["template_stride"]),
        )
        self.identity_points = estimate_template_points(identity_bin, *args)
        self.tracking_points = estimate_template_points(tracking_bin, *args)

        mask_path = self._path("brightness_mask", "loss_brightness_mask.png")
        mask_im = Image.open(mask_path).convert("RGBA").resize(
            (ICON_W, ICON_H), Image.Resampling.NEAREST
        )
        rgba = np.asarray(mask_im, dtype=np.uint8)
        gray = (
            0.299 * rgba[..., 0].astype(np.float32)
            + 0.587 * rgba[..., 1].astype(np.float32)
            + 0.114 * rgba[..., 2].astype(np.float32)
        )
        self.brightness_mask = (rgba[..., 3] > 20) & (gray >= 128)

    def reset(self):
        self.active = False
        self.bbox = None
        self.dx = self.dy = 0.0
        self.frames_since_reacq = 0
        self.last_tier = "-"
        self.last_match = None

    def _crop_rgb(self, pre: Preprocessed, result: MatchResult) -> np.ndarray:
        return pre.rgb[
            result.y: result.y + ICON_H,
            result.x: result.x + ICON_W
        ].copy()

    def _acquire_original(self, capture, roi, tracking=False):
        started = time.perf_counter()
        rgb = capture.grab_rgb(roi)
        width = float(self.cfg.get("loss_icon_width", 48))
        found = self.original_assets.find_loss(rgb, width, float(self.cfg.get("loss_match_threshold", .90)),
                                                float(self.cfg.get("loss_match_margin", .04)))
        self.last_track_ms = (time.perf_counter() - started) * 1000
        if found is None:
            self.active = False
            self.last_match = None
            self.last_tier = "原图：未确认"
            return None, None
        score, candidate = found
        x, y, w, h = candidate.x, candidate.y, candidate.width, candidate.height
        self.bbox = (roi[0] + x, roi[1] + y, roi[0] + x + w, roi[1] + y + h)
        self.active = True
        if tracking:
            self.frames_since_reacq += 1
        else:
            self.frames_since_reacq = 0
            self.reacquire_count += 1
        self.last_tier = "原图追踪" if tracking else "原图定位"
        self.last_match = MatchResult(1-score, x, y, 0, 0, 0,
                                      tier=self.last_tier, identity_score=score, visual_fraction=candidate.visual_fraction)
        padded = np.zeros((h, w, 3), np.uint8)
        sx0, sy0 = max(0, x), max(0, y)
        sx1, sy1 = min(rgb.shape[1], x+w), min(rgb.shape[0], y+h)
        padded[sy0-y:sy1-y, sx0-x:sx1-x] = rgb[sy0:sy1, sx0:sx1]
        crop = cv2.resize(padded, (ICON_W, ICON_H), interpolation=cv2.INTER_AREA)
        return crop, self.last_match

    def global_acquire(
        self, capture: ScreenCapture
    ) -> tuple[Optional[np.ndarray], Optional[MatchResult]]:
        roi = tuple(map(int, self.cfg["loss_roi"]))
        if self.original_assets is not None:
            return self._acquire_original(capture, roi)
        rgb = capture.grab_rgb(roi)
        if rgb.shape[0] < ICON_H or rgb.shape[1] < ICON_W:
            return None, None

        pre = preprocess_rgb(rgb, self.cfg)
        coarse = coarse_best(
            pre, self.identity_points, self.cfg, int(self.cfg["global_step"])
        )
        if coarse is None:
            return None, None

        result = refine_best(
            pre,
            self.identity_points,
            coarse,
            self.cfg,
            radius=max(2, int(self.cfg["global_step"]) + 1),
        )

        self.last_match = result
        if not result.accepted(self.cfg):
            return None, result

        x0, y0, _, _ = roi
        self.bbox = (
            x0 + result.x,
            y0 + result.y,
            x0 + result.x + ICON_W,
            y0 + result.y + ICON_H,
        )
        self.active = True
        self.dx = self.dy = 0.0
        self.frames_since_reacq = 0
        self.reacquire_count += 1
        self.last_tier = "全局"
        return self._crop_rgb(pre, result), result

    def _track_tier(
        self,
        capture: ScreenCapture,
        radius: int,
        step: int,
        tier: str,
    ) -> tuple[Optional[np.ndarray], Optional[MatchResult]]:
        if self.bbox is None:
            return None, None

        old_x = self.bbox[0]
        old_y = self.bbox[1]
        pred_x = old_x + (self.dx if self.cfg["track_predict"] else 0.0)
        pred_y = old_y + (self.dy if self.cfg["track_predict"] else 0.0)

        left = int(round(pred_x - radius))
        top = int(round(pred_y - radius))
        right = left + ICON_W + radius * 2
        bottom = top + ICON_H + radius * 2

        rgb = capture.grab_rgb((left, top, right, bottom))
        if rgb.shape[0] < ICON_H or rgb.shape[1] < ICON_W:
            return None, None

        pre = preprocess_rgb(rgb, self.cfg)

        # Prior position inside this local search image is the old actual box.
        prior_local = (old_x - left, old_y - top)
        coarse = coarse_best(
            pre,
            self.tracking_points,
            self.cfg,
            int(step),
            prior_xy=prior_local,
            move_weight=float(self.cfg["track_move_weight"]),
            move_radius=float(radius),
        )
        if coarse is None:
            return None, None

        result = refine_best(
            pre,
            self.tracking_points,
            coarse,
            self.cfg,
            radius=int(self.cfg["track_refine_r"]),
            prior_xy=prior_local,
            move_weight=float(self.cfg["track_move_weight"]),
            move_radius=float(radius),
        )
        result.tier = tier
        if not result.accepted(self.cfg):
            return None, result

        new_x = left + result.x
        new_y = top + result.y
        self.dx = float(new_x - old_x)
        self.dy = float(new_y - old_y)
        self.bbox = (new_x, new_y, new_x + ICON_W, new_y + ICON_H)
        self.frames_since_reacq += 1
        self.last_tier = tier
        self.last_match = result
        return self._crop_rgb(pre, result), result

    def track(
        self, capture: ScreenCapture
    ) -> tuple[Optional[np.ndarray], Optional[MatchResult]]:
        if self.original_assets is not None:
            if self.bbox is None:
                return self.global_acquire(capture)
            x0, y0, x1, y1 = self.bbox
            radius = int(self.cfg["track_r2"])
            bounds = tuple(map(int, self.cfg["loss_roi"]))
            roi = (max(bounds[0], x0-radius), max(bounds[1], y0-radius),
                   min(bounds[2], x1+radius), min(bounds[3], y1+radius))
            return self._acquire_original(capture, roi, tracking=True)
        t0 = time.perf_counter()
        tiers = [
            (int(self.cfg["track_r1"]), int(self.cfg["track_step1"]), "一级"),
            (int(self.cfg["track_r2"]), int(self.cfg["track_step2"]), "二级"),
            (int(self.cfg["track_r3"]), int(self.cfg["track_step3"]), "三级"),
        ]

        last_result = None
        for radius, step, tier in tiers:
            crop, result = self._track_tier(capture, radius, step, tier)
            if result is not None:
                last_result = result
            if crop is not None:
                self.last_track_ms = (time.perf_counter() - t0) * 1000.0
                return crop, result

        self.last_track_ms = (time.perf_counter() - t0) * 1000.0
        self.active = False
        self.last_tier = "丢失"
        self.last_match = last_result
        return None, last_result

    def brightness_percent(self, crop_rgb: np.ndarray) -> Optional[float]:
        if crop_rgb is None or crop_rgb.shape[:2] != (ICON_H, ICON_W):
            return None

        ox = int(self.cfg["mask_offset_x"])
        oy = int(self.cfg["mask_offset_y"])
        shifted = np.zeros_like(self.brightness_mask)
        src = self.brightness_mask

        x0 = max(0, ox)
        y0 = max(0, oy)
        sx0 = max(0, -ox)
        sy0 = max(0, -oy)
        ww = min(ICON_W - x0, ICON_W - sx0)
        hh = min(ICON_H - y0, ICON_H - sy0)
        if ww <= 0 or hh <= 0:
            return None
        shifted[y0:y0+hh, x0:x0+ww] = src[sy0:sy0+hh, sx0:sx0+ww]

        if not np.any(shifted):
            return None

        rgb = crop_rgb.astype(np.float32)
        lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        return float(np.mean(lum[shifted]) / 255.0 * 100.0)


class RefreshDetector:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.reset()

    def reset(self):
        self.relative = (RelativeRefreshDetector(self.cfg) if not any(self.cfg.get(key) for key in
                         ("identity_template", "tracking_template", "brightness_mask")) else None)
        self.armed = False
        self.candidate = False
        self.cand_count = 0
        self.cand_start_t = None
        self.cand_cross_t = None
        self.baseline = None
        self.raw_history: list[tuple[float, float]] = []
        self.smooth_history: list[tuple[float, float]] = []
        self.stable_history: list[tuple[float, float]] = []
        self.last_reacquire_count = -1
        self.state_text = "观察图案亮度变化" if self.relative is not None else "等待盾牌先变暗"

    @staticmethod
    def _median(values) -> float:
        arr = np.asarray(values, dtype=np.float32)
        return float(np.median(arr))

    def cancel_candidate(self):
        self.candidate = False
        self.cand_count = 0
        self.cand_start_t = None
        self.cand_cross_t = None

    def update(
        self,
        raw_percent: float,
        now: float,
        tracker_frames: int,
        reacquire_count: int,
        visual_fraction: Optional[float] = None,
    ) -> dict:
        if self.relative is not None:
            result = self.relative.update(raw_percent, now, tracker_frames, reacquire_count, visual_fraction)
            self.state_text = result["state"]
            return result
        cfg = self.cfg
        if reacquire_count != self.last_reacquire_count:
            self.last_reacquire_count = reacquire_count
            self.cancel_candidate()

        self.raw_history.append((now, raw_percent))
        n_med = max(1, int(cfg["refresh_median_n"]))
        smooth = self._median([v for _, v in self.raw_history[-n_med:]])
        previous = self.smooth_history[-1] if self.smooth_history else None
        self.smooth_history.append((now, smooth))

        high = float(cfg["refresh_high"])
        low = float(cfg["refresh_low"])
        min_jump = float(cfg["refresh_min_jump"])

        if not self.candidate and smooth < high:
            self.stable_history.append((now, smooth))

        n_base = max(3, int(cfg["refresh_baseline_n"]))
        base_values = [v for _, v in self.stable_history[-n_base:]]
        base_now = self._median(base_values) if base_values else smooth
        if not self.candidate:
            self.baseline = base_now

        baseline = float(self.baseline if self.baseline is not None else base_now)
        jump = smooth - baseline
        blocked = tracker_frames < int(cfg["reacquire_block_frames"])

        result = {
            "raw": raw_percent,
            "smooth": smooth,
            "baseline": baseline,
            "jump": jump,
            "armed": self.armed,
            "candidate": self.candidate,
            "state": self.state_text,
            "refresh": False,
            "fill_time": None,
            "blocked": blocked,
        }

        if not self.armed:
            if smooth <= low and not blocked:
                self.armed = True
                self.state_text = "已准备：等待突然变亮"
            else:
                self.state_text = "重捕获保护" if blocked else "等待盾牌先变暗"
            result.update(armed=self.armed, state=self.state_text)
            return result

        if blocked:
            self.cancel_candidate()
            self.state_text = "重捕获保护"
            result.update(candidate=False, state=self.state_text)
            return result

        qualifies = smooth >= high and jump >= min_jump
        if qualifies:
            if not self.candidate:
                self.candidate = True
                self.cand_count = 1
                self.cand_start_t = now

                if previous is not None:
                    pt, pv = previous
                    if abs(smooth - pv) > 1e-6:
                        a = clamp((high - pv) / (smooth - pv), 0.0, 1.0)
                        self.cand_cross_t = pt + a * (now - pt)
                    else:
                        self.cand_cross_t = now
                else:
                    self.cand_cross_t = now
            else:
                self.cand_count += 1

            hold_ms = (now - float(self.cand_start_t)) * 1000.0
            self.state_text = (
                f"疑似刷新 {self.cand_count}/{int(cfg['refresh_confirm_n'])}"
            )

            if (
                self.cand_count >= int(cfg["refresh_confirm_n"])
                and hold_ms >= float(cfg["refresh_hold_ms"])
            ):
                fill_time = float(
                    self.cand_cross_t
                    if self.cand_cross_t is not None
                    else self.cand_start_t
                )
                self.armed = False
                self.cancel_candidate()
                self.baseline = None
                self.stable_history.clear()
                self.state_text = "刷新确认"
                result.update(
                    refresh=True,
                    fill_time=fill_time,
                    armed=False,
                    candidate=False,
                    state=self.state_text,
                )
                return result
        else:
            if self.candidate:
                self.cancel_candidate()
            self.state_text = "已准备：等待突然变亮"

        result.update(
            armed=self.armed,
            candidate=self.candidate,
            state=self.state_text,
        )
        return result



class DiziDetector:
    """固定位置笛子可用检测：只检查可用模板中的高亮白色像素恢复率。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.roi: Optional[tuple[int, int, int, int]] = None
        self.template_mask: Optional[np.ndarray] = None
        self.is_ready = False
        self.ready_count = 0
        self.off_count = 0
        self.last_alert_time: Optional[float] = None
        self.last_ratio: Optional[float] = None
        self.load()

    @property
    def configured(self) -> bool:
        return (
            self.roi is not None
            and self.template_mask is not None
            and int(np.count_nonzero(self.template_mask)) >= 20
        )

    def load(self):
        self.roi = None
        self.template_mask = None
        self.reset_state()
        calibration = self.cfg.get("dizi_calibration")
        if isinstance(calibration, dict):
            try:
                roi = tuple(map(int, calibration["roi"]))
                mask = np.array(calibration["mask"], dtype=bool)
                if len(roi) == 4 and mask.shape == (roi[3]-roi[1], roi[2]-roi[0]) and mask.sum() >= 20:
                    self.roi, self.template_mask = roi, mask
                    return
            except (ValueError, KeyError, TypeError):
                pass
        if not DIZI_TEMPLATE_PATH.exists():
            return
        try:
            data = np.load(DIZI_TEMPLATE_PATH)
            left = int(data["left"])
            top = int(data["top"])
            width = int(data["width"])
            height = int(data["height"])
            mask = data["template_mask"].astype(bool)
            if width <= 0 or height <= 0 or mask.shape != (height, width):
                return
            if int(np.count_nonzero(mask)) < 20:
                return
            self.roi = (left, top, left + width, top + height)
            self.template_mask = mask
            self.cfg["dizi_calibration"] = {"roi": list(self.roi), "mask": mask.astype(np.uint8).tolist(),
                                            "source": "legacy_screenshot"}
            data.close()
        except Exception:
            self.roi = None
            self.template_mask = None

    def reset_state(self):
        self.visible = False
        self.is_ready = False
        self.ready_count = 0
        self.off_count = 0
        self.last_alert_time = None
        self.last_ratio = None
        self.cooldown_start = None
        self._off_since = None

    def ratio_from_rgb(self, rgb: np.ndarray) -> float:
        if self.template_mask is None:
            return 0.0
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if gray.shape != self.template_mask.shape:
            return 0.0
        current_white = gray >= int(self.cfg.get("dizi_white_threshold", 225))
        denom = int(np.count_nonzero(self.template_mask))
        if denom <= 0:
            return 0.0
        return float(np.count_nonzero(current_white & self.template_mask) / denom)

    def present_in_rgb(self, rgb: np.ndarray) -> bool:
        if self.template_mask is None or rgb.shape[:2] != self.template_mask.shape:
            return False
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        template = self.template_mask.astype(np.float32)
        if float(gray.std()) < 3 or float(template.std()) < .01:
            return False
        score = float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)[0, 0])
        self.presence_score = score
        return math.isfinite(score) and score >= float(self.cfg.get("dizi_presence_threshold", .30))

    def update(self, ratio: float, now: float, present=True) -> dict:
        """返回 alert=True 时需要提醒。持续可用按 repeat_interval 重复。"""
        self.last_ratio = float(ratio)
        self.visible = bool(present)
        if not present:
            # Hidden HUD is unknown, never a cast. Reacquire without inferring what happened while hidden.
            self.is_ready = False
            self.ready_count = self.off_count = 0
            self._off_since = None
            self.last_alert_time = None
            return {"event": None, "visible": False, "ready": False, "ratio": ratio,
                    "repeat_remaining": None, "cooldown_start": self.cooldown_start}
        event = None
        repeat_interval = float(self.cfg.get("dizi_repeat_interval", 25.0))

        if not self.is_ready:
            if ratio >= float(self.cfg.get("dizi_ready_on", 0.80)):
                self.ready_count += 1
            else:
                self.ready_count = 0

            if self.ready_count >= int(self.cfg.get("dizi_ready_frames", 2)):
                self.is_ready = True
                self.ready_count = 0
                self.off_count = 0
                self.last_alert_time = now
                self.cooldown_start = None
                self._off_since = None
                event = "ready"
        else:
            if ratio <= float(self.cfg.get("dizi_ready_off", 0.35)):
                if self.off_count == 0:
                    self._off_since = now
                self.off_count += 1
            else:
                self.off_count = 0
                self._off_since = None

            if self.off_count >= int(self.cfg.get("dizi_off_frames", 2)):
                self.is_ready = False
                self.ready_count = 0
                self.off_count = 0
                self.last_alert_time = None
                self.cooldown_start = self._off_since if self._off_since is not None else now
                self._off_since = None
                event = "off"
            elif self.off_count == 0 and self.last_alert_time is not None and now-self.last_alert_time >= repeat_interval:
                self.last_alert_time = now
                event = "repeat"

        remain = None
        if self.is_ready and self.last_alert_time is not None:
            remain = max(0.0, repeat_interval - (now - self.last_alert_time))

        return {
            "event": event,
            "visible": self.visible,
            "ready": self.is_ready,
            "ratio": float(ratio),
            "repeat_remaining": remain,
            "cooldown_start": self.cooldown_start,
        }


class RegionSelector(tk.Toplevel):
    def __init__(
        self, parent: tk.Tk, screenshot: Image.Image, title: str, callback,
        min_w: int = ICON_W, min_h: int = ICON_H, confirm_on_enter: bool = False
    ):
        super().__init__(parent)
        self.callback = callback
        self.min_w = int(min_w)
        self.min_h = int(min_h)
        self.confirm_on_enter = bool(confirm_on_enter)
        self.start = None
        self.rect = None
        self.selection_box = None
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.title(title)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.source_size = screenshot.size
        self.screen_origin = screenshot.info.get("screen_origin", (0, 0))
        self.display_size = (sw, sh)
        self.image = screenshot.resize((sw, sh), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.image)
        self.canvas = tk.Canvas(
            self, width=sw, height=sh, cursor="cross", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(0, 0, sw, 42, fill="#111111", outline="")
        hint = "鼠标拖动框选，Enter 确认，Esc 取消" if self.confirm_on_enter else "拖动框选，Esc 取消"
        self.canvas.create_text(
            sw // 2, 21,
            text=f"{title}：{hint}",
            fill="white", font=("Microsoft YaHei UI", 15, "bold")
        )
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.bind("<Escape>", lambda _e: self.destroy())
        if self.confirm_on_enter:
            self.bind("<Return>", self.confirm_selection)
            self.bind("<KP_Enter>", self.confirm_selection)
        self.focus_force()

    def on_down(self, event):
        self.start = (event.x, event.y)
        self.selection_box = None
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="#31e687", width=3
        )

    def on_move(self, event):
        if self.start:
            self.canvas.coords(
                self.rect, self.start[0], self.start[1], event.x, event.y
            )

    def on_up(self, event):
        if not self.start:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        self.start = None
        if box[2] - box[0] < self.min_w or box[3] - box[1] < self.min_h:
            self.selection_box = None
            return

        if self.confirm_on_enter:
            # 保留绿色框，允许继续重拖；只有按 Enter 才真正保存。
            self.selection_box = box
        else:
            self.callback(self.screen_box(box))
            self.destroy()

    def confirm_selection(self, _event=None):
        if self.selection_box is None:
            return
        box = self.selection_box
        self.callback(self.screen_box(box))
        self.destroy()

    def screen_box(self, box):
        sx, sy = self.source_size[0]/self.display_size[0], self.source_size[1]/self.display_size[1]
        ox, oy = self.screen_origin
        return [round(box[0]*sx)+ox, round(box[1]*sy)+oy,
                round(box[2]*sx)+ox, round(box[3]*sy)+oy]


PARAM_TABS = {
    "频率 / 原图匹配": [
        ("global_fps", "未锁定时检测频率 Hz", int, 1, 20),
        ("track_hz", "锁定后目标检测频率 Hz", int, 3, 40),
        ("loss_icon_width", "旗框宽度 px", int, 24, 120),
        ("loss_match_threshold", "原图最低匹配分数", float, .70, .99),
        ("loss_match_margin", "不同目标最小分差", float, 0, .3),
        ("refresh_phase_jump", "刷新扇区最小回跳比例", float, .125, .75),
        ("refresh_observation_gap", "短暂掉锁保留基线 秒", float, .2, 5),
    ],
    "笛子检测": [
        ("dizi_hz", "目标检测频率 Hz", int, 3, 40),
        ("dizi_presence_threshold", "图标仍在的最低形状分数", float, .1, .9),
        ("dizi_white_threshold", "可用白色像素阈值", int, 100, 255),
        ("dizi_ready_on", "可用像素比例", float, .1, 1),
        ("dizi_ready_off", "使用后像素比例", float, 0, .9),
        ("dizi_ready_frames", "可用连续确认帧", int, 1, 10),
        ("dizi_off_frames", "使用连续确认帧", int, 2, 10),
    ],
    "旧版 Sobel": [
        ("global_step", "全局粗搜索步长 px", int, 1, 6),
        ("normalize", "百分位拉伸", bool, None, None),
        ("low_pct", "低百分位", float, 0, 50),
        ("high_pct", "高百分位", float, 50, 100),
        ("sobel_gain", "Sobel 增益", float, 0.1, 3),
        ("sobel_binary", "Sobel 二值阈值", int, 1, 254),
        ("max_dist", "最大 Chamfer 距离 px", float, 1, 12),
        ("pos_weight", "位置距离权重", float, 0, 1),
        ("dir_weight", "方向权重", float, 0, 1),
        ("orient_radius", "模板方向邻域", int, 1, 5),
        ("anis_threshold", "方向可靠度阈值", float, 0, 1),
        ("template_stride", "模板点抽样", int, 1, 8),
        ("max_mean_dist", "最大允许平均距离 px", float, 0.05, 4),
        ("max_mean_dir", "最大允许平均方向误差 °", float, 1, 90),
        ("point_dir_tol", "单点方向合格角度 °", float, 1, 90),
        ("min_dir_pass", "最低方向合格率 %", float, 0, 100),
    ],
    "追踪": [
        ("track_r1", "一级搜索 ±px", int, 2, 40),
        ("track_r2", "二级搜索 ±px", int, 8, 80),
        ("track_r3", "三级搜索 ±px", int, 16, 140),
        ("track_step1", "一级步长 px", int, 1, 8),
        ("track_step2", "二级步长 px", int, 1, 10),
        ("track_step3", "三级步长 px", int, 1, 12),
        ("track_refine_r", "最终精搜半径 px", int, 1, 10),
        ("track_move_weight", "位移连续性权重", float, 0, 0.5),
        ("track_predict", "位置预测", bool, None, None),
    ],
    "刷新明度": [
        ("refresh_high", "刷新后至少亮到 %", float, 1, 100),
        ("refresh_low", "旧模板模式：先暗到 % 以下", float, 1, 100),
        ("refresh_min_jump", "至少突然变亮多少个百分点", float, 0, 50),
        ("refresh_median_n", "明度防抖看最近几次", int, 1, 9),
        ("refresh_baseline_n", "刷新前参考取最近几次", int, 3, 20),
        ("refresh_confirm_n", "连续满足几次才算刷新", int, 1, 8),
        ("refresh_hold_ms", "至少持续多久 ms", int, 0, 1000),
        ("reacquire_block_frames", "重捕获后忽略几次", int, 0, 12),
        ("mask_offset_x", "明度遮罩 X 微调 px", int, -15, 15),
        ("mask_offset_y", "明度遮罩 Y 微调 px", int, -15, 15),
    ],
    "时间与提醒": [
        ("loss_duration", "失魂总时长 s", float, 15, 30),
        ("warning_before", "剩余多久提醒 s", float, 0.2, 10),
        ("auto_birth_axis", "初次高亮自动起轴", bool, None, None),
        ("birth_confirm_n", "初次高亮确认次数", int, 1, 8),
        ("audio_enabled", "声音提醒", bool, None, None),
        ("visual_enabled", "画面文字提醒", bool, None, None),
    ],
}


class ParameterDialog(tk.Toplevel):
    def __init__(self, app: "ReminderApp"):
        super().__init__(app.root)
        self.app = app
        self.title("高级参数｜全部保留可调")
        self.geometry("720x650")
        self.transient(app.root)
        self.grab_set()

        self.vars = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        for tab_name, specs in PARAM_TABS.items():
            frame = ttk.Frame(nb, padding=12)
            nb.add(frame, text=tab_name)
            frame.columnconfigure(1, weight=1)
            for row, (key, label, cast, lo, hi) in enumerate(specs):
                ttk.Label(frame, text=label).grid(
                    row=row, column=0, sticky="w", padx=(0, 12), pady=5
                )
                if cast is bool:
                    var = tk.BooleanVar(value=bool(app.cfg.get(key, False)))
                    ttk.Checkbutton(frame, variable=var).grid(
                        row=row, column=1, sticky="w", pady=5
                    )
                else:
                    var = tk.StringVar(value=str(app.cfg.get(key, "")))
                    entry = ttk.Entry(frame, textvariable=var)
                    entry.grid(row=row, column=1, sticky="ew", pady=5)
                    if lo is not None:
                        ttk.Label(frame, text=f"{lo} ～ {hi}", foreground="#777").grid(
                            row=row, column=2, padx=(8, 0), sticky="w"
                        )
                self.vars[key] = (var, cast, lo, hi)

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="恢复本页全部默认值", command=self.defaults).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存并应用", command=self.apply).pack(side="right", padx=8)

    def defaults(self):
        for key, (var, cast, _, _) in self.vars.items():
            value = DEFAULT_CONFIG[key]
            var.set(value)

    def apply(self):
        new_cfg = dict(self.app.cfg)
        try:
            for key, (var, cast, lo, hi) in self.vars.items():
                if cast is bool:
                    value = bool(var.get())
                else:
                    value = cast(var.get())
                    if lo is not None and not (lo <= value <= hi):
                        raise ValueError(f"{key} 超出范围 {lo}～{hi}")
                new_cfg[key] = value

            if new_cfg["refresh_low"] >= new_cfg["refresh_high"]:
                raise ValueError("“刷新前至少暗到”必须低于“刷新后至少亮到”")
            if new_cfg["track_r1"] > new_cfg["track_r2"] or new_cfg["track_r2"] > new_cfg["track_r3"]:
                raise ValueError("三级搜索范围必须满足 一级 ≤ 二级 ≤ 三级")
        except Exception as e:
            messagebox.showerror("参数错误", str(e), parent=self)
            return

        self.app.apply_new_config(new_cfg)
        self.destroy()


class TemplateDialog(tk.Toplevel):
    ITEMS = [
        ("identity_template", "身份 Sobel 模板", "loss_identity_sobel.png"),
        ("tracking_template", "固定 Sobel 追踪模板", "loss_tracking_sobel.png"),
        ("brightness_mask", "盾牌明度遮罩", "loss_brightness_mask.png"),
    ]

    def __init__(self, app: "ReminderApp"):
        super().__init__(app.root)
        self.app = app
        self.title("模板与遮罩")
        self.geometry("900x330")
        self.transient(app.root)
        self.grab_set()

        self.vars = {}
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        for row, (key, label, bundled) in enumerate(self.ITEMS):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=8)
            val = str(app.cfg.get(key, "") or "")
            var = tk.StringVar(value=val)
            self.vars[key] = var
            ttk.Entry(body, textvariable=var).grid(
                row=row, column=1, sticky="ew", padx=8, pady=8
            )
            ttk.Button(
                body, text="浏览…",
                command=lambda k=key: self.browse(k)
            ).grid(row=row, column=2, pady=8)
            ttk.Button(
                body, text="清除自定义",
                command=lambda k=key: self.vars[k].set("")
            ).grid(row=row, column=3, padx=(6, 0), pady=8)
            ttk.Label(
                body, text=f"旧版文件：{bundled}", foreground="#777"
            ).grid(row=row, column=4, padx=(8, 0), sticky="w")

        ttk.Label(
            body,
            text="三项全部留空：使用仓库原图自动定位、追踪并生成明度遮罩。\n旧版 Sobel 模式需同时提供三项自定义文件；普通原图不应填在这里。",
            foreground="#555"
        ).grid(row=4, column=0, columnspan=5, sticky="w", pady=(12, 0))

        buttons = ttk.Frame(self, padding=12)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存并重载", command=self.apply).pack(
            side="right", padx=8
        )

    def browse(self, key):
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 PNG / JPG / SVG 导出的位图",
            filetypes=[
                ("图片", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("所有文件", "*.*"),
            ]
        )
        if path:
            self.vars[key].set(path)

    def apply(self):
        cfg = dict(self.app.cfg)
        paths = [var.get().strip() for var in self.vars.values()]
        if any(paths) and not all(paths):
            messagebox.showerror("模板配置", "使用原图模式请全部留空；旧版模式需提供完整的三个文件。", parent=self)
            return
        for key, var in self.vars.items():
            path = var.get().strip()
            if path and not Path(path).exists():
                messagebox.showerror("文件不存在", path, parent=self)
                return
            cfg[key] = path
        self.app.apply_new_config(cfg)
        self.destroy()


class ReminderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"尘 Buff 提醒器 {APP_VERSION}｜原图识别 + 明度提醒")
        self.root.geometry("790x740")
        self.root.minsize(730, 680)

        self.cfg = load_config()
        self.catalog = CatalogUI(self, ROOT, save_config, RegionSelector)
        self.capture = ScreenCapture()
        # Template files are optional for configuring and testing the reminder UI.
        # Monitoring still validates them before starting its worker.
        self.tracker = None
        self.template_error = ""
        try:
            self.tracker = LossTracker(self.cfg)
        except (OSError, ValueError) as exc:
            self.template_error = str(exc)
        self.refresh_detector = RefreshDetector(self.cfg)
        self.dizi_detector = DiziDetector(self.cfg)

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker: Optional[threading.Thread] = None

        # 全局快捷键独立线程：不依赖 Tk 主线程，不抢焦点、不吞键。
        self.hotkey_stop_event = threading.Event()
        self.hotkey_thread: Optional[threading.Thread] = None
        self.messages: queue.Queue = queue.Queue()
        self.loss_trace = deque(maxlen=1500)
        self.last_loss_seen = None
        self.axis_start: Optional[float] = None
        self.axis_warned = False
        self.birth_count = 0
        self.birth_first_t = None
        self.last_status = {}
        self.overlay = None
        self.overlay_preview = False
        self.overlay_drag = None
        self.overlay_timer = None
        self.overlay_kind = "text"
        self.icon_timer = None
        self._icon_sources = [Image.open(ROOT / "yysls_export/debuff_shihunluopo.png").convert("RGBA"),
                              Image.open(ROOT / "yysls_export/dizi.png").convert("RGBA")]
        self._dizi_setup_shot: Optional[Image.Image] = None

        self._build_ui()
        self._update_summary()
        self.root.after(40, self.poll_messages)
        self.icon_timer = self.root.after(100, self._icon_tick)

        # 5 ms 独立轮询，比 root.after 更不容易漏掉游戏里的短按键。
        self.hotkey_thread = threading.Thread(
            target=self.hotkey_loop, daemon=True, name="BuffReminderHotkey"
        )
        self.hotkey_thread.start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(
            outer,
            text="多技能监控｜Buff 持续时间、奇术冷却与音效提醒",
            font=("Microsoft YaHei UI", 13, "bold")
        )
        title.pack(anchor="w")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 8))
        self.start_btn = ttk.Button(actions, text="开始监测", command=self.toggle)
        self.start_btn.pack(side="left")
        ttk.Button(actions, text="监控列表", command=self.catalog.open).pack(side="left", padx=6)
        ttk.Button(actions, text="框选敌方 Buff 栏", command=self.select_roi).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="手动起轴", command=self.manual_axis).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="重置计时", command=self.reset_axis).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="测试提醒", command=self.test_reminder).pack(
            side="right"
        )

        quick = ttk.LabelFrame(outer, text="常用设置", padding=10)
        quick.pack(fill="x", pady=6)
        quick.columnconfigure(1, weight=1)

        ttk.Label(quick, text="剩余多久提醒").grid(row=0, column=0, sticky="w")
        self.warning_var = tk.DoubleVar(value=float(self.cfg["warning_before"]))
        warning = ttk.Spinbox(
            quick, from_=0.2, to=10.0, increment=0.1,
            textvariable=self.warning_var, width=8, command=self.apply_quick
        )
        warning.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(quick, text="秒").grid(row=0, column=2, sticky="w")

        self.audio_var = tk.BooleanVar(value=bool(self.cfg["audio_enabled"]))
        ttk.Checkbutton(
            quick, text="声音提醒", variable=self.audio_var,
            command=self.apply_quick
        ).grid(row=0, column=3, padx=12)

        self.visual_var = tk.BooleanVar(value=bool(self.cfg["visual_enabled"]))
        ttk.Checkbutton(
            quick, text="画面提醒", variable=self.visual_var,
            command=self.apply_quick
        ).grid(row=0, column=4, padx=12)

        ttk.Button(quick, text="选择自定义 WAV", command=self.choose_audio).grid(
            row=1, column=0, pady=(8, 0), sticky="w"
        )
        ttk.Button(quick, text="清除自定义音频", command=self.clear_audio).grid(
            row=1, column=1, pady=(8, 0), sticky="w", padx=8
        )
        self.audio_text = ttk.Label(quick, text="", foreground="#666")
        self.audio_text.grid(
            row=1, column=2, columnspan=3, sticky="w", pady=(8, 0)
        )

        ttk.Label(quick, text="暂停 / 继续快捷键").grid(
            row=2, column=0, sticky="w", pady=(9, 0)
        )
        self.pause_hotkey_var = tk.StringVar(
            value=str(self.cfg.get("pause_hotkey", "NumLock"))
        )
        self.pause_hotkey_combo = ttk.Combobox(
            quick, textvariable=self.pause_hotkey_var,
            values=HOTKEY_CHOICES, state="readonly", width=12
        )
        self.pause_hotkey_combo.grid(
            row=2, column=1, sticky="w", padx=8, pady=(9, 0)
        )
        self.pause_hotkey_combo.bind(
            "<<ComboboxSelected>>", self.on_pause_hotkey_changed
        )
        ttk.Label(
            quick, text="全局监听：按一次暂停，再按一次继续（不吞键）",
            foreground="#666"
        ).grid(row=2, column=2, columnspan=3, sticky="w", pady=(9, 0))

        ttk.Button(quick, text="浮层位置 / 音效设置", command=self.open_reminder_settings).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(9, 0))
        ttk.Label(quick, text="拖动浮层保存位置；失魂、笛子分别配音",
                  foreground="#666").grid(row=3, column=2, columnspan=3, sticky="w", pady=(9, 0))

        dizi = ttk.LabelFrame(outer, text="笛子提醒", padding=10)
        dizi.pack(fill="x", pady=6)
        dizi.columnconfigure(2, weight=1)

        self.dizi_enabled_var = tk.BooleanVar(
            value=bool(self.cfg.get("dizi_enabled", True))
        )
        ttk.Checkbutton(
            dizi, text="启用笛子提醒", variable=self.dizi_enabled_var,
            command=self.apply_dizi_quick
        ).grid(row=0, column=0, sticky="w")

        ttk.Button(
            dizi, text="设置笛子图标（5秒自动截图）",
            command=self.setup_dizi
        ).grid(row=0, column=1, padx=(12, 16), sticky="w")

        ttk.Label(dizi, text="持续可用时重复提醒").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.dizi_repeat_var = tk.DoubleVar(
            value=float(self.cfg.get("dizi_repeat_interval", 25.0))
        )
        self.dizi_repeat_scale = ttk.Scale(
            dizi, from_=2, to=60, orient="horizontal",
            variable=self.dizi_repeat_var,
            command=self.on_dizi_repeat_slide
        )
        self.dizi_repeat_scale.grid(
            row=1, column=1, columnspan=2, sticky="ew",
            padx=(12, 8), pady=(10, 0)
        )
        self.dizi_repeat_text = ttk.Label(
            dizi, text=f"{self.dizi_repeat_var.get():.0f} 秒",
            width=7
        )
        self.dizi_repeat_text.grid(row=1, column=3, sticky="e", pady=(10, 0))

        self.dizi_setup_text = ttk.Label(dizi, text="", foreground="#666")
        self.dizi_setup_text.grid(
            row=0, column=2, columnspan=2, sticky="w", padx=(8, 0)
        )

        status = ttk.LabelFrame(outer, text="实时状态", padding=10)
        status.pack(fill="x", pady=6)
        status.columnconfigure(1, weight=1)
        status.columnconfigure(3, weight=1)

        self.vars = {}
        labels = [
            ("monitor", "监测", 0, 0),
            ("identity", "失魂定位", 0, 2),
            ("track", "追踪", 1, 0),
            ("brightness", "图案明度", 1, 2),
            ("refresh", "刷新检测", 2, 0),
            ("remaining", "理论剩余", 2, 2),
            ("match", "匹配质量", 3, 0),
            ("cost", "追踪耗时", 3, 2),
            ("dizi", "笛子", 4, 0),
            ("dizi_repeat", "笛子再提醒", 4, 2),
        ]
        for key, label, row, col in labels:
            ttk.Label(status, text=label).grid(
                row=row, column=col, sticky="w", pady=4
            )
            var = tk.StringVar(value="-")
            self.vars[key] = var
            ttk.Label(
                status, textvariable=var,
                font=("Microsoft YaHei UI", 10, "bold")
            ).grid(row=row, column=col+1, sticky="w", padx=(8, 18), pady=4)

        setup = ttk.LabelFrame(outer, text="设置 / 调试", padding=10)
        setup.pack(fill="x", pady=6)
        ttk.Button(setup, text="高级参数（全部可调）", command=self.open_params).pack(
            side="left"
        )
        ttk.Button(setup, text="模板与遮罩", command=self.open_templates).pack(
            side="left", padx=6
        )
        ttk.Button(setup, text="导出参数 JSON", command=self.export_config).pack(
            side="left", padx=6
        )
        ttk.Button(setup, text="导入参数 JSON", command=self.import_config).pack(
            side="left", padx=6
        )
        ttk.Button(setup, text="保存诊断截图", command=self.save_debug).pack(
            side="right"
        )

        self.summary = tk.Text(
            outer, height=10, wrap="word",
            background="#f6f7f9", relief="flat"
        )
        self.summary.pack(fill="both", expand=True, pady=(8, 0))
        self.summary.configure(state="disabled")

    def _update_summary(self):
        roi = self.cfg.get("loss_roi", [])
        audio = self.cfg.get("custom_audio", "")
        lines = [
            f"版本：{APP_VERSION}",
            (f"识别模板未就绪：{self.template_error}（可测试浮层与音效；监测前请补齐模板）"
             if self.template_error else ("识别模板：游戏原图（遮罩自动生成）" if self.tracker.original_assets is not None
                   else "识别模板：旧版自定义 Sobel")),
            f"敌方 Buff 搜索区（已保存）：{roi}",
            f"笛子区域（已保存）：{self.dizi_detector.roi or '尚未设置'}",
            "",
            "当前关键参数：",
            f"• 追踪频率 {self.cfg['track_hz']} Hz",
            f"• 搜索范围 ±{self.cfg['track_r1']} / ±{self.cfg['track_r2']} / ±{self.cfg['track_r3']} px",
            f"• 步长 {self.cfg['track_step1']} / {self.cfg['track_step2']} / {self.cfg['track_step3']} px，精搜半径 {self.cfg['track_refine_r']} px",
            f"• 位移连续性权重 {self.cfg['track_move_weight']}，位置预测 {'开' if self.cfg['track_predict'] else '关'}",
            f"• 原图刷新：相对近期亮度跃升 ≥{self.cfg['refresh_min_jump']:.1f} 个百分点，连续确认后重置时间轴",
            f"• 刷新确认：连续 {self.cfg['refresh_confirm_n']} 次 + 至少 {self.cfg['refresh_hold_ms']} ms",
            f"• 提醒：21秒轴剩余 {self.cfg['warning_before']:.1f}s 时提示",
            f"• 自定义音频：{audio if audio else '内置提示音'}",
            f"• 暂停 / 继续快捷键：{self.cfg.get('pause_hotkey', 'NumLock')}（全局）",
            f"• 笛子：{'启用' if self.cfg.get('dizi_enabled', True) else '关闭'}｜"
            f"模板{'已设置' if self.dizi_detector.configured else '未设置'}｜"
            f"持续可用每 {float(self.cfg.get('dizi_repeat_interval',25.0)):.0f}s 重复提醒",
        ]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")
        self.audio_text.configure(
            text=Path(audio).name if audio else "内置提示音（可切换电子 / 百度语音）"
        )
        if hasattr(self, "dizi_setup_text"):
            self.dizi_setup_text.configure(
                text=("✓ 已设置笛子模板" if self.dizi_detector.configured
                      else "尚未设置笛子模板")
            )

    def apply_quick(self):
        try:
            self.cfg["warning_before"] = clamp(float(self.warning_var.get()), 0.2, 10.0)
        except Exception:
            pass  # Sound/visual switches must work even while the numeric entry is incomplete.
        self.cfg["audio_enabled"] = bool(self.audio_var.get())
        if not self.cfg["audio_enabled"]:
            self._stop_audio()
        self.cfg["visual_enabled"] = bool(self.visual_var.get())
        save_config(self.cfg)
        self._update_summary()

    def on_pause_hotkey_changed(self, event=None):
        key = str(self.pause_hotkey_var.get() or "NumLock")
        if key not in HOTKEY_VK:
            key = "NumLock"
            self.pause_hotkey_var.set(key)
        self.cfg["pause_hotkey"] = key
        save_config(self.cfg)
        self._update_summary()

    def hotkey_loop(self):
        """
        独立后台线程监听暂停/继续键。

        GetAsyncKeyState 不注册热键、不安装拦截钩子，因此按键仍会正常
        传给游戏。高位检查“此刻是否按下”，低位补偿“两次轮询之间曾按过”
        的极短按键；再加 250ms 防抖，避免一次按键切换两次。
        """
        if sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
        except Exception:
            return

        last_vk = None
        was_down = False
        debounce_until = 0.0

        while not self.hotkey_stop_event.is_set():
            try:
                key = str(self.cfg.get("pause_hotkey", "NumLock"))
                vk = HOTKEY_VK.get(key, HOTKEY_VK["NumLock"])

                state = int(user32.GetAsyncKeyState(vk)) & 0xFFFF
                down = bool(state & 0x8000)
                pressed_since_last_call = bool(state & 0x0001)
                now = time.monotonic()

                if vk != last_vk:
                    # 换键后先同步当前物理状态，避免切换选项时误触发。
                    last_vk = vk
                    was_down = down
                    debounce_until = now + 0.15
                else:
                    rising_edge = down and not was_down
                    if (rising_edge or pressed_since_last_call) and now >= debounce_until:
                        # Tk 只能由主线程操作，所以只投递消息。
                        self.messages.put({"type": "hotkey_toggle", "key": key})
                        debounce_until = now + 0.25
                    was_down = down

            except Exception:
                pass

            # 5 ms 足够轻，同时远低于普通短按键持续时间。
            self.hotkey_stop_event.wait(0.005)

    def toggle_pause(self):
        if not (self.worker and self.worker.is_alive()):
            return

        key = str(self.cfg.get("pause_hotkey", "NumLock"))
        if not self.pause_event.is_set():
            # Pause all detection and all reminder delivery.
            self.pause_event.set()
            self.catalog.engine.reset()
            self.axis_start = None
            self.axis_warned = False
            self.birth_count = 0
            self.birth_first_t = None
            self.dizi_detector.reset_state()
            self.vars["monitor"].set(f"已暂停｜{key} 继续")
            self.vars["dizi"].set("已暂停")
            self.vars["dizi_repeat"].set("-")
            try:
                if winsound is not None:
                    winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
            if self.overlay is not None:
                self._hide_overlay(self.overlay)
        else:
            # Resume from a clean transient state so an out-of-combat pause
            # cannot leave a stale loss target / timer behind.
            try:
                self.tracker.reset()
                self.refresh_detector.reset()
                self.dizi_detector.reset_state()
            except Exception:
                pass
            self.axis_start = None
            self.axis_warned = False
            self.birth_count = 0
            self.birth_first_t = None
            self.catalog.engine.reset()
            self.pause_event.clear()
            self.vars["monitor"].set("运行中")
            self.vars["identity"].set("正在全局搜索")
            self.vars["refresh"].set("观察图案亮度变化")

    def apply_dizi_quick(self):
        self.cfg["dizi_enabled"] = bool(self.dizi_enabled_var.get())
        try:
            self.cfg["dizi_repeat_interval"] = clamp(
                float(self.dizi_repeat_var.get()), 2.0, 60.0
            )
        except Exception:
            self.cfg["dizi_repeat_interval"] = 25.0
        self.dizi_detector.cfg = self.cfg
        save_config(self.cfg)
        self._update_summary()

    def on_dizi_repeat_slide(self, value=None):
        try:
            v = clamp(float(self.dizi_repeat_var.get()), 2.0, 60.0)
        except Exception:
            v = 25.0
        self.dizi_repeat_text.configure(text=f"{v:.0f} 秒")
        # 滑动时只更新内存；松手/开始监测时会持久化。
        self.cfg["dizi_repeat_interval"] = v
        self.dizi_detector.cfg = self.cfg

    def setup_dizi(self):
        if self.worker and self.worker.is_alive():
            self.stop_monitor()
        messagebox.showinfo(
            "设置笛子",
            "请先让笛子处于【可用、白色亮起】状态。\\n\\n"
            "点“确定”后程序会缩到后台，5 秒后自动截屏。\\n"
            "期间不需要按任何键。截屏后会出现冻结画面。\n"
            "鼠标拖动框选笛子，Enter 确认，Esc 取消。",
            parent=self.root
        )
        self.root.iconify()
        self.root.after(5000, self._capture_dizi_setup)

    def _capture_dizi_setup(self):
        try:
            shot = self.capture.full_screen()
            self._dizi_setup_shot = shot
            self.root.deiconify()
            self.root.lift()
            RegionSelector(
                self.root,
                shot,
                "框选【可用状态】的笛子图标",
                self._set_dizi_roi,
                min_w=6,
                min_h=6,
                confirm_on_enter=True,
            )
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("笛子截屏失败", str(e), parent=self.root)

    def _set_dizi_roi(self, box):
        shot = self._dizi_setup_shot
        self._dizi_setup_shot = None
        if shot is None:
            messagebox.showerror("设置失败", "冻结截图已经丢失，请重新设置。", parent=self.root)
            return

        x0, y0, x1, y1 = map(int, box)
        if x1 <= x0 or y1 <= y0:
            return

        # Selector returns physical desktop coordinates, including multi-monitor origin.
        ox, oy = shot.info.get("screen_origin", (0, 0))
        rx0, ry0, rx1, ry1 = x0-ox, y0-oy, x1-ox, y1-oy

        crop = np.asarray(shot.crop((rx0, ry0, rx1, ry1)).convert("RGB"), dtype=np.uint8)
        threshold = int(self.cfg.get("dizi_white_threshold", 225))
        try:
            mask = OriginalAssets(ROOT).calibrate_dizi(crop, threshold)
        except (OSError, ValueError) as exc:
            messagebox.showerror("笛子校准失败", str(exc), parent=self.root)
            return
        white_pixels = int(np.count_nonzero(mask))
        if white_pixels < 20:
            messagebox.showerror(
                "模板白像素太少",
                f"只找到 {white_pixels} 个灰度 ≥ {threshold} 的像素。\\n"
                "请确认截屏时笛子处于可用白色状态，并重新框选。",
                parent=self.root
            )
            return

        self.cfg["dizi_calibration"] = {"roi": [x0, y0, x1, y1],
                                        "mask": mask.astype(np.uint8).tolist(),
                                        "source": "yysls_export/dizi.png"}
        save_config(self.cfg)
        self.dizi_detector = DiziDetector(self.cfg)
        self._update_summary()
        messagebox.showinfo(
            "笛子设置完成",
            f"模板白像素：{white_pixels}\\n"
            f"识别阈值：灰度 ≥ {threshold}\\n"
            "之后直接开始监测即可。",
            parent=self.root
        )

    def _reminder_text(self, kind, seconds):
        return format_reminder(self.cfg.get(kind+"_text", "{%s} {%d}"), seconds,
                               self.cfg.get(kind+"_skill_name", "笛子" if kind == "dizi" else "失魂落魄"))

    def open_reminder_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("浮层文字、图标与音效")
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        values = {}
        mode = tk.StringVar(value="图标" if self.cfg.get("overlay_mode") == "icon" else "文字")
        numbers = tk.BooleanVar(value=self.cfg.get("icon_numbers", True))
        allow_capture = tk.BooleanVar(value=self.cfg.get("overlay_allow_capture", False))
        cooldown = tk.StringVar(value=str(self.cfg.get("dizi_cooldown_seconds", 0)))

        def save():
            try:
                duration = float(cooldown.get())
                if not math.isfinite(duration) or not 0 <= duration <= 3600:
                    raise ValueError()

            except ValueError:
                messagebox.showerror("冷却秒数", "请输入实际冷却秒数（0～3600）；0 表示未知，不显示推测倒数。", parent=dialog)
                return False
            self.cfg.update({key: var.get() for key, var in values.items()})
            self.cfg["overlay_mode"] = "icon" if mode.get() == "图标" else "text"
            self.cfg["icon_numbers"] = numbers.get()
            self.cfg["overlay_allow_capture"] = allow_capture.get()
            self.cfg["dizi_cooldown_seconds"] = duration
            self.cfg["dizi_icon_timer"] = "cooldown"
            save_config(self.cfg)
            return True
        def preview():
            if save():
                if self.overlay is not None:
                    self._hide_overlay(self.overlay)
                self.show_visual_reminder(self._reminder_text("loss", 12), preview=True)
        styles = ttk.Frame(body)
        styles.pack(fill="x", pady=5)
        ttk.Label(styles, text="浮层模式").pack(side="left")
        ttk.Combobox(styles, textvariable=mode, values=("文字", "图标"), state="readonly", width=7).pack(side="left", padx=8)
        ttk.Checkbutton(styles, text="图标显示倒数", variable=numbers).pack(side="left")
        def color():
            _, selected = colorchooser.askcolor(self.cfg.get("icon_color", "#b95739"), parent=dialog)
            if selected:
                self.cfg["icon_color"] = selected
                save_config(self.cfg)
        ttk.Button(styles, text="选择底色", command=color).pack(side="left", padx=8)
        ttk.Checkbutton(body, text="允许截图/录屏捕获浮层", variable=allow_capture).pack(anchor="w", pady=5)
        ttk.Label(body, text="保存或预览后生效。录屏请选择显示器/桌面捕获；请将浮层移出游戏图标检测区域。",
                  wraplength=510).pack(anchor="w")
        timing = ttk.Frame(body)
        timing.pack(fill="x", pady=5)
        ttk.Label(timing, text="笛子使用后冷却秒数（0 = 未知）").pack(side="left")
        ttk.Entry(timing, textvariable=cooldown, width=8).pack(side="left", padx=8)
        ttk.Label(body, text="{%d} = 剩余秒数（向下取整），{%s} = 技能名。图标从 12 点顺时针变灰。").pack(anchor="w", pady=5)
        sound_names = {"baidu": "百度语音", "electronic": "电子合成音"}
        for kind, title, key in (("loss", "失魂提醒", "custom_audio"), ("dizi", "笛子提醒", "dizi_custom_audio")):
            frame = ttk.LabelFrame(body, text=title, padding=10)
            frame.pack(fill="x", pady=6)
            for field, label in (("skill_name", "技能名"), ("text", "浮层文字")):
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=3)
                ttk.Label(row, text=label, width=9).pack(side="left")
                var = tk.StringVar(value=self.cfg.get(kind+"_"+field, ""))
                values[kind+"_"+field] = var
                ttk.Entry(row, textvariable=var, width=40).pack(side="left", fill="x", expand=True)
            audio_enabled = tk.BooleanVar(value=self.cfg.get(kind+"_audio_enabled", True))
            ttk.Checkbutton(frame, text="启用此提醒的语音 / 音效（立即生效）", variable=audio_enabled,
                            command=lambda k=kind, v=audio_enabled: self._set_reminder_audio(k, v.get())).pack(anchor="w", pady=4)
            name = tk.StringVar(value=self.cfg.get(key) or "使用内置声音")
            ttk.Label(frame, textvariable=name, wraplength=470).pack(anchor="w")
            buttons = ttk.Frame(frame)
            buttons.pack(anchor="w", pady=(8, 0))
            preset = tk.StringVar(value=sound_names.get(self.cfg.get(kind+"_sound", "baidu"), "百度语音"))
            combo = ttk.Combobox(buttons, textvariable=preset, values=tuple(sound_names.values()), state="readonly", width=11)
            combo.pack(side="left")
            def pick(_event=None, k=key, group=kind, var=preset, display=name):
                self.cfg[group+"_sound"] = next(code for code, label in sound_names.items() if label == var.get())
                self.cfg[k] = ""
                display.set("使用内置声音")
                save_config(self.cfg)
            combo.bind("<<ComboboxSelected>>", pick)
            def choose(k=key, v=name):
                self.choose_audio(k)
                v.set(self.cfg.get(k) or "使用内置声音")
            ttk.Button(buttons, text="自定义 WAV", command=choose).pack(side="left", padx=5)
            ttk.Button(buttons, text="试听", command=lambda k=key: self._play_audio(k, preview=True)).pack(side="left")
            def clear(k=key, v=name):
                self.clear_audio(k)
                v.set("使用内置声音")
            ttk.Button(buttons, text="恢复内置", command=clear).pack(side="left", padx=5)
        ttk.Label(body, text=f"选区与设置自动保存在：{CONFIG_PATH}", wraplength=510).pack(anchor="w", pady=8)
        controls = ttk.Frame(body)
        controls.pack(fill="x")
        ttk.Button(controls, text="预览并拖动", command=preview).pack(side="left")
        ttk.Button(controls, text="恢复默认位置", command=self.reset_overlay_position).pack(side="left", padx=8)
        def close():
            if not save():
                return
            if self.overlay is not None:
                self._hide_overlay(self.overlay)
            dialog.destroy()
        ttk.Button(controls, text="保存并关闭", command=close).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close)

    def _ensure_icon_overlay(self, preview=False):
        if self.overlay is not None and self.overlay_kind == "icon":
            if not preview and self.worker is not None and self.worker.is_alive():
                self.overlay_preview = False
            return
        if self.overlay is not None:
            self._hide_overlay(self.overlay)
        win = tk.Toplevel(self.root)
        win.withdraw()
        self.overlay = win
        self.overlay_kind = "icon"
        self.overlay_preview = preview
        self.overlay_drag = None
        self._icon_preview_started = time.monotonic()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        transparent_key = "#010203"
        background = transparent_key
        if sys.platform == "win32":
            win.attributes("-transparentcolor", transparent_key)
        elif sys.platform == "darwin":
            win.attributes("-transparent", True)
            background = "systemTransparent"
        win.configure(bg=background)
        self.icon_label = tk.Label(win, bg=background, cursor="fleur", borderwidth=0, highlightthickness=0)
        self.icon_label.pack()
        self.icon_label.bind("<ButtonPress-1>", lambda event: self._start_overlay_drag(event, win))
        self.icon_label.bind("<B1-Motion>", lambda event: self._move_overlay_drag(event, win))
        self.icon_label.bind("<ButtonRelease-1>", lambda event: self._end_overlay_drag(event, win))
        self._render_icons()
        win.update_idletasks()
        position = self.cfg.get("overlay_position")
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            position = ((win.winfo_screenwidth()-win.winfo_reqwidth())//2, win.winfo_screenheight()//5)
        self._position_overlay(win, *position)
        self._protect_overlay(win)
        win.deiconify()

    def _render_icons(self):
        now = time.monotonic()
        loss_duration = float(self.cfg.get("loss_duration", 21))
        remaining = None if self.axis_start is None else max(0, loss_duration-(now-self.axis_start))
        dizi_remaining, dizi_duration, dizi_ready = dizi_icon_state(
            self.cfg, self.dizi_detector.is_ready, self.dizi_detector.last_alert_time,
            self.dizi_detector.cooldown_start, now)
        if self.overlay_preview:
            remaining = loss_duration-((now-self._icon_preview_started) % loss_duration)
            dizi_duration = max(0., float(self.cfg.get("dizi_cooldown_seconds", 0)))
            dizi_remaining = (dizi_duration-((now-self._icon_preview_started) % dizi_duration)
                              if dizi_duration > 0 else None)
            dizi_ready = dizi_duration <= 0
        items = []
        if self.cfg.get("loss_enabled", True):
            items.append((self._icon_sources[0], remaining, loss_duration, False,
                          self.overlay_preview or self._loss_icon_visible(now)))
        if self.cfg.get("dizi_enabled", True):
            items.append((self._icon_sources[1], dizi_remaining, dizi_duration, dizi_ready,
                          self.overlay_preview or self.dizi_detector.visible))
        items.extend(self.catalog.icon_items(now, self.overlay_preview, self._icon_preview_started))
        strip = Image.new("RGBA", (max(1, len(items)*96-8), 88), (0, 0, 0, 0))
        for index, (source, value, duration, ready, visible) in enumerate(items):
            if not visible:
                continue
            unknown = not self.overlay_preview and not ready and (value is None or value <= 0)
            icon = cooldown_icon(source, self.cfg.get("icon_color", "#b95739"),
                                 value, duration, self.cfg.get("icon_numbers", True), ready=ready, unknown=unknown)
            strip.paste(icon, (index*96, 0))
        self._icon_photo = ImageTk.PhotoImage(strip)
        self.icon_label.configure(image=self._icon_photo)

    def _icon_tick(self):
        live = self.worker is not None and self.worker.is_alive() and not self.pause_event.is_set()
        if self.cfg.get("overlay_mode") == "icon" and self.cfg.get("visual_enabled", True) and live:
            self._ensure_icon_overlay()
        if self.overlay is not None and self.overlay_kind == "icon":
            if self.overlay_preview or (live and self.cfg.get("overlay_mode") == "icon" and self.cfg.get("visual_enabled", True)):
                self._render_icons()
            else:
                self._hide_overlay(self.overlay)
        self.icon_timer = self.root.after(100, self._icon_tick)

    def reset_overlay_position(self):
        self.cfg["overlay_position"] = None
        save_config(self.cfg)
        self.show_visual_reminder("拖动调整位置", preview=True)

    def choose_audio(self, key="custom_audio"):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择短 WAV 提醒音",
            filetypes=[("WAV 音频", "*.wav"), ("所有文件", "*.*")]
        )
        if not path:
            return
        try:
            with wave.open(path, "rb") as audio:
                if audio.getnframes() == 0:
                    raise ValueError("音频为空")
            AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            dest = AUDIO_DIR / ("dizi_reminder.wav" if key == "dizi_custom_audio" else "loss_reminder.wav")
            if Path(path).resolve() != dest.resolve():
                shutil.copy2(path, dest)
            self.cfg[key] = str(dest)
            save_config(self.cfg)
            self._update_summary()
        except Exception as e:
            messagebox.showerror("音频加载失败", f"请选择有效的 PCM WAV 文件。\n{e}", parent=self.root)

    def clear_audio(self, key="custom_audio"):
        self.cfg[key] = ""
        save_config(self.cfg)
        self._update_summary()

    def select_roi(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "请先停止监测再重新框选。", parent=self.root)
            return
        shot = self.capture.full_screen()
        RegionSelector(
            self.root, shot, "框选敌方 Buff 整行", self._set_roi
        )

    def _set_roi(self, box):
        self.cfg["loss_roi"] = list(map(int, box))
        save_config(self.cfg)
        self._update_summary()

    def open_params(self):
        ParameterDialog(self)

    def open_templates(self):
        TemplateDialog(self)

    def apply_new_config(self, cfg: dict):
        was_running = self.worker is not None and self.worker.is_alive()
        if was_running:
            self.stop_monitor()
        self.cfg = cfg
        self.catalog.apply_defaults()
        save_config(self.cfg)
        try:
            self.tracker = LossTracker(self.cfg)
            self.refresh_detector = RefreshDetector(self.cfg)
            self.dizi_detector = DiziDetector(self.cfg)
        except Exception as e:
            self.template_error = str(e)
            self._update_summary()
            messagebox.showerror("模板 / 参数加载失败", str(e), parent=self.root)
            return
        self.template_error = ""
        self.warning_var.set(float(self.cfg["warning_before"]))
        self.audio_var.set(bool(self.cfg["audio_enabled"]))
        self.visual_var.set(bool(self.cfg["visual_enabled"]))
        if hasattr(self, "pause_hotkey_var"):
            key = str(self.cfg.get("pause_hotkey", "NumLock"))
            if key not in HOTKEY_VK:
                key = "NumLock"
            self.pause_hotkey_var.set(key)
        if hasattr(self, "dizi_enabled_var"):
            self.dizi_enabled_var.set(bool(self.cfg.get("dizi_enabled", True)))
            self.dizi_repeat_var.set(float(self.cfg.get("dizi_repeat_interval", 25.0)))
            self.dizi_repeat_text.configure(
                text=f"{float(self.cfg.get('dizi_repeat_interval',25.0)):.0f} 秒"
            )
        self._update_summary()
        if was_running:
            self.start_monitor()

    def export_config(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出参数",
            defaultextension=".json",
            initialfile="尘Buff提醒器_参数.json",
            filetypes=[("JSON", "*.json")]
        )
        if path:
            Path(path).write_text(
                json.dumps(self.cfg, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    def import_config(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入参数",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")]
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(data)
            self.apply_new_config(cfg)
        except Exception as e:
            messagebox.showerror("导入失败", str(e), parent=self.root)

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.stop_monitor()
        else:
            self.start_monitor()

    def start_monitor(self):
        self.apply_quick()
        self.apply_dizi_quick()
        try:
            self.catalog.validate_start()
        except ValueError as exc:
            messagebox.showerror("监控列表", str(exc), parent=self.root)
            return
        if self.cfg["refresh_low"] >= self.cfg["refresh_high"]:
            messagebox.showerror(
                "参数错误",
                "刷新前低阈值必须低于刷新后的高阈值。",
                parent=self.root
            )
            return

        roi = self.cfg.get("loss_roi", [])
        if self.cfg.get("loss_enabled", True) and (len(roi) != 4 or roi[2] - roi[0] < ICON_W or roi[3] - roi[1] < ICON_H):
            messagebox.showerror(
                "搜索区无效", "请先框选敌方 Buff 整行。", parent=self.root
            )
            return

        try:
            self.tracker = LossTracker(self.cfg)
            self.refresh_detector = RefreshDetector(self.cfg)
        except Exception as e:
            messagebox.showerror("初始化失败", f"请确认下载了完整仓库；使用原图模式时「模板与遮罩」三项应全部留空。\n{e}", parent=self.root)
            return
        self.template_error = ""
        self._update_summary()

        self.stop_event.clear()
        self.pause_event.clear()
        self.axis_start = None
        self.last_loss_seen = None
        self.axis_warned = False
        self.birth_count = 0
        self.birth_first_t = None
        self.dizi_detector = DiziDetector(self.cfg)
        self.dizi_detector.reset_state()
        self.monitor_started = time.monotonic()
        self.worker = threading.Thread(target=self.monitor_loop, daemon=True)
        self.worker.start()
        self.start_btn.configure(text="停止监测")
        self.vars["monitor"].set("运行中")
        if not self.dizi_detector.configured:
            self.vars["dizi"].set("未设置")
            self.vars["dizi_repeat"].set("-")

    def stop_monitor(self):
        self.stop_event.set()
        self.pause_event.clear()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=1.0)
        self.worker = None
        self.start_btn.configure(text="开始监测")
        self.vars["monitor"].set("已停止")
        self.catalog.engine.reset()
        if self.overlay is not None and not self.overlay_preview:
            self._hide_overlay(self.overlay)

    def manual_axis(self):
        self.axis_start = time.monotonic()
        self.axis_warned = False
        self.messages.put({
            "type": "event",
            "text": "已手动起轴：从现在开始 21 秒",
        })

    def reset_axis(self):
        self.axis_start = None
        self.last_loss_seen = None
        self.axis_warned = False
        self.birth_count = 0
        self.birth_first_t = None
        self.messages.put({"type": "event", "text": "理论时间轴已清空"})

    def _play_catalog_audio(self):
        if winsound is not None:
            try:
                winsound.PlaySound(str(ROOT / "assets/audio/loss_electronic.wav"),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass

    def _reminder_audio(self):
        self._play_audio("custom_audio")

    def _dizi_audio(self):
        self._play_audio("dizi_custom_audio")

    def _stop_audio(self):
        if winsound is not None:
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass

    def _set_reminder_audio(self, kind, enabled):
        self.cfg[kind+"_audio_enabled"] = bool(enabled)
        if not enabled:
            self._stop_audio()
        save_config(self.cfg)

    def _play_audio(self, key, preview=False):
        kind = "dizi" if key == "dizi_custom_audio" else "loss"
        if not preview and (not self.cfg.get("audio_enabled", True)
                            or not self.cfg.get(kind+"_audio_enabled", True)):
            return
        if winsound is None:
            if preview:
                messagebox.showinfo("音效试听", "当前音效播放需要 Windows 系统。", parent=self.root)
            return
        custom = str(self.cfg.get(key, "") or "")
        if not custom:
            kind = "dizi" if key == "dizi_custom_audio" else "loss"
            preset = self.cfg.get(kind+"_sound", "baidu")
            if preset in ("baidu", "electronic"):
                custom = str(ROOT / "assets/audio" / f"{kind}_{preset}.wav")
        if custom:
            try:
                if not Path(custom).is_file():
                    raise FileNotFoundError(custom)
                winsound.PlaySound(custom, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return
            except Exception as e:
                if preview:
                    messagebox.showwarning("音效不可用", f"已改用默认蜂鸣。\n{e}", parent=self.root)
        def beep():
            try:
                if key == "dizi_custom_audio":
                    winsound.Beep(1200, 90)
                    time.sleep(0.05)
                    winsound.Beep(1550, 130)
                else:
                    winsound.Beep(880, 320)
            except Exception:
                pass
        threading.Thread(target=beep, daemon=True).start()

    def _cancel_overlay_timer(self):
        if self.overlay_timer is not None:
            self.root.after_cancel(self.overlay_timer)
            self.overlay_timer = None

    def show_visual_reminder(self, text: str, preview=False):
        if self.cfg.get("overlay_mode") == "icon":
            if preview or self.cfg.get("visual_enabled", True):
                self._ensure_icon_overlay(preview)
            return
        if not preview and not self.cfg.get("visual_enabled", True):
            return
        # Keep the adjustment preview stable while live reminders arrive.
        if self.overlay is not None and (self.overlay_drag or (self.overlay_preview and not preview)):
            return
        if self.overlay is not None:
            self._hide_overlay(self.overlay)

        win = tk.Toplevel(self.root)
        win.withdraw()
        self.overlay = win
        self.overlay_kind = "text"
        self.overlay_preview = preview
        self.overlay_drag = None
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.92)
        except Exception:
            pass

        label = tk.Label(
            win, text=text, cursor="fleur",
            font=("Microsoft YaHei UI", 28, "bold"),
            bg="#111820", fg="#ffffff", padx=28, pady=16)
        label.pack()
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        x, y = (sw - ww) // 2, max(50, sh // 5)
        position = self.cfg.get("overlay_position")
        if (isinstance(position, (list, tuple)) and len(position) == 2
                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in position)):
            x, y = map(int, position)
        self._position_overlay(win, x, y)
        self._protect_overlay(win)
        win.deiconify()
        label.bind("<ButtonPress-1>", lambda event: self._start_overlay_drag(event, win))
        label.bind("<B1-Motion>", lambda event: self._move_overlay_drag(event, win))
        label.bind("<ButtonRelease-1>", lambda event: self._end_overlay_drag(event, win))
        if not preview:
            self.overlay_timer = self.root.after(900, lambda: self._hide_overlay(win))

    def _protect_overlay(self, win):
        key = str(win)
        allow_capture = self.cfg.get("overlay_allow_capture", False)
        excluded = not allow_capture and exclude_window_from_capture(win.winfo_id())
        def refresh(event=None):
            nonlocal excluded
            if event is not None and event.widget is not win:
                return
            if event is not None and event.type == tk.EventType.Map:
                # Reapply after mapping: Tk may recreate its native wrapper.
                excluded = not allow_capture and exclude_window_from_capture(win.winfo_id())
            if event is None:
                bounds = win._capture_bounds
            else:
                x, y = win.winfo_rootx(), win.winfo_rooty()
                bounds = (x, y, x+max(win.winfo_width(), win.winfo_reqwidth()),
                          y+max(win.winfo_height(), win.winfo_reqheight()))
            self.capture.guard.set_window(key, bounds, excluded)
        def destroyed(event):
            if event.widget is win:
                self.capture.guard.remove(key)
        win.bind("<Configure>", refresh, add="+")
        win.bind("<Map>", refresh, add="+")
        win.bind("<Destroy>", destroyed, add="+")
        win.update_idletasks()
        refresh()

    def _position_overlay(self, win, x, y):
        # Use virtual-desktop coordinates on Windows, including monitors left of primary.
        left, top = 0, 0
        width, height = win.winfo_screenwidth(), win.winfo_screenheight()
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            left, top, width, height = [user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        x = int(clamp(x, left, max(left, left + width - win.winfo_reqwidth())))
        y = int(clamp(y, top, max(top, top + height - win.winfo_reqheight())))
        # A leading '+' makes even negative coordinates absolute in Tk geometry.
        win._capture_bounds = (x, y, x+win.winfo_reqwidth(), y+win.winfo_reqheight())
        if hasattr(self, "capture"):
            self.capture.guard.moving(str(win), win._capture_bounds)
        win.geometry(f"+{x}+{y}")

    def _start_overlay_drag(self, event, win):
        if self.overlay is not win:
            return
        self._cancel_overlay_timer()
        self.overlay_drag = (event.x_root - win.winfo_x(), event.y_root - win.winfo_y())

    def _move_overlay_drag(self, event, win):
        if self.overlay is win and self.overlay_drag is not None:
            dx, dy = self.overlay_drag
            self._position_overlay(win, event.x_root - dx, event.y_root - dy)

    def _end_overlay_drag(self, event, win):
        if self.overlay is not win or self.overlay_drag is None:
            return
        self._move_overlay_drag(event, win)
        win.update_idletasks()
        self.overlay_drag = None
        self.cfg["overlay_position"] = [win.winfo_x(), win.winfo_y()]
        save_config(self.cfg)
        if not self.overlay_preview and self.overlay_kind == "text":
            self.overlay_timer = self.root.after(900, lambda: self._hide_overlay(win))

    def _hide_overlay(self, win):
        if self.overlay is win:
            self._cancel_overlay_timer()
            self.overlay = None
            self.overlay_preview = False
            self.overlay_drag = None
        try:
            win.destroy()
        except tk.TclError:
            pass

    def test_reminder(self):
        self._reminder_audio()
        self.show_visual_reminder(self._reminder_text("loss", float(self.cfg['warning_before'])))

    def _trace_loss(self, now, refresh=None):
        if not hasattr(self, "loss_trace"):
            self.loss_trace = deque(maxlen=1500)
        assets = getattr(self.tracker, "original_assets", None)
        match = getattr(self.tracker, "last_match", None)
        self.loss_trace.append({"time": now, "active": getattr(self.tracker, "active", False),
                               "score": getattr(match, "identity_score", None),
                               "phase": getattr(match, "visual_fraction", None),
                               "match": getattr(assets, "last_diagnostics", None),
                               "cost_ms": getattr(self.tracker, "last_track_ms", None),
                               "refresh": refresh, "axis_start": self.axis_start})

    def _loss_missing(self, now):
        self.tracker.active = False
        self._trace_loss(now)
        if self.last_loss_seen is not None and now-self.last_loss_seen > float(self.cfg.get("refresh_observation_gap", 2.0)):
            self.axis_start = None
            self.axis_warned = False
            self.birth_count = 0
            self.birth_first_t = None
            self.refresh_detector.reset()

    def _loss_icon_visible(self, now):
        return (self.tracker is not None and self.tracker.active and self.last_loss_seen is not None
                and now-self.last_loss_seen <= float(self.cfg.get("refresh_observation_gap", 2.0)))

    def _observe_loss(self, bright, now):
        if self.last_loss_seen is not None and now-self.last_loss_seen > float(self.cfg.get("refresh_observation_gap", 2.0)):
            self.refresh_detector.reset()
            # An expired application followed by a new acquisition may start a new timer.
            # Continuous visibility at zero alone is NOT evidence of a refresh.
            if self.axis_start is not None and now-self.axis_start >= float(self.cfg['loss_duration']):
                self.axis_start = None
                self.axis_warned = False
                self.birth_count = 0
                self.birth_first_t = None
        self.last_loss_seen = now
        refresh = self.refresh_detector.update(
            bright,
            now,
            self.tracker.frames_since_reacq,
            self.tracker.reacquire_count,
            getattr(getattr(self.tracker, "last_match", None), "visual_fraction", None),
        )
        self._trace_loss(now, refresh)

        if refresh.get("refresh"):
            fill_t = float(refresh["fill_time"])
            self.axis_start = fill_t
            self.axis_warned = False
            self.birth_count = 0
            self.birth_first_t = None
            self.messages.put({
                "type": "event",
                "text": (
                    f"刷新确认｜明度 {refresh['smooth']:.1f}%｜"
                    f"时间轴已回填"
                )
            })

        # Optional automatic first-axis start.
        if self.axis_start is None and bool(self.cfg["auto_birth_axis"]):
            if (
                self.tracker.frames_since_reacq
                >= int(self.cfg["reacquire_block_frames"])
                and refresh["smooth"] >= float(self.cfg["refresh_high"])
            ):
                if self.birth_count == 0:
                    self.birth_first_t = now
                self.birth_count += 1
                if self.birth_count >= int(self.cfg["birth_confirm_n"]):
                    self.axis_start = float(self.birth_first_t if self.birth_first_t is not None else now)
                    self.axis_warned = False
                    self.messages.put({
                        "type": "event",
                        "text": "检测到初始高亮：已自动建立 21 秒时间轴",
                    })
            else:
                self.birth_count = 0
                self.birth_first_t = None

        return refresh

    def monitor_loop(self):
        next_global = 0.0
        next_track = 0.0
        next_dizi = 0.0
        last_ui = 0.0

        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                time.sleep(0.05)
                continue

            now = time.monotonic()
            crop = None
            match = None
            loss_attempted = False

            try:
                # 笛子：固定小区域独立检测，不参与失魂定位/追踪逻辑。
                if (
                    bool(self.cfg.get("dizi_enabled", True))
                    and self.dizi_detector.configured
                    and now >= next_dizi
                ):
                    next_dizi = now + 1.0 / max(1.0, float(self.cfg.get("dizi_hz", 20)))
                    try:
                        dizi_rgb = self.capture.grab_rgb(self.dizi_detector.roi)
                        dizi_ratio = self.dizi_detector.ratio_from_rgb(dizi_rgb)
                        dizi_state = self.dizi_detector.update(dizi_ratio, time.monotonic(),
                                                               present=self.dizi_detector.present_in_rgb(dizi_rgb))
                        self.messages.put({
                            "type": "dizi_status",
                            **dizi_state,
                        })
                        if dizi_state.get("event") in ("ready", "repeat"):
                            self.messages.put({
                                "type": "dizi_reminder",
                                "repeat": dizi_state.get("event") == "repeat",
                                "ratio": dizi_ratio,
                            })
                    except CaptureOccluded as de:
                        self.messages.put({"type": "capture_blocked", "text": str(de)})
                    except Exception as de:
                        self.messages.put({
                            "type": "dizi_status",
                            "ready": False,
                            "ratio": None,
                            "repeat_remaining": None,
                            "error": str(de),
                        })

                extra = self.catalog.engine.sample(self.capture, time.monotonic(), CaptureOccluded)
                if extra is not None:
                    self.messages.put({"type": "catalog_status", "results": extra})
                if not self.cfg.get("loss_enabled", True):
                    time.sleep(.01)
                    continue
                if self.tracker.active:
                    if now >= next_track:
                        next_track = now + 1.0 / max(1.0, float(self.cfg["track_hz"]))
                        loss_attempted = True
                        crop, match = self.tracker.track(self.capture)
                else:
                    if now >= next_global:
                        next_global = now + 1.0 / max(1.0, float(self.cfg["global_fps"]))
                        loss_attempted = True
                        crop, match = self.tracker.global_acquire(self.capture)

                now = time.monotonic()
                if loss_attempted and crop is None:
                    self._loss_missing(now)
                if crop is not None:
                    bright = self.tracker.brightness_percent(crop)
                    if bright is not None:
                        refresh = self._observe_loss(bright, now)

                        remaining = None
                        if self.axis_start is not None:
                            remaining = max(
                                0.0,
                                float(self.cfg["loss_duration"]) - (now - self.axis_start)
                            )
                            if (
                                remaining <= float(self.cfg["warning_before"])
                                and remaining > 0
                                and not self.axis_warned
                            ):
                                self.axis_warned = True
                                self.messages.put({
                                    "type": "reminder",
                                    "remaining": remaining,
                                })

                        if now - last_ui >= 0.04:
                            last_ui = now
                            self.messages.put({
                                "type": "status",
                                "active": self.tracker.active,
                                "tier": self.tracker.last_tier,
                                "brightness": bright,
                                "refresh_state": refresh["state"],
                                "smooth": refresh["smooth"],
                                "jump": refresh["jump"],
                                "remaining": remaining,
                                "match": self.tracker.last_match,
                                "track_ms": self.tracker.last_track_ms,
                                "reacq": self.tracker.reacquire_count,
                                "frames": self.tracker.frames_since_reacq,
                            })

                elif now - last_ui >= 0.12:
                    last_ui = now
                    self.messages.put({
                        "type": "status",
                        "active": self.tracker.active,
                        "tier": self.tracker.last_tier,
                        "brightness": None,
                        "refresh_state": self.refresh_detector.state_text,
                        "remaining": (
                            None if self.axis_start is None or not self.tracker.active
                            else max(
                                0.0,
                                float(self.cfg["loss_duration"]) - (now - self.axis_start)
                            )
                        ),
                        "match": match or self.tracker.last_match,
                        "track_ms": self.tracker.last_track_ms,
                        "reacq": self.tracker.reacquire_count,
                        "frames": self.tracker.frames_since_reacq,
                    })

            except CaptureOccluded as e:
                # No fake disappearance, cooldown transition or reminder from an occluded frame.
                self.messages.put({"type": "capture_blocked", "text": str(e)})
                time.sleep(0.05)
            except Exception as e:
                self.messages.put({"type": "error", "text": str(e)})
                time.sleep(0.2)

            time.sleep(0.003)

    def poll_messages(self):
        try:
            while True:
                msg = self.messages.get_nowait()
                kind = msg.get("type")

                if kind == "hotkey_toggle":
                    if self.worker and self.worker.is_alive():
                        self.toggle_pause()
                    continue

                # Paused means fully quiet: discard stale status/events that were
                # already queued immediately before the hotkey was pressed.
                if self.pause_event.is_set() and kind in (
                    "status", "event", "reminder", "dizi_status", "dizi_reminder", "capture_blocked", "catalog_status"
                ):
                    continue

                if kind == "catalog_status":
                    if self.worker and self.worker.is_alive():
                        self.catalog.handle([r for r in msg["results"]
                                             if r["observed"] >= self.monitor_started])
                    continue

                if kind == "status":
                    self.vars["monitor"].set("运行中")
                    self.last_status = msg
                    active = bool(msg.get("active"))
                    self.vars["identity"].set(
                        "已锁定" if active else "正在全局搜索"
                    )
                    self.vars["track"].set(
                        f"{msg.get('tier','-')}｜连续 {msg.get('frames',0)} 帧｜重捕获 {msg.get('reacq',0)}"
                    )
                    b = msg.get("brightness")
                    self.vars["brightness"].set(
                        "-" if b is None else f"{b:.1f}%"
                    )
                    self.vars["refresh"].set(str(msg.get("refresh_state", "-")))
                    r = msg.get("remaining")
                    self.vars["remaining"].set(
                        "图标不可见" if not active else "等待起轴" if r is None else
                        "刷新未确认" if r <= 0 else f"{r:.2f} s"
                    )
                    m = msg.get("match")
                    if isinstance(m, MatchResult):
                        self.vars["match"].set(
                            (f"原图身份匹配 {m.identity_score:.3f}" if m.identity_score is not None
                             else f"d {m.mean_dist:.2f}px｜θ {m.mean_dir_deg:.1f}°｜P {m.dir_pass_ratio*100:.1f}%")
                        )
                    else:
                        self.vars["match"].set("-")
                    self.vars["cost"].set(f"{float(msg.get('track_ms',0)):.1f} ms")

                elif kind == "event":
                    self.vars["refresh"].set(msg.get("text", "事件"))

                elif kind == "reminder":
                    if not self.pause_event.is_set():
                        self._reminder_audio()
                        self.show_visual_reminder(self._reminder_text("loss", msg.get("remaining", 0)))

                elif kind == "dizi_status":
                    ratio = msg.get("ratio")
                    if msg.get("visible") is False:
                        self.vars["dizi"].set("图标不可见")
                        self.vars["dizi_repeat"].set("-")
                    elif msg.get("error"):
                        self.vars["dizi"].set("错误")
                        self.vars["dizi_repeat"].set(str(msg.get("error"))[:40])
                    elif ratio is None:
                        self.vars["dizi"].set("未设置")
                        self.vars["dizi_repeat"].set("-")
                    else:
                        ready = bool(msg.get("ready"))
                        self.vars["dizi"].set(
                            f"{'可用' if ready else '等待'}｜匹配 {float(ratio)*100:.1f}%"
                        )
                        remain = msg.get("repeat_remaining")
                        self.vars["dizi_repeat"].set(
                            "-" if remain is None else f"{float(remain):.1f} s"
                        )

                elif kind == "dizi_reminder":
                    if not self.pause_event.is_set():
                        self._dizi_audio()
                        self.show_visual_reminder(self._reminder_text("dizi", 0))

                elif kind == "capture_blocked":
                    self.vars["monitor"].set("检测暂缓：浮层遮挡")
                    self.vars["refresh"].set(msg.get("text", "请将浮层移出检测区域"))

                elif kind == "error":
                    self.vars["monitor"].set("错误")
                    self.vars["refresh"].set(msg.get("text", "未知错误")[:80])

        except queue.Empty:
            pass
        self.root.after(40, self.poll_messages)

    def save_debug(self):
        try:
            roi = tuple(map(int, self.cfg["loss_roi"]))
            rgb = self.capture.grab_rgb(roi)
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            Image.fromarray(rgb).save(DEBUG_DIR / f"buff_roi_{stamp}.png")
            meta = {
                "time": time.time(),
                "config": self.cfg,
                "loss_trace": list(self.loss_trace),
                "status": {
                    k: (v if not isinstance(v, MatchResult) else vars(v))
                    for k, v in self.last_status.items()
                    if k != "type"
                }
            }
            (DEBUG_DIR / f"status_{stamp}.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            messagebox.showinfo(
                "已保存",
                f"诊断已保存到：\n{DEBUG_DIR}",
                parent=self.root
            )
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.root)

    def on_close(self):
        self.hotkey_stop_event.set()
        self.stop_monitor()
        if self.hotkey_thread and self.hotkey_thread.is_alive():
            self.hotkey_thread.join(timeout=0.3)
        if self.icon_timer is not None:
            self.root.after_cancel(self.icon_timer)
        self.root.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    root = tk.Tk()
    try:
        app = ReminderApp(root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        messagebox.showerror("启动失败", f"请检查 skills.json 和资源是否完整：\n{exc}", parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
