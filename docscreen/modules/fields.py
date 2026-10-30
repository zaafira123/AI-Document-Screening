"""Visual-inspection-zone (VIZ) field extraction from OCR boxes, and document-type detection."""
from __future__ import annotations

import re
from datetime import date

import cv2
import numpy as np

from . import ocr

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# field -> label regexes (applied to lower-case label text with any "4b." style numbering removed)

FRENCH_MONTHS = {
    "JAN": 1, "JANV": 1, "FEV": 2, "FEVR": 2, "MAR": 3, "MARS": 3, "AVR": 4, "AVRI": 4,
    "MAI": 5, "JUN": 6, "JUIN": 6, "JUL": 7, "JUIL": 7, "AOU": 8, "AOUT": 8, "AOÛ": 8, "AOÛT": 8,
    "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

LABELS: list[tuple[str, str]] = [
    ("visa_no", r"visa\s*n[o°o]?\b|n[o°o]?\s*de\s*visa"),
    ("passport_no", r"passport\s*n[o°o]?\b|n[o°o]?\s*du\s*passeport"),
    ("number", r"(document|licen[cs]e|permit)\s*n[o°o]?\b|n[o°o]?\s*du\s*document"),
    ("surname", r"\bsurname\b|\bnom\b"),
    ("given", r"given\s*names?\b|pr[eé]noms?\b"),
    ("pob", r"place\s*of\s*birth\b|lieu\s*de\s*naissance\b"),
    ("dob", r"date\s*(and\s*place\s*)?of\s*birth|date\s*de\s*naissance"),
    ("sex", r"\bsex[e]?\b"),
    ("nationality", r"\bnationality\b|\bnationalit[eé]\b"),
    ("issue", r"date\s*of\s*issue|date\s*de\s*d[eé]livrance|\bissued\b|valid\s*from|valable\s*du"),
    ("expiry", r"date\s*of\s*expiry|date\s*d['’]expiration|\bexpires\b|valid\s*until|jusqu['’]au"),
    ("entries", r"\bentries\b|\bentr[eé]es\b"),
    ("stay", r"duration\s*of\s*stay|dur[eé]e\s*du\s*s[eé]jour"),
    ("visa_type", r"type\s*/\s*(cat|type)\b|type\s*de\s*visa"),
    ("permit_type", r"permit\s*type|type\s*de\s*permis"),
]

REQUIRED = {
    "passport": ["surname", "given", "dob", "sex", "nationality", "number", "expiry"],
    "id_card": ["surname", "given", "dob", "number", "expiry"],
    "permit": ["surname", "given", "dob", "number", "expiry"],
    "driving_licence": ["surname", "given", "dob", "number", "expiry"],
    "visa": ["surname", "given", "dob", "visa_no", "passport_no", "issue", "expiry"],
}
NICE = {
    "passport": "Passport", "id_card": "National ID card", "permit": "Work permit",
    "driving_licence": "Driving licence", "visa": "Visa", "unknown": "Unknown document",
}
_NUMBERING = re.compile(r"^\s*\d{1,2}[a-z]?[.,]\s*|^\s*[a-z]{1,2}[.,]\s+", re.I)
_DATE = re.compile(r"(\d{1,2})[\s\-./]*([A-Z0-9]{3,4})[\s\-./]*(\d{4})")


def _clean_label(t: str) -> str:
    return _NUMBERING.sub("", t).strip().lower()


def label_of(text: str) -> str | None:
    t = _clean_label(text)
    for name, pat in LABELS:
        if re.search(pat, t):
            return name
    return None


def is_document_label(text: str) -> bool:
    """Return True if text is a recognized field label, a bilingual template label, or a document header."""
    if label_of(text) is not None:
        return True
    t = _clean_label(text)
    # Bilingual slash labels such as "code/code", "type/type", "authority/autorite", etc.
    if re.search(r"\b[a-z]{2,}\s*/\s*[a-z]{2,}\b", t) or ("/" in t and len(t) < 45 and not re.search(r"\d", t)):
        return True
    # Common document headers/labels that must never be assigned as field values
    if re.search(r"\b(code|type|authority|autorit[eé]|signature|republic|republique|passport|passeport|visa|specimen|immigrationservice)\b", t):
        return True
    return False


# --------------------------------------------------------------------------
# value parsing
# --------------------------------------------------------------------------
def _fix_digits(s: str) -> str:
    return s.translate(str.maketrans("OoQDIl|SsBZ", "00001115582"))


def _month(tok: str) -> int | None:
    tok = tok.upper().replace("0", "O").replace("1", "I").replace("5", "S").strip()
    if tok in FRENCH_MONTHS:
        return FRENCH_MONTHS[tok]
    if len(tok) >= 3 and tok[:3] in FRENCH_MONTHS:
        return FRENCH_MONTHS[tok[:3]]
    if tok in MONTHS:
        return MONTHS.index(tok) + 1
    best = min(range(12), key=lambda i: sum(a != b for a, b in zip(tok[:3], MONTHS[i])))
    return best + 1 if sum(a != b for a, b in zip(tok[:3], MONTHS[best])) <= 1 else None


def parse_date(text: str) -> date | None:
    t = text.upper().strip()
    m = _DATE.search(t)
    if m:
        d, mon, y = _fix_digits(m.group(1)), _month(m.group(2)), _fix_digits(m.group(3))
        if mon and d.isdigit() and y.isdigit():
            try:
                return date(int(y), mon, int(d))
            except ValueError:
                return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t) or None
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _value(field: str, raw: str) -> dict:
    raw = raw.strip()
    if field in ("dob", "issue", "expiry"):
        d = parse_date(raw)
        out = {"value": d.isoformat() if d else None, "raw": raw}
        if field == "dob":
            m = _DATE.search(raw.upper())
            rest = raw[m.end():].strip() if m else ""
            if rest:
                out["place"] = rest
        return out
    if field == "sex":
        c = raw.strip().upper()[:1]
        return {"value": c if c in "MFX" else None, "raw": raw}
    if field == "stay":
        m = re.search(r"\d+", _fix_digits(raw))
        return {"value": int(m.group()) if m else None, "raw": raw}
    if field in ("number", "visa_no", "passport_no"):
        return {"value": re.sub(r"[^A-Z0-9\-]", "", raw.upper().replace(" ", "")), "raw": raw}
    return {"value": raw.upper(), "raw": raw}


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
def header_text(img_bgr: np.ndarray) -> str:
    """Read the coloured title band (light text on a dark band) by inverting it."""
    h = img_bgr.shape[0]
    band = img_bgr[: max(60, int(h * 0.12))]
    g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    if g.mean() < 140:
        g = 255 - g
    boxes = ocr.read_text(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
    return " ".join(b["text"] for b in boxes)


def detect_doc_type(boxes: list[dict], mrz_parsed: dict | None, header: str = "") -> tuple[str, str]:
    """Return (type, evidence)."""
    blob = " ".join(b["text"].lower() for b in boxes)
    hd = header.lower()
    code = (mrz_parsed or {}).get("doc_code", "") or ""
    if "visa no" in blob or code.startswith("V") or re.search(r"\bvisa\b", hd):
        return "visa", "visa number label / MRZ code V"
    if re.search(r"licen[cs]e no", blob) or re.search(r"driving", hd + blob):
        return "driving_licence", "licence number label"
    if "permit" in blob or "permit" in hd or code.startswith(("IR", "AR")):
        return "permit", "permit label / MRZ code"
    if code.startswith("P") or "passport no" in blob or "passport" in hd:
        return "passport", "passport label / MRZ code P"
    if code.startswith(("I", "A", "C")) or "document no" in blob or "identity" in hd:
        return "id_card", "document number label / MRZ code I"
    return "unknown", "no distinguishing label found"


def extract(boxes: list[dict]) -> dict:
    """Return {field: {value, raw, bbox, conf}} found in the visual zone."""
    labelled = []
    for b in boxes:
        f = label_of(b["text"])
        if f:
            labelled.append((f, b))
    label_ids = {id(b) for _, b in labelled}
    fields: dict[str, dict] = {}
    for f, L in labelled:
        if f in fields:
            continue
        best = None
        for B in boxes:
            if id(B) in label_ids or B is L:
                continue
            if is_document_label(B["text"]):
                continue
            dy = B["y"] - L["y"]
            if dy < 0.45 * L["h"] or dy > 3.6 * max(L["h"], 14) + 12:
                continue
            # left edges aligned (values start under the label)
            if re.fullmatch(r"\d{1,2}[a-z]?[.,]?", B["text"].strip(), re.I) or B["x"] < L["x"] - 8:
                continue  # field-number tokens such as "1." / "4a." are not values
            dx = abs(B["x"] - L["x"])
            if dx > 0.55 * max(L["w"], 90) or B["x"] > L["x"] + L["w"]:
                continue
            if " / " in B["text"] and not re.search(r"\d", B["text"]):
                continue
            score = dy + 0.4 * dx
            if best is None or score < best[0]:
                best = (score, B)
        if best:
            B = best[1]
            v = _value(f, B["text"])
            v.update(bbox=[B["x"], B["y"], B["w"], B["h"]], conf=B["conf"], label=L["text"], source="visual")
            fields[f] = v
    return fields


def reread_value(img_bgr: np.ndarray, bbox: list[int], field: str) -> dict | None:
    """Second look at one value: crop it, enlarge it and OCR it again.  Used to confirm a
    mismatch before it is reported, so that a single OCR slip does not raise a false alarm."""
    x, y, w, h = bbox
    H, W = img_bgr.shape[:2]
    crop = img_bgr[max(0, y - 6) : min(H, y + h + 6), max(0, x - 8) : min(W, x + w + 24)]
    if crop.size == 0:
        return None
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    crop = cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_REPLICATE)
    boxes = ocr.read_text(crop)
    if not boxes:
        return None
    txt = " ".join(b["text"] for b in sorted(boxes, key=lambda b: b["x"]))
    v = _value(field, txt)
    return v


