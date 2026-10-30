"""Stamp analysis: locate ink stamps, flag cloned stamps and stamps that lack real ink texture."""
from __future__ import annotations

import cv2
import numpy as np

from .result import finding, result


def find_stamps(img_bgr: np.ndarray, exclude: list[list[int]] | None = None) -> list[dict]:
    H, W = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    ink = ((hsv[..., 1] > 60) & (hsv[..., 2] < 225)).astype(np.uint8)
    for e in exclude or []:
        x, y, w, h = e
        ink[max(0, y - 8): y + h + 8, max(0, x - 8): x + w + 8] = 0
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 70 or h < 55 or w > 0.45 * W or h > 0.45 * H:
            continue
        if not 0.45 < w / h < 2.2:
            continue
        core = ink[y:y + h, x:x + w]
        fill = float(core.mean())
        if fill > 0.40 or fill < 0.03:  # photos are dense, stray colour is sparse; stamps are line art
            continue
        s = hsv[y:y + h, x:x + w, 1].astype(np.float32)
        m = core.astype(bool)
        inner = cv2.erode(core, np.ones((3, 3), np.uint8)).astype(bool)
        vals = s[inner] if inner.sum() > 40 else s[m]
        cv = float(vals.std() / (vals.mean() + 1e-6))
        out.append({"bbox": [int(x), int(y), int(w), int(h)], "fill": round(fill, 3), "ink_cv": round(cv, 3),
                    "hue": int(np.median(hsv[y:y + h, x:x + w, 0][m]))})
    out.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
    return out


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:h, :w].astype(np.float32).ravel(), b[:h, :w].astype(np.float32).ravel()
    a, b = a - a.mean(), b - b.mean()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def _orb_clone(a: np.ndarray, b: np.ndarray) -> float:
    """Cloned-and-rotated/scaled stamp: align a onto b with matched feature points and return the
    correlation of the aligned crops (0 when no consistent alignment exists)."""
    orb = cv2.ORB_create(600)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return 0.0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [m[0] for m in matches if len(m) == 2 and m[0].distance < 0.7 * m[1].distance]
    if len(good) < 12:
        return 0.0
    src = np.float32([ka[m.queryIdx].pt for m in good])
    dst = np.float32([kb[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None or inl.sum() < 12:
        return 0.0
    warped = cv2.warpAffine(a, M, (b.shape[1], b.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    return _ncc(warped, b)


def _acuity(gray: np.ndarray, box) -> float:
    x, y, w, h = box
    roi = gray[y:y + h, x:x + w].astype(np.float32)
    gm = np.hypot(cv2.Sobel(roi, cv2.CV_32F, 1, 0), cv2.Sobel(roi, cv2.CV_32F, 0, 1))
    con = np.percentile(roi, 97) - np.percentile(roi, 1) + 1e-6
    return float(np.percentile(gm, 99.5) / con)


def analyse(img_bgr: np.ndarray, exclude: list[list[int]] | None = None) -> dict:
    stamps = find_stamps(img_bgr, exclude)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    fs = []
    ref = 0.0
    for i in range(len(stamps)):
        for j in range(i + 1, len(stamps)):
            a, b = stamps[i]["bbox"], stamps[j]["bbox"]
            ca = gray[a[1]:a[1] + a[3], a[0]:a[0] + a[2]]
            cb = gray[b[1]:b[1] + b[3], b[0]:b[0] + b[2]]
            same_size = abs(a[2] - b[2]) <= 0.06 * a[2] and abs(a[3] - b[3]) <= 0.06 * a[3]
            ncc = _ncc(ca, cb) if same_size else 0.0
            aligned = 0.0 if ncc >= 0.96 else _orb_clone(ca, cb)
            if ncc >= 0.96 or aligned >= 0.96:
                how = f"pixel correlation {max(ncc, aligned):.2f}" + ("" if ncc >= 0.96 else " after rotation/scale alignment")
                fs.append(finding("high", "STP_COPY_MOVE",
                                  f"Two stamps are identical ({how}). Genuine hand-stamped impressions are never exact copies - one was cloned.", b,
                                  other=a))
    # a stamp pasted in afterwards has crisper edges than everything that was scanned with the page
    page = gray.copy()
    for s in stamps:
        x, y, w, h = s["bbox"]
        page[y:y + h, x:x + w] = int(np.median(gray))
    ref = _acuity(page, (0, 0, page.shape[1], page.shape[0]))
    for s in stamps:
        s["acuity"] = round(_acuity(gray, s["bbox"]), 2)
        if s["acuity"] - ref > 0.14:
            fs.append(finding("high", "STP_TOO_CLEAN",
                              f"Stamp edges are {s['acuity'] / ref - 1:.0%} sharper than the rest of the page: it was added digitally after the page was captured.",
                              s["bbox"]))
    return result(fs, {"stamps": stamps, "count": len(stamps), "page_acuity": round(ref, 2) if stamps else None})
