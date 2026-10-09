"""原始游戏素材加载与识别。"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

@dataclass
class FlagTemplate:
    bgr: np.ndarray
    alpha: np.ndarray | None

def load_flag_template(path: Path) -> FlagTemplate:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Cannot decode image: {path}")
    if image.ndim == 3 and image.shape[2] == 4:
        return FlagTemplate(image[..., :3], image[..., 3])
    if image.ndim == 3 and image.shape[2] == 3:
        return FlagTemplate(image, None)
    raise ValueError(f"Unsupported template format: {path} {image.shape}")


@dataclass
class GlyphBox:
    x: int
    y: int
    width: int
    height: int
    visual_fraction: float = 1.0


class OriginalAssets:
    def __init__(self, root: Path):
        self.root = root
        self.frame = load_flag_template(root / 'yysls_export/frame_debuff.png')
        self.glyph = load_flag_template(root / 'yysls_export/debuff_shihunluopo.png')
        self.dizi = load_flag_template(root / 'yysls_export/dizi.png')
        if any(item.alpha is None for item in (self.frame, self.glyph, self.dizi)):
            raise ValueError('原图必须保留透明通道')
        self.identity = np.where(self.glyph.alpha >= 16, 255, 0).astype(np.uint8)

    def brightness_mask(self, size=(43, 63)):
        # Only sample the opaque interior of the white glyph, never the flag border.
        canvas = np.zeros(self.frame.alpha.shape, np.uint8)
        canvas[14:58, 4:44] = self.glyph.alpha
        alpha = cv2.resize(canvas, size, interpolation=cv2.INTER_AREA)
        return cv2.erode((alpha >= 220).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)

    @lru_cache(maxsize=8)
    def _loss_templates(self, width):
        templates = []
        for w in range(max(18, round(width * 40/48 * .85)), round(width * 40/48 * 1.25)+1):
            h = round(w * 44/40)
            alpha = cv2.resize(self.glyph.alpha.astype(np.float32), (w, h))
            yy, xx = np.mgrid[:h, :w]
            angle = np.arctan2(xx-w/2, -(yy-h/2)) % (2*np.pi)
            # Model the clockwise cooldown wipe instead of requiring a fully bright glyph.
            for portion in (0, .125, .25, .375, .5, .625, .75, .875):
                for dark in ((1,) if portion == 0 else (.35, .6)):
                    shadow = np.where(angle > portion*2*np.pi, dark, 1).astype(np.float32)
                    templates.append((alpha * shadow, w, h, 1.0 if portion == 0 else portion))
        return templates

    def find_loss(self, rgb, width=48, threshold=.90, margin=.04):
        if rgb.size == 0:
            return None
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        # Suppress the red background; normalized correlation tolerates overall dimming.
        features = gray * (1-hsv[..., 1]/255)**2
        ranked = []
        best_score = 0.0
        for template, w, h, phase in self._loss_templates(round(width)):
            if w > rgb.shape[1] or h > rgb.shape[0]:
                continue
            response = cv2.matchTemplate(features, template, cv2.TM_CCOEFF_NORMED)
            response = np.nan_to_num(response, nan=0, posinf=0, neginf=0)
            # Retain different slots, then merge scale/phase variants of each slot.
            for _ in range(3):
                _, score, _, (x, y) = cv2.minMaxLoc(response)
                best_score = max(best_score, score)
                if score < threshold:
                    break
                ranked.append((score, x, y, w, h, phase))
                response[max(0,y-h//2):y+h//2+1, max(0,x-w//2):x+w//2+1] = 0
        ranked.sort(reverse=True)
        slots = []
        for item in ranked:
            score, x, y, w, h, phase = item
            if all(abs(x+w/2-(other[1]+other[3]/2)) > max(w,other[3])*.6
                   or abs(y+h/2-(other[2]+other[4]/2)) > max(h,other[4])*.6 for other in slots):
                slots.append(item)
        self.last_diagnostics = {"best_score": best_score, "threshold": threshold, "margin": margin,
                                 "runner_up": slots[1][0] if len(slots)>1 else None,
                                 "reason": "below_threshold" if not slots else "matched"}
        if not slots:
            return None
        if len(slots)>1 and slots[0][0]-slots[1][0]<margin:
            self.last_diagnostics["reason"] = "ambiguous"
            return None
        score, x, y, w, h, phase = slots[0]
        # Infer canonical flag coordinates, even when selection clips its empty top margin.
        return score, GlyphBox(round(x-4*w/40), round(y-14*h/44),
                               round(w*48/40), round(h*72/44), phase)

    def calibrate_dizi(self, rgb, threshold=225):
        """Align source alpha to the selected ready-state screen area."""
        ys, xs = np.nonzero(self.dizi.alpha >= 128)
        alpha = self.dizi.alpha[ys.min():ys.max()+1, xs.min():xs.max()+1]
        white = (cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) >= threshold).astype(np.float32)
        height, width = white.shape
        fit = min(width / alpha.shape[1], height / alpha.shape[0])
        best = None
        for scale in np.linspace(fit * 0.55, fit, 16):
            w, h = round(alpha.shape[1] * scale), round(alpha.shape[0] * scale)
            if min(w, h) < 6 or w > width or h > height:
                continue
            mask = (cv2.resize(alpha, (w, h), interpolation=cv2.INTER_AREA) >= max(220, threshold)).astype(np.float32)
            count = float(mask.sum())
            if count < 20 or count >= mask.size:
                continue
            response = cv2.matchTemplate(white, mask, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(np.nan_to_num(response))
            coverage = float((white[y:y+h, x:x+w] * mask).sum() / count)
            if score >= 0.45 and coverage >= 0.65 and (best is None or score > best[0]):
                result = np.zeros((height, width), bool)
                result[y:y+h, x:x+w] = mask > 0
                best = (score, result)
        if best is None:
            raise ValueError('所选区域未匹配到亮起的笛子原图。请紧贴笛子图案框选，避开数字与其他技能。')
        # Keep only source-aligned pixels that are actually white at this UI scale.
        calibrated = best[1] & (white > 0)
        if np.count_nonzero(calibrated) < 20:
            raise ValueError('笛子图案有效白色像素不足，请放大 UI 后重新框选。')
        return calibrated

    def write_preview(self, path):
        glyph = Image.open(self.root / 'yysls_export/debuff_shihunluopo.png').convert('RGBA')
        composite = Image.open(self.root / 'yysls_export/frame_debuff.png').convert('RGBA')
        composite.alpha_composite(glyph, (4, 14))
        canvas = Image.new('RGB', (48 * 6, 72 * 3), '#26303b')
        canvas.paste(composite.resize((144, 216)), (0, 0), composite.resize((144, 216)))
        mask = Image.fromarray(self.brightness_mask((48, 72)).astype(np.uint8) * 255)
        canvas.paste(mask.resize((144, 216)), (144, 0))
        canvas.save(path)
