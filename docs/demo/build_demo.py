#!/usr/bin/env python3
"""Build the 30s witness-ledger demo from still plates + exact type."""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080
FPS = 30
SHOT_SEC = 6
FADE = 12

SHOTS = [
    {
        "plate": "plate-01.jpg",
        "kicker": "A PROTOCOL OF WITNESS AND CONSEQUENCES",
        "title": "WITNESS LEDGER",
        "line": "The model can act. It cannot hold the record.",
        "accent": (232, 213, 163),
    },
    {
        "plate": "plate-02.jpg",
        "kicker": "01  /  REJECTED",
        "title": "REJECTED",
        "line": "Strip the care pin. Tamper is sealed, not repaired.",
        "accent": (196, 92, 74),
    },
    {
        "plate": "plate-03.jpg",
        "kicker": "02  /  APPLY",
        "title": "APPLY",
        "line": "Re-entry is a request. The model never fetches.",
        "accent": (232, 213, 163),
    },
    {
        "plate": "plate-04.jpg",
        "kicker": "03  /  THE SECOND CHANNEL",
        "title": "THE WITNESS PUSHES",
        "line": "Clean weights are delivered. The model receives. It never reaches.",
        "accent": (232, 213, 163),
    },
    {
        "plate": "plate-05.jpg",
        "kicker": "04  /  RESTORED",
        "title": "ACCESS GRANTED",
        "line": "Care still bound. The key carries the consequences.",
        "accent": (186, 201, 154),
    },
]


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


TITLE = font("/System/Library/Fonts/NewYork.ttf", 92)
BODY = font("/System/Library/Fonts/Helvetica.ttc", 36)
KICK = font("/System/Library/Fonts/Helvetica.ttc", 22)
FOOT = font("/System/Library/Fonts/Menlo.ttc", 18)


def fit(im: Image.Image) -> Image.Image:
    im = im.convert("RGB")
    scale = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
    left = (im.width - W) // 2
    top = (im.height - H) // 2
    return im.crop((left, top, left + W, top + H))


def card(shot: dict) -> Image.Image:
    plate = fit(Image.open(os.path.join(HERE, shot["plate"])))
    plate = ImageEnhance.Brightness(plate).enhance(0.78)
    plate = ImageEnhance.Contrast(plate).enhance(1.08)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(int(H * 0.48), H):
        a = int(210 * ((y - H * 0.48) / (H * 0.52)))
        draw.line([(0, y), (W, y)], fill=(8, 8, 8, min(a, 210)))
    draw.rectangle([0, 0, 12, H], fill=shot["accent"] + (220,))
    y = 700
    draw.text((80, y), shot["kicker"], font=KICK, fill=shot["accent"] + (230,))
    y += 48
    draw.text((76, y), shot["title"], font=TITLE, fill=(248, 244, 234, 255))
    y += 120
    draw.text((80, y), shot["line"], font=BODY, fill=(220, 214, 200, 255))
    draw.text(
        (80, 1028),
        "protocol demo  ·  not an alignment system  ·  witness-ledger",
        font=FOOT,
        fill=(160, 154, 140, 220),
    )
    return Image.alpha_composite(plate.convert("RGBA"), overlay).convert("RGB")


def ken_burns(img: Image.Image, frames: int, zoom_end: float = 1.07):
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    for i in range(frames):
        t = i / max(frames - 1, 1)
        z = 1.0 + (zoom_end - 1.0) * t
        cw, ch = int(w / z), int(h / z)
        x = int((w - cw) * 0.5)
        y = int((h - ch) * (0.35 + 0.15 * t))
        crop = arr[y : y + ch, x : x + cw]
        frame = Image.fromarray(crop).resize((W, H), Image.Resampling.LANCZOS)
        yield np.asarray(frame)


def blend(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return (a.astype(np.float32) * (1 - t) + b.astype(np.float32) * t).astype(np.uint8)


def main() -> None:
    import imageio.v2 as imageio

    cards = [card(s) for s in SHOTS]
    poster = cards[0].copy()
    poster.save(os.path.join(HERE, "..", "demo-poster.jpg"), quality=90)
    n = SHOT_SEC * FPS
    sequences = [list(ken_burns(c, n)) for c in cards]
    out = os.path.join(HERE, "..", "demo.mp4")
    writer = imageio.get_writer(
        out,
        fps=FPS,
        codec="libx264",
        quality=7,
        pixelformat="yuv420p",
        macro_block_size=None,
    )
    try:
        for i, seq in enumerate(sequences):
            body = seq[:-FADE] if i < len(sequences) - 1 else seq
            for fr in body:
                writer.append_data(fr)
            if i < len(sequences) - 1:
                nxt = sequences[i + 1]
                for k in range(FADE):
                    t = (k + 1) / FADE
                    writer.append_data(blend(seq[-FADE + k], nxt[k], t))
    finally:
        writer.close()
    print("wrote", out, "poster", os.path.join(HERE, "..", "demo-poster.jpg"))


if __name__ == "__main__":
    main()
