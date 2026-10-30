"""Fixed-pitch glyph matcher for the machine readable zone.

The MRZ is monospaced, so a line can be cut into equal character cells and each cell
classified against glyph templates.  This cannot drop, merge or invent characters the
way a general OCR engine can.  Templates are rendered from the bundled monospace
fonts (the same family the specimen documents use); on a real OCR-B document the
match scores fall and the caller falls back to the OCR engine, so a low score is
never trusted.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
CELL_W, CELL_H = 24, 40
FONTS = ["DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "LiberationMono-Regular.ttf"]
PITCH_RATIO = {"DejaVuSansMono.ttf": 0.602, "DejaVuSansMono-Bold.ttf": 0.602, "LiberationMono-Regular.ttf": 0.600}
# advance / cap-height for the template font family (used to choose 30 / 36 / 44 columns)
ADV_OVER_CAP = 0.826


def _norm(v: np.ndarray) -> np.ndarray:
    v = v - v.mean()
    return v / (np.linalg.norm(v) + 1e-9)


@lru_cache(maxsize=1)
def _templates() -> np.ndarray:
    """(n_fonts * n_chars, CELL_W*CELL_H) matrix of unit-norm template vectors."""
    rows = []
    for fn in FONTS:
        path = ROOT / "assets" / "fonts" / fn
        if not path.exists():
            continue
        f = ImageFont.truetype(str(path), 120)
        adv = int(round(PITCH_RATIO[fn] * 120))
        top, bot = 10 ** 6, -1
        glyphs = {}
        for c in CHARS:
            im = Image.new("L", (adv, 200), 0)
            ImageDraw.Draw(im).text((0, 20), c, font=f, fill=255)
            a = np.asarray(im)
            glyphs[c] = a
            ys = np.where(a.max(axis=1) > 80)[0]
            if len(ys):
                top, bot = min(top, ys[0]), max(bot, ys[-1])
        # vertical extent of capitals / digits: take the extent of 'H' and '0' only (ignore '<' overshoot)
        ys_h = np.where(glyphs["H"].max(axis=1) > 80)[0]
        top, bot = int(ys_h[0]), int(ys_h[-1])
        for c in CHARS:
            cell = glyphs[c][top : bot + 1, :].astype(np.float32) / 255.0
            cell = cv2.resize(cell, (CELL_W, CELL_H), interpolation=cv2.INTER_AREA)
            rows.append(_norm(cell.reshape(-1)))
    return np.stack(rows)


def clean_rules(gray: np.ndarray) -> np.ndarray:
    """Blank out vertical rules (sticker borders, card edges) that would distort the text extent.
    A rule is dark in the gap between two text lines, text is not."""
    bands = _bands(gray)
    if len(bands) < 2:
        return gray
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    dark = g < (np.percentile(g, 50) - 40)
    gap_rows = np.zeros(gray.shape[0], bool)
    for (a0, a1), (b0, b1) in zip(bands[:-1], bands[1:]):
        gap_rows[a1 + 2 : b0 - 1] = True
    if gap_rows.sum() < 3:
        return gray
    rules = dark[gap_rows].mean(axis=0) > 0.6
    if not rules.any():
        return gray
    out = gray.copy()
    bg = int(np.median(gray))
    for i in np.where(rules)[0]:
        out[:, max(0, i - 2) : i + 3] = bg
    return out


def _ink(gray: np.ndarray) -> np.ndarray:
    g = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    lo, hi = np.percentile(g, 2), np.percentile(g, 98)
    return np.clip((hi - g) / max(hi - lo, 1.0), 0, 1)


def _bands(gray: np.ndarray):
    from .ocr import split_lines  # local import: ocr imports this module

    return split_lines(gray)


def _select_bands(gray: np.ndarray, ink: np.ndarray):
    """Pick the MRZ lines: the bottom-most 2-3 text bands that share height and width."""
    bands = _bands(gray)
    if len(bands) < 2:
        return []
    ext = []
    for (a, b) in bands:
        cp = ink[a:b].mean(axis=0)
        cols = np.where(cp > max(0.05, 0.15 * np.percentile(cp, 99)))[0]
        ext.append((int(cols[0]), int(cols[-1]) + 1) if len(cols) else (0, 0))
    ref = len(bands) - 1
    rh = bands[ref][1] - bands[ref][0]
    rw = ext[ref][1] - ext[ref][0]
    keep = [ref]
    for k in range(ref - 1, -1, -1):
        h = bands[k][1] - bands[k][0]
        w = ext[k][1] - ext[k][0]
        if abs(h - rh) <= 0.25 * rh and abs(w - rw) <= 0.08 * rw and len(keep) < 3:
            keep.append(k)
        else:
            break
    keep.sort()
    return [bands[k] for k in keep] if len(keep) >= 2 else []


def read(gray: np.ndarray) -> tuple[list[str] | None, float, float]:
    """Return (lines, mean_score, min_line_score) or (None, 0, 0).  Scores are normalised
    correlations in [0, 1]; genuine specimen-font MRZ lines score > 0.8."""
    gray = clean_rules(gray)
    ink = _ink(gray)
    bands = _select_bands(gray, ink)
    if len(bands) < 2:
        return None, 0.0, 0.0
    T = _templates()
    nT = T.shape[0] // len(CHARS)
    colprof = ink[bands[0][0] : bands[-1][1]].mean(axis=0)
    cols = np.where(colprof > max(0.05, 0.15 * np.percentile(colprof, 99)))[0]
    if len(cols) < 20:
        return None, 0.0, 0.0
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    h = float(np.median([b[1] - b[0] for b in bands]))
    if h < 9:
        return None, 0.0, 0.0
    width = x1 - x0
    n = min((30, 36, 44), key=lambda k: abs(width - k * ADV_OVER_CAP * h))
    pitch0 = width / (n - 0.15)
    lines, scores = [], []
    for (y0, y1) in bands:
        band = ink[max(0, y0 - 1) : y1 + 1, :]
        best = (-1.0, None)
        for sx in (0.985, 1.0, 1.015):
            pitch = pitch0 * sx
            sc = CELL_W / pitch
            scaled = cv2.resize(band, None, fx=sc, fy=CELL_H / band.shape[0], interpolation=cv2.INTER_AREA)
            for off in range(-7, 4):
                start = int(round((x0 - 0.06 * pitch) * sc)) + off
                if start < -CELL_W or start + n * CELL_W > scaled.shape[1] + CELL_W:
                    continue
                pad = np.pad(scaled, ((0, 0), (CELL_W, CELL_W * 2)))
                cells = np.stack([_norm(pad[:, start + CELL_W + i * CELL_W : start + CELL_W + (i + 1) * CELL_W].reshape(-1))
                                  if False else pad[:, start + CELL_W + i * CELL_W : start + CELL_W + (i + 1) * CELL_W].reshape(-1)
                                  for i in range(n)])
                cells = cells - cells.mean(axis=1, keepdims=True)
                cells = cells / (np.linalg.norm(cells, axis=1, keepdims=True) + 1e-9)
                sim = cells @ T.T  # (n, nT*chars)
                sim = sim.reshape(n, nT, len(CHARS)).max(axis=1)
                tot = float(sim.max(axis=1).mean())
                if tot > best[0]:
                    best = (tot, sim)
        tot, sim = best
        if sim is None:
            return None, 0.0, 0.0
        lines.append("".join(CHARS[i] for i in sim.argmax(axis=1)))
        scores.append(tot)
    return lines, float(np.mean(scores)), float(min(scores))
