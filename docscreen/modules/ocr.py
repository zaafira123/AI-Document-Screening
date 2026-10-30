"""OCR engines and the MRZ reader.

Engines are discovered at run time:
  rapidocr   pip install rapidocr-onnxruntime        (no system install needed)
  tesseract  Tesseract binary on PATH (or TESSERACT_CMD)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import nullcontext
from datetime import date

import cv2
import numpy as np

from . import mrz as M
from . import mrzglyph

_lock = threading.Lock()
MRZ_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _box(text, x, y, w, h, conf):
    return {"text": text, "x": int(x), "y": int(y), "w": int(w), "h": int(h), "conf": round(float(conf), 1)}


def group_words(words: list[dict]) -> list[dict]:
    """Merge words that sit on the same baseline and close together into segments."""
    words = [w for w in words if w["text"].strip()]
    if not words:
        return []
    words.sort(key=lambda w: (w["y"] + w["h"] / 2, w["x"]))
    rows: list[list[dict]] = []
    for w in words:
        cy = w["y"] + w["h"] / 2
        for r in rows:
            ry = np.mean([q["y"] + q["h"] / 2 for q in r])
            rh = np.median([q["h"] for q in r])
            if abs(cy - ry) < 0.55 * max(rh, w["h"]):
                r.append(w)
                break
        else:
            rows.append([w])
    segs = []
    for r in rows:
        r.sort(key=lambda w: w["x"])
        cur = [r[0]]
        for w in r[1:]:
            prev = cur[-1]
            gap = w["x"] - (prev["x"] + prev["w"])
            if gap <= 1.15 * max(prev["h"], w["h"]):
                cur.append(w)
            else:
                segs.append(cur)
                cur = [w]
        segs.append(cur)
    out = []
    for s in segs:
        x0, y0 = min(w["x"] for w in s), min(w["y"] for w in s)
        x1, y1 = max(w["x"] + w["w"] for w in s), max(w["y"] + w["h"] for w in s)
        out.append(_box(" ".join(w["text"] for w in s), x0, y0, x1 - x0, y1 - y0, np.mean([w["conf"] for w in s])))
    out.sort(key=lambda b: (b["y"], b["x"]))
    return out


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
class TesseractEngine:
    name = "Tesseract"
    needs_lock = False  # every call is its own subprocess

    def __init__(self):
        cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        if not cmd:
            for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                      "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
                if os.path.exists(p):
                    cmd = p
                    break
        if not cmd:
            raise FileNotFoundError("tesseract binary not found")
        self.cmd = cmd
        self.version = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=20).stdout.splitlines()[0]

    def _run(self, gray: np.ndarray, psm: int, whitelist: str | None = None):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "in.png")
            cv2.imwrite(path, gray)
            args = [self.cmd, path, "stdout", "--psm", str(psm), "-l", "eng", "-c", "preserve_interword_spaces=1"]
            if whitelist:
                args += ["-c", f"tessedit_char_whitelist={whitelist}"]
            args.append("tsv")
            res = subprocess.run(args, capture_output=True, text=True, timeout=90)
        rows = []
        for line in res.stdout.splitlines()[1:]:
            c = line.split("\t")
            if len(c) < 12 or c[0] != "5" or not c[11].strip():
                continue
            conf = float(c[10])
            if conf < 0:
                continue
            rows.append(_box(c[11], c[6], c[7], c[8], c[9], conf))
        return rows

    def read(self, img_bgr: np.ndarray, scale: float = 1.6) -> list[dict]:
        g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
        if scale != 1:
            g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        words = self._run(g, 11)
        for w in words:
            for k in ("x", "y", "w", "h"):
                w[k] = int(round(w[k] / scale))
        return group_words(words)

    def read_lines_mrz(self, gray: np.ndarray) -> list[str]:
        words = self._run(gray, 6, MRZ_WHITELIST)
        rows = group_rows(words)
        return rows

    def read_line(self, gray: np.ndarray) -> str:
        """OCR a single text line (psm 7) restricted to the MRZ alphabet."""
        words = self._run(gray, 7, MRZ_WHITELIST)
        return "".join(w["text"] for w in sorted(words, key=lambda w: w["x"]))


class RapidOcrEngine:
    name = "RapidOCR"
    needs_lock = True  # one shared ONNX session

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR  # noqa

        self.engine = RapidOCR()
        self.version = "rapidocr-onnxruntime"

    def read(self, img_bgr: np.ndarray, scale: float = 1.0) -> list[dict]:
        res, _ = self.engine(img_bgr)
        out = []
        for box, text, conf in res or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            out.append(_box(text, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), float(conf) * 100))
        out.sort(key=lambda b: (b["y"], b["x"]))
        return out

    def read_lines_mrz(self, gray: np.ndarray) -> list[str]:
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        boxes = self.read(bgr)
        return [b["text"] for b in sorted(boxes, key=lambda b: b["y"])]

    def read_line(self, gray: np.ndarray) -> str:
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        res, _ = self.engine(bgr)
        boxes = sorted(res or [], key=lambda r: min(p[0] for p in r[0]))
        return "".join(r[1] for r in boxes).replace(" ", "")


def group_rows(words: list[dict]) -> list[str]:
    words = sorted(words, key=lambda w: (w["y"] + w["h"] / 2, w["x"]))
    rows: list[list[dict]] = []
    for w in words:
        cy = w["y"] + w["h"] / 2
        if rows and abs(cy - np.mean([q["y"] + q["h"] / 2 for q in rows[-1]])) < 0.6 * w["h"]:
            rows[-1].append(w)
        else:
            rows.append([w])
    return ["".join(w["text"] for w in sorted(r, key=lambda w: w["x"])) for r in rows]


_engines = {"obj": None, "tried": False, "errors": []}


def get_engine():
    """Preferred engine: env DOCSCREEN_OCR=rapidocr|tesseract, else RapidOCR then Tesseract."""
    if _engines["tried"]:
        return _engines["obj"]
    pref = os.environ.get("DOCSCREEN_OCR", "").lower()
    order = [pref] if pref else ["rapidocr", "tesseract"]
    for kind in order:
        try:
            _engines["obj"] = RapidOcrEngine() if kind == "rapidocr" else TesseractEngine()
            break
        except Exception as e:
            _engines["errors"].append(f"{kind}: {type(e).__name__}")
    _engines["tried"] = True
    return _engines["obj"]


def engine_info() -> dict:
    e = get_engine()
    return {"available": e is not None, "name": e.name if e else None, "version": getattr(e, "version", None),
            "errors": _engines["errors"]}


def read_text(img_bgr: np.ndarray) -> list[dict]:
    e = get_engine()
    if e is None:
        return []
    with (_lock if getattr(e, "needs_lock", False) else nullcontext()):
        return e.read(img_bgr)


# --------------------------------------------------------------------------
# MRZ reader
# --------------------------------------------------------------------------
def _prep_variants(gray: np.ndarray):
    """Yield differently pre-processed versions of an MRZ crop."""
    h, w = gray.shape
    scale = max(1.0, 1800 / max(w, 1))
    big = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else gray
    yield "gray", big
    clahe = cv2.createCLAHE(2.0, (8, 8)).apply(big)
    _, otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield "otsu", otsu
    ad = cv2.adaptiveThreshold(big, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    yield "adaptive", ad


def locate_mrz(img_bgr: np.ndarray, boxes: list[dict] | None = None) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) of the MRZ block. Uses OCR boxes if available, else the
    bottom band of the page where the MRZ always is."""
    H, W = img_bgr.shape[:2]
    if boxes:
        cands = [b for b in boxes if b["text"].count("<") >= 3 and len(b["text"]) >= 15]
        if cands:
            x0 = max(0, min(b["x"] for b in cands) - 30)
            y0 = max(0, min(b["y"] for b in cands) - 25)
            x1 = min(W, max(b["x"] + b["w"] for b in cands) + 30)
            y1 = min(H, max(b["y"] + b["h"] for b in cands) + 25)
            return x0, y0, x1 - x0, y1 - y0
    # fallback: find the lowest dense band of dark text with small, regular pitch
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    bw = (cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15) > 0).astype(np.uint8)
    rows = bw.mean(axis=1)
    top = int(H * 0.5)
    thr = max(0.04, rows[top:].mean() * 1.1)
    ys = [y for y in range(top, H) if rows[y] > thr]
    if ys:
        y0 = max(0, min(ys) - 20)
        return 0, y0, W, H - y0
    return 0, int(H * 0.68), W, H - int(H * 0.68)