def extract_robust(img_bgr: np.ndarray, boxes: list[dict], doc_type: str, mrz_parsed: dict | None = None) -> dict:
    """extract() plus one retry on an enlarged copy of the page when a required field is missing,
    with MRZ fallback for missing required fields."""
    f = extract(boxes)
    if doc_type == "passport":
        if "number" not in f and "passport_no" in f:
            f["number"] = f["passport_no"]
        elif "passport_no" not in f and "number" in f:
            f["passport_no"] = f["number"]
    elif doc_type == "visa":
        if "number" not in f and "visa_no" in f:
            f["number"] = f["visa_no"]
        elif "visa_no" not in f and "number" in f:
            f["visa_no"] = f["number"]
    elif doc_type == "permit":
        if "number" not in f and "permit_no" in f:
            f["number"] = f["permit_no"]
        elif "permit_no" not in f and "number" in f:
            f["permit_no"] = f["number"]

    need = REQUIRED.get(doc_type, [])
    miss = [k for k in need if k not in f or f[k].get("value") in (None, "")]
    if miss:
        k = 1.5
        up = cv2.resize(img_bgr, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        b2 = ocr.read_text(up)
        for b in b2:
            for a in ("x", "y", "w", "h"):
                b[a] = int(round(b[a] / k))
        f2 = extract(b2)
        if doc_type == "passport" and "number" not in f2 and "passport_no" in f2:
            f2["number"] = f2["passport_no"]
        elif doc_type == "visa" and "number" not in f2 and "visa_no" in f2:
            f2["number"] = f2["visa_no"]
        elif doc_type == "permit" and "number" not in f2 and "permit_no" in f2:
            f2["number"] = f2["permit_no"]
        for name in miss:
            if name in f2 and f2[name].get("value") not in (None, ""):
                f[name] = f2[name]
                f[name]["retry"] = True

    # Fallback to parsed MRZ for any required fields still missing from visual extraction
    if mrz_parsed:
        mrz_map = {
            "surname": mrz_parsed.get("surname"),
            "given": mrz_parsed.get("given_names"),
            "dob": mrz_parsed.get("dob_iso"),
            "expiry": mrz_parsed.get("expiry_iso"),
            "sex": mrz_parsed.get("sex") if mrz_parsed.get("sex") in ("M", "F", "X") else None,
            "nationality": mrz_parsed.get("nationality"),
            "number": (mrz_parsed.get("doc_number") or "").replace("<", ""),
        }
        if doc_type == "visa":
            mrz_map["visa_no"] = mrz_map["number"]
            if mrz_parsed.get("optional"):
                mrz_map["passport_no"] = mrz_parsed.get("optional").replace("<", "")
        elif doc_type == "passport":
            mrz_map["passport_no"] = mrz_map["number"]

        for k in need:
            if (k not in f or f[k].get("value") in (None, "")) and mrz_map.get(k):
                f[k] = {
                    "value": mrz_map[k],
                    "raw": str(mrz_map[k]),
                    "source": "mrz",
                    "mrz_fallback": True,
                }
                if k == "number" and doc_type == "passport" and "passport_no" not in f:
                    f["passport_no"] = f["number"]
                elif k == "number" and doc_type == "visa" and "visa_no" not in f:
                    f["visa_no"] = f["number"]
    return f


def read_barcodes(img_bgr: np.ndarray) -> list[dict]:
    """Scan and decode 2D barcodes (QR code, Data Matrix, PDF417) from document image."""
    out = []
    # 1. QR Code
    try:
        qr = cv2.QRCodeDetector()
        retval, decoded_info, points, straight_qrcode = qr.detectAndDecodeMulti(img_bgr)
        if retval:
            for i, text in enumerate(decoded_info):
                if text and text.strip():
                    bbox = None
                    if points is not None and len(points) > i:
                        pts = points[i].astype(int)
                        bx = int(pts[:, 0].min())
                        by = int(pts[:, 1].min())
                        bw = int(pts[:, 0].max() - bx)
                        bh = int(pts[:, 1].max() - by)
                        bbox = [bx, by, bw, bh]
                    out.append({"type": "QR_CODE", "data": text.strip(), "bbox": bbox})
    except Exception:
        pass

    # 2. 1D/2D Barcodes
    try:
        if hasattr(cv2, "barcode") and hasattr(cv2.barcode, "BarcodeDetector"):
            bd = cv2.barcode.BarcodeDetector()
            ok, decoded_info, decoded_type, points = bd.detectAndDecode(img_bgr)
            if ok:
                for text, btype in zip(decoded_info, decoded_type):
                    if text and text.strip():
                        out.append({"type": str(btype), "data": text.strip(), "bbox": None})
    except Exception:
        pass
    return out

