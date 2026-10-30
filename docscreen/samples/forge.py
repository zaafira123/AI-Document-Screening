"""Simulated forgery operations used to build the tampered test samples.
These are deliberately ordinary edits (a careless forger), so the detector can
be evaluated honestly on a synthetic set - they are NOT a security benchmark."""
from __future__ import annotations

import io
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import render as R


def scan(img: Image.Image, seed: int = 0, noise: float = 1.8, quality: int = 90) -> Image.Image:
    """Simulate a flatbed scan / camera capture: sensor noise + one JPEG pass."""
    rng = np.random.default_rng(seed)
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), 0.45)
    a += rng.normal(0, noise, a.shape)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return jpeg_roundtrip(out, quality)


def jpeg_roundtrip(img: Image.Image, quality: int = 90) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _clamp(box, shape, pad=0):
    x, y, w, h = box
    x0, y0 = max(0, int(x - pad)), max(0, int(y - pad))
    x1, y1 = min(shape[1], int(x + w + pad)), min(shape[0], int(y + h + pad))
    return x0, y0, x1, y1


def edit_text(img: Image.Image, box, new_value: str, size: int = 29, bold: bool = True) -> Image.Image:
    """Wipe the value in `box` (inpaint) and type `new_value` in a different font."""
    a = np.array(img.convert("RGB"))
    x0, y0, x1, y1 = _clamp(box, a.shape, pad=2)
    roi = a[y0:y1, x0:x1].copy()
    g = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    mask = (g < (np.median(g) - 30)).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    a[y0:y1, x0:x1] = cv2.inpaint(roi, mask, 3, cv2.INPAINT_TELEA)
    out = Image.fromarray(a)
    d = ImageDraw.Draw(out)
    f = R.font(R.ALT_SANSB if bold else R.SANS, size)
    d.text((box[0], box[1] + 2), new_value, font=f, fill=(30, 34, 44))
    return out


def swap_photo(img: Image.Image, box, portrait: Image.Image, quality: int = 62) -> Image.Image:
    """Paste another portrait over the holder photo (different compression history,
    slightly different colour balance, like a photo lifted from another image)."""
    x, y, w, h = box
    p = jpeg_roundtrip(portrait.resize((w, h)), quality)
    p = p.filter(__import__("PIL.ImageFilter", fromlist=["x"]).GaussianBlur(0.8))
    arr = np.asarray(p).astype(np.float32)
    arr = np.clip(arr * np.array([1.04, 1.0, 0.95]) + 4, 0, 255).astype(np.uint8)
    out = img.copy()
    out.paste(Image.fromarray(arr), (x, y))
    return out


def copy_region(img: Image.Image, src_box, dst_xy) -> Image.Image:
    """Clone a region (e.g. a genuine entry stamp) to a second location."""
    x, y, w, h = src_box
    out = img.copy()
    out.paste(img.crop((x, y, x + w, y + h)), dst_xy)
    return out


def damage(img: Image.Image, blur: float = 4.0, darken: float = 0.55, glare: bool = True) -> Image.Image:
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    a = cv2.GaussianBlur(a, (0, 0), blur) * darken
    if glare:
        h, w = a.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        g = np.exp(-(((xx - w * 0.7) / (w * 0.18)) ** 2 + ((yy - h * 0.35) / (h * 0.14)) ** 2))
        a = a + 255 * g[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def save_jpeg(img: Image.Image, path, quality: int = 90, software: str | None = None,
              camera: tuple[str, str] | None = None, taken: datetime | None = None,
              modified: datetime | None = None) -> None:
    exif = Image.Exif()
    if camera:
        exif[0x010F], exif[0x0110] = camera
    if software:
        exif[0x0131] = software
    if modified:
        exif[0x0132] = modified.strftime("%Y:%m:%d %H:%M:%S")
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
    kw = {"quality": quality}
    if len(exif):
        kw["exif"] = exif
    img.convert("RGB").save(path, "JPEG", **kw)