def split_lines(gray: np.ndarray, max_lines: int = 3) -> list[tuple[int, int]]:
    """Find the vertical extent (y0, y1) of each text line in an MRZ crop using the
    row-ink profile. Returns [] when no clear bands are found."""
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    prof = bw.mean(axis=1) / 255.0
    thr = max(0.02, 0.10 * prof.max())
    on = prof > thr
    bands, start = [], None
    for y, v in enumerate(on):
        if v and start is None:
            start = y
        elif not v and start is not None:
            bands.append([start, y])
            start = None
    if start is not None:
        bands.append([start, len(on)])
    # merge bands separated by a tiny gap (broken characters), drop slivers
    merged: list[list[int]] = []
    for b in bands:
        if merged and b[0] - merged[-1][1] <= 3:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    if not merged:
        return []
    hs = sorted(b[1] - b[0] for b in merged)
    med = hs[len(hs) // 2]
    merged = [b for b in merged if (b[1] - b[0]) >= 0.5 * med]
    return [tuple(b) for b in merged[-max_lines:]] if len(merged) > max_lines else [tuple(b) for b in merged]


def _line_crops(gray: np.ndarray):
    """Yield (name, [line images]) using row-profile segmentation, tight horizontal trim."""
    bands = split_lines(gray)
    if len(bands) < 2:
        return
    _, bw = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cols = np.where(bw.mean(axis=0) > 2)[0]
    if len(cols) == 0:
        return
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    for pad_name, pad in (("tight", 6), ("loose", 14)):
        imgs = []
        for (y0, y1) in bands:
            ch = y1 - y0
            sub = gray[max(0, y0 - pad) : min(gray.shape[0], y1 + pad), max(0, x0 - pad) : min(gray.shape[1], x1 + pad)]
            target_h = 90
            f = target_h / max(sub.shape[0], 1)
            sub = cv2.resize(sub, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
            sub = cv2.copyMakeBorder(sub, 20, 20, 30, 30, cv2.BORDER_REPLICATE)
            imgs.append(sub)
        yield f"lines-{pad_name}", imgs


def _reflow_lines(gray: np.ndarray, n: int):
    """MRZ text is fixed-pitch.  Cut every line into n equal character cells, re-assemble the
    cells with wide gaps and OCR the result: a character can then neither be dropped nor
    merged with its neighbour.  Returns a list of line images (one per MRZ line) or None."""
    bands = split_lines(gray)
    if len(bands) < 2:
        return None
    _, bw = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cols = np.where(bw.mean(axis=0) > 2)[0]
    if len(cols) == 0:
        return None
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    cw = (x1 - x0) / n
    out = []
    for (y0, y1) in bands:
        cells = []
        for i in range(n):
            a, b = int(round(x0 + i * cw)), int(round(x0 + (i + 1) * cw))
            c = gray[max(0, y0 - 3) : y1 + 3, max(0, a - 1) : b + 1]
            if c.size == 0:
                return None
            f = 60 / c.shape[0]
            c = cv2.resize(c, (max(8, int(c.shape[1] * f)), 60), interpolation=cv2.INTER_CUBIC)
            canvas = np.full((84, 76), 255, np.uint8)
            xo = (76 - c.shape[1]) // 2
            canvas[12:72, max(0, xo) : max(0, xo) + min(c.shape[1], 76)] = c[:, : min(c.shape[1], 76)]
            cells.append(canvas)
        out.append(np.hstack([np.hstack([cell, np.full((84, 34), 255, np.uint8)]) for cell in cells]))
    return out


def _clean_line(t: str) -> str:
    return "".join(ch for ch in t.upper().replace(" ", "") if ch in MRZ_WHITELIST)


def _fit_length(line: str, lengths=(30, 36, 44)) -> str:
    """Pad with filler or trim stray edge characters so that the line has a legal MRZ length."""
    n = len(line)
    target = min(lengths, key=lambda L: (abs(L - n), L))
    if n < target:
        return line + "<" * (target - n)
    if n > target:
        # drop extra characters from the end when they are fillers, else from the start
        excess = n - target
        tail = line[target:]
        return line[:target] if set(tail) <= {"<"} else line[excess:] if line[:excess].strip("<") == "" else line[:target]
    return line


def read_mrz(img_bgr: np.ndarray, boxes: list[dict] | None = None, today: date | None = None) -> dict:
    """Read and validate the MRZ. Returns a dict (never raises)."""
    out = {"found": False, "lines": None, "parsed": None, "notes": [], "engine": None, "region": None, "attempts": []}
    eng = get_engine()
    if eng is None:
        out["error"] = "No OCR engine available"
        return out
    out["engine"] = eng.name
    x, y, w, h = locate_mrz(img_bgr, boxes)
    out["region"] = [x, y, w, h]
    crop = cv2.cvtColor(img_bgr[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
    best = None

    def consider(name, lines, repair=True):
        nonlocal best
        if repair:
            fixed, parsed, notes = M.repair(lines, today)
        else:
            fixed, parsed, notes = lines, M.parse_mrz(lines, today), []
        score = sum(c["ok"] for c in parsed["checks"]) if parsed.get("checks") else -1
        out["attempts"].append({"variant": name, "raw": lines, "ok_checks": score})
        if best is None or score > best[0]:
            best = (score, fixed, parsed, notes, lines)
        return bool(parsed.get("ok"))

    # Strategy 0: fixed-pitch glyph matcher.  When it reads the zone with high confidence its
    # output is taken literally (no check-digit "repair"): a failing check digit then means the
    # printed MRZ itself is inconsistent, which is exactly what must be reported.
    done = False
    try:
        glines, gmean, gmin = mrzglyph.read(crop)
    except Exception:
        glines, gmean, gmin = None, 0.0, 0.0
    if glines and gmean >= 0.72 and gmin >= 0.65:
        out["method"], out["confidence"] = "glyph", round(gmean, 3)
        consider("glyph", glines, repair=False)
        out["attempts"][-1]["score"] = round(gmean, 3)
        if gmean >= 0.8 or (best and best[2].get("ok")):
            done = True

    # Strategy 1: every MRZ line is cropped and read on its own
    for pname, pimg in ([] if done else _prep_variants(crop)):
        if pname == "adaptive":
            continue
        for lname, imgs in _line_crops(pimg if pname == "gray" else pimg):
            try:
                with (_lock if getattr(eng, 'needs_lock', False) else nullcontext()):
                    texts = [_clean_line(eng.read_line(im)) for im in imgs]
            except Exception as e:
                out["attempts"].append({"variant": f"{pname}/{lname}", "error": type(e).__name__})
                continue
            # a real MRZ line is long and mostly filler characters; anything else (printed
            # headings, address lines...) must not be padded into something MRZ-shaped
            texts = [t for t in texts if len(t) >= 24 and t.count("<") >= 3]
            if len(texts) < 2:
                out["attempts"].append({"variant": f"{pname}/{lname}", "result": "too few lines"})
                continue
            if consider(f"{pname}/{lname}", [_fit_length(t) for t in texts]):
                done = True
                break
        if done:
            break
    # Strategy 1b: fixed-pitch cell reflow when whole-line OCR could not satisfy the check digits
    if not done and (best is None or best[0] < 3 or not best[2].get("ok")):
        for pname, pimg in _prep_variants(crop):
            if pname == "adaptive":
                continue
            nl = len(split_lines(pimg))
            if nl not in (2, 3):
                continue
            imgs = _reflow_lines(pimg, 30 if nl == 3 else 44)
            if not imgs:
                continue
            try:
                with (_lock if getattr(eng, 'needs_lock', False) else nullcontext()):
                    texts = [_clean_line(eng.read_line(im)) for im in imgs]
            except Exception as e:
                out["attempts"].append({"variant": f"{pname}/reflow", "error": type(e).__name__})
                continue
            want = 30 if nl == 3 else 44
            if any(len(t) != want for t in texts):
                out["attempts"].append({"variant": f"{pname}/reflow", "result": f"cell count {[len(t) for t in texts]}"})
                continue
            if consider(f"{pname}/reflow", texts):
                done = True
                break
    # Strategy 2: whole-block OCR
    for name, variant in ([] if done else _prep_variants(crop)):
        try:
            with (_lock if getattr(eng, 'needs_lock', False) else nullcontext()):
                raw = eng.read_lines_mrz(variant)
        except Exception as e:  # engine failure on one variant
            out["attempts"].append({"variant": name, "error": type(e).__name__})
            continue
        lines = M.pick_mrz_lines(raw)
        if not lines:
            out["attempts"].append({"variant": name, "result": "no MRZ lines"})
            continue
        if consider(name, lines):
            break
    if best:
        _, fixed, parsed, notes, raw_lines = best
        out.update(found=True, lines=fixed, raw_lines=raw_lines, parsed=parsed, notes=notes)
        out.setdefault("method", "ocr")
    return out
