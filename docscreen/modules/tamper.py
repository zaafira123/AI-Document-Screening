"""Pixel-level tampering analysis.

Three independent consistency tests, each comparing a region with the rest of the same image:
  1. text sharpness   - characters typed or pasted after capture have crisper edges than
                        characters that went through the scanner/camera with the page
  2. local noise      - retouched or patched areas have less sensor noise than the page
  3. photo statistics - a substituted portrait carries a different noise/compression
                        fingerprint than the printed page around it
plus an Error-Level-Analysis heat-map (visual aid for the officer, not scored on its own).

All three are heuristics tuned on a synthetic specimen set - see README "Limitations".
"""
from __future__ import annotations

import base64
import io

import cv2
import numpy as np
from PIL import Image

from . import fields as FLD
from .result import finding, result

KEY_FIELDS = {"surname": "Surname", "given": "Given names", "dob": "Date of birth", "expiry": "Date of expiry",
              "issue": "Date of issue", "number": "Document number", "visa_no": "Visa number", "passport_no": "Passport number",
              "pob": "Place of birth", "nationality": "Nationality", "sex": "Sex"}


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _text_features(g, res, global_noise, box):
    x, y, w, h = box
    x0, y0 = max(0, x - 3), max(0, y - 3)
    x1, y1 = min(g.shape[1], x + w + 3), min(g.shape[0], y + h + 3)
    roi, r = g[y0:y1, x0:x1], res[y0:y1, x0:x1]
    if roi.size < 200:
        return None
    med = np.median(roi)
    ink = roi < med - 45
    if ink.sum() < 30:
        return None
    ink_d = cv2.dilate(ink.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    bg = ~ink_d
    noise = float(np.median(np.abs(r[bg])) / global_noise) if bg.sum() > 50 else None
    gm = np.hypot(cv2.Sobel(roi, cv2.CV_32F, 1, 0), cv2.Sobel(roi, cv2.CV_32F, 0, 1))
    contrast = np.percentile(roi, 95) - np.percentile(roi, 3) + 1e-6
    return {"acuity": float(np.percentile(gm, 98) / contrast), "noise": noise}


def text_consistency(img_bgr, boxes: list[dict], fields: dict, exclude: list[list[int]] | None = None) -> list[dict]:
    """Find text boxes whose sharpness / noise deviates from boxes of the same size on the same page."""
    g = _gray(img_bgr)
    res = g - cv2.GaussianBlur(g, (0, 0), 1.0)
    gl = float(np.median(np.abs(res))) + 1e-6
    names = {}
    for k, f in fields.items():
        if f.get("bbox") and k in KEY_FIELDS:
            names[tuple(f["bbox"])] = KEY_FIELDS[k]
    cand = []
    for b in boxes:
        txt = b["text"].strip()
        if b["h"] < 17 or b["w"] < 40 or b["conf"] < 55 or sum(c.isalnum() for c in txt) < 3 or "<<" in txt:
            continue
        if FLD.is_document_label(txt) or FLD._NUMBERING.match(txt) or "/" in txt or "signature" in txt.lower():
            continue  # printed labels are small fixed text; compare values with values
        if exclude and any(b["x"] >= e[0] - 5 and b["y"] >= e[1] - 5 and b["x"] + b["w"] <= e[0] + e[2] + 5 and b["y"] + b["h"] <= e[1] + e[3] + 5 for e in exclude):
            continue
        ft = _text_features(g, res, gl, (b["x"], b["y"], b["w"], b["h"]))
        if ft:
            cand.append((b, ft))
    out = []
    for b, ft in cand:
        peers = [f for bb, f in cand if bb is not b and abs(bb["h"] - b["h"]) <= 0.3 * b["h"]]
        if len(peers) < 4:
            continue
        pa = np.array([p["acuity"] for p in peers])
        med, mad = float(np.median(pa)), float(np.median(np.abs(pa - np.median(pa)))) * 1.4826
        delta = ft["acuity"] - med
        z = delta / max(mad, 0.02)
        rel = delta / max(med, 1e-6)
        pn = [p["noise"] for p in peers if p["noise"] is not None]
        noise_ratio = (ft["noise"] / np.median(pn)) if (ft["noise"] is not None and pn) else None
        reasons = []
        if delta >= 0.15 and rel >= 0.05 and z >= 3.5:
            reasons.append(f"edges are {rel:.0%} sharper than comparable text on the page (typed or pasted after capture)")
        if noise_ratio is not None and (noise_ratio < 0.35 or (reasons and noise_ratio < 0.6)):
            reasons.append(f"background noise is only {noise_ratio:.0%} of the page level (retouched area)")
        if reasons:
            bbox = [b["x"], b["y"], b["w"], b["h"]]
            label = names.get(tuple(bbox)) or f"text '{b['text'][:24]}'"
            out.append(finding("high", "TMP_TEXT_SHARPNESS" if "sharp" in reasons[0] else "TMP_TEXT_NOISE",
                               f"{label}: " + "; ".join(reasons) + ".", bbox, z=round(float(z), 1)))
    return out


def photo_consistency(img_bgr, face_box) -> tuple[list[dict], dict]:
    """Compare the noise level of the portrait region with the rest of the page."""
    g = _gray(img_bgr)
    res = g - cv2.GaussianBlur(g, (0, 0), 1.0)
    gl = float(np.median(np.abs(res))) + 1e-6
    x, y, w, h = face_box
    X0, Y0 = max(0, x - int(0.6 * w)), max(0, y - int(0.5 * h))
    X1, Y1 = min(g.shape[1], x + int(1.6 * w)), min(g.shape[0], y + int(1.8 * h))
    r = res[Y0:Y1, X0:X1]
    B = 8
    vals = [np.median(np.abs(r[i:i + B, j:j + B])) for i in range(0, r.shape[0] - B, B) for j in range(0, r.shape[1] - B, B)]
    if len(vals) < 20:
        return [], {}
    ratio = float(np.median(vals) / gl)
    det = {"photo_noise_ratio": round(ratio, 2)}
    fs = []
    if ratio < 1.15:
        fs.append(finding("high", "TMP_PHOTO_NOISE",
                          f"Portrait carries {ratio:.0%} of the page's noise level - it looks like it was inserted from a different source.",
                          [X0, Y0, X1 - X0, Y1 - Y0], ratio=round(ratio, 2)))
    return fs, det


def ela_heatmap(img_bgr, quality: int = 75, size: int = 640) -> str:
    """Error-level-analysis heat-map as a base64 PNG (visual aid)."""
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=quality)
    buf.seek(0)
    re = np.array(Image.open(buf).convert("RGB")).astype(np.float32)
    diff = np.abs(rgb.astype(np.float32) - re).max(axis=2)
    diff = cv2.GaussianBlur(diff, (0, 0), 1.2)
    top = max(float(np.percentile(diff, 99.5)), 1.0)
    heat = cv2.applyColorMap(np.clip(diff / top * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    s = size / max(heat.shape[:2])
    heat = cv2.resize(heat, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", heat)
    return "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()


def analyse(img_bgr, boxes: list[dict], fields: dict, face_box=None, mrz_region=None, with_heatmap: bool = True) -> dict:
    fs = text_consistency(img_bgr, boxes, fields, exclude=[mrz_region] if mrz_region else None)
    det = {"tests": ["text sharpness", "local noise", "photo statistics", "ELA heat-map (visual)"]}
    if face_box is not None:
        pf, pd = photo_consistency(img_bgr, face_box)
        fs += pf
        det.update(pd)
    if with_heatmap:
        det["ela_png"] = ela_heatmap(img_bgr)
    return result(fs, det)
