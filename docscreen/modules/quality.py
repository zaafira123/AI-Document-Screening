"""Capture-quality checks: resolution, blur, glare, exposure, deskewing, and actionable operator guidance."""
from __future__ import annotations

import cv2
import numpy as np

from .result import finding, result


def estimate_skew(img_bgr: np.ndarray) -> float:
    """Estimate document skew angle in degrees using Hough line transform on document edges."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 800.0 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else gray
    edges = cv2.Canny(small, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=60, maxLineGap=15)
    if lines is None or len(lines) == 0:
        return 0.0
    angles = []
    for line in lines:
        coords = line.ravel()
        if len(coords) < 4:
            continue
        x1, y1, x2, y2 = coords[:4]
        dx, dy = float(x2 - x1), float(y2 - y1)
        if abs(dx) > 1e-4:
            deg = np.degrees(np.arctan2(dy, dx))
            while deg > 45:
                deg -= 90
            while deg < -45:
                deg += 90
            angles.append(deg)
    return float(np.median(angles)) if angles else 0.0


def deskew(img_bgr: np.ndarray, angle: float) -> np.ndarray:
    """Rotate image by -angle to align document horizontally."""
    if abs(angle) < 0.6 or abs(angle) > 40:
        return img_bgr
    h, w = img_bgr.shape[:2]
    center = (w / 2, h / 2)
    M = cv2.getRotationMatrix2D(center, -angle, 1.0)
    return cv2.warpAffine(img_bgr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def analyse(img_bgr: np.ndarray, n_text_boxes: int | None = None) -> dict:
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    norm = cv2.resize(gray, (1000, max(1, int(h * 1000 / w))), interpolation=cv2.INTER_AREA)
    # sharpness: Laplacian variance on the amplitude-normalised page
    lap = float(cv2.Laplacian(norm, cv2.CV_64F).var())
    # sharpest tile - a sharp document with a few blurry areas is fine, a blurry page is not
    th, tw = norm.shape[0] // 4, norm.shape[1] // 4
    tiles = [cv2.Laplacian(norm[i * th:(i + 1) * th, j * tw:(j + 1) * tw], cv2.CV_64F).var() for i in range(4) for j in range(4)]
    sharp_tiles = float(np.mean(np.array(tiles) > 60))
    sat = (gray >= 254).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sat, 8)
    glare_area = float(max((stats[i, cv2.CC_STAT_AREA] for i in range(1, n)), default=0)) / (h * w)
    mean = float(gray.mean())
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 5))
    skew_angle = round(estimate_skew(img_bgr), 2)

    guidance = []
    if glare_area > 0.02:
        guidance.append("Reposition document or shield scanner glass to remove glare reflection.")
    if sharp_tiles < 0.12 and lap < 60:
        guidance.append("Image is out of focus; press document flat against the glass and re-scan.")
    if mean < 70:
        guidance.append("Capture is too dark; increase booth illumination.")
    if abs(skew_angle) >= 2.0:
        guidance.append(f"Document is tilted by {abs(skew_angle):.1f}°; auto-deskewing applied.")
    if not guidance:
        guidance.append("Image sharpness, illumination, and geometry are optimal.")

    details = {"width": w, "height": h, "laplacian_var": round(lap, 1), "sharp_tile_fraction": round(sharp_tiles, 2),
               "glare_fraction": round(glare_area, 4), "mean_brightness": round(mean, 1), "contrast": round(contrast, 1),
               "skew_angle": skew_angle, "guidance": guidance}
    fs = []
    if min(h, w) < 380:
        fs.append(finding("medium", "Q_LOWRES", f"Image resolution is very low ({w}x{h}px); small print cannot be read reliably."))
    elif min(h, w) < 500:
        fs.append(finding("low", "Q_LOWRES", f"Image resolution is low ({w}x{h}px)."))
    if sharp_tiles < 0.12 and lap < 60:
        fs.append(finding("medium", "Q_BLUR", "Image is out of focus; fine detail and the MRZ are unreliable. Re-scan the page."))
    if glare_area > 0.02:
        fs.append(finding("low", "Q_GLARE", f"Glare / over-exposed area covers {glare_area:.1%} of the image."))
    if mean < 70:
        fs.append(finding("low", "Q_DARK", "Image is too dark."))
    elif contrast < 60:
        fs.append(finding("low", "Q_LOWCONTRAST", "Image has very low contrast."))
    if n_text_boxes is not None and n_text_boxes < 4:
        fs.append(finding("medium", "Q_NO_TEXT", "Almost no text could be read from this image."))
    details["usable"] = not any(f["severity"] == "medium" for f in fs)
    return result(fs, details)
