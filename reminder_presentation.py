"""Pure rendering helpers for reminder text and clockwise cooldown icons."""
import math
from PIL import Image, ImageDraw, ImageFont, ImageOps


def format_reminder(template, seconds, skill):
    seconds = max(0, int(seconds)) if seconds is not None else 0
    return str(template).replace('{%d}', str(seconds)).replace('{%s}', str(skill))


def cooldown_icon(source, color, remaining=None, duration=0, show_number=True, size=88, ready=False, unknown=False):
    base = Image.new('RGB', (size, size), color)
    glyph = source.convert('RGBA').copy()
    glyph = ImageOps.contain(glyph, (round(size*.84), round(size*.84)), Image.Resampling.LANCZOS)
    base.paste(glyph, ((size-glyph.width)//2, (size-glyph.height)//2), glyph)
    # Time elapsed becomes gray: start at 12 o'clock, sweep clockwise.
    fraction = 0 if ready else (1 if remaining is None or duration <= 0
                               else max(0, min(1, 1-remaining/duration)))
    mask = Image.new('L', base.size, round(fraction)*255 if fraction in (0, 1) else 0)
    if 0 < fraction < 1:
        radius = math.ceil(size/math.sqrt(2))+2
        center = size/2
        ImageDraw.Draw(mask).pieslice((center-radius, center-radius, center+radius, center+radius),
                                     -90, -90+360*fraction, fill=255)
    base = Image.composite(ImageOps.grayscale(base).convert('RGB'), base, mask)
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((1, 1, size-2, size-2), radius=8, outline='#c4c9d0', width=2)
    if show_number and (unknown or (remaining is not None and duration > 0)):
        text = "?" if unknown else str(max(0, int(remaining)))
        font = ImageFont.load_default(size=round(size*.42))
        draw.text((size/2, size/2), text, anchor='mm', font=font, fill='white',
                  stroke_width=2, stroke_fill='black')
    base = base.convert('RGBA')
    rounded = Image.new('L', base.size, 0)
    ImageDraw.Draw(rounded).rounded_rectangle((1, 1, size-2, size-2), radius=8, fill=255)
    base.putalpha(rounded)
    return base


def dizi_icon_state(cfg, ready, last_alert, cooldown_start, now):
    """Only a confirmed ready-to-used transition starts a skill cooldown."""
    duration = max(0., float(cfg.get('dizi_cooldown_seconds', 0)))
    if ready:
        return None, duration, True
    remaining = (max(0., duration-(now-cooldown_start))
                 if cooldown_start is not None and duration > 0 else None)
    return remaining, duration, False
