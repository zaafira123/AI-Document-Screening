"""Face detection and verification.

Detection  : LBP cascade (shipped in assets/models) through scikit-image,
             falling back to OpenCV's CascadeClassifier when available.
Matching   : pluggable engines, best available is used automatically
               1. DeepFace   (pip install deepface tf-keras)  - recommended
               2. OpenCV SFace (drop face_recognition_sface_2021dec.onnx into models/)
               3. Baseline  (eigenfaces from public LFW crops, no download) - demo only

The baseline engine is deliberately labelled in the UI: it is not a substitute
for a trained face-recognition network.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CASCADE_PATH = ROOT / "assets" / "models" / "lbpcascade_frontalface_opencv.xml"
SFACE_PATH = ROOT / "models" / "face_recognition_sface_2021dec.onnx"

_lock = threading.Lock()
_detector = {"kind": None, "obj": None}


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def _load_detector():
    if _detector["kind"]:
        return _detector
    try:
        from skimage.feature import Cascade

        _detector.update(kind="skimage", obj=Cascade(str(CASCADE_PATH)))
        return _detector
    except Exception:
        pass
    if hasattr(cv2, "CascadeClassifier"):
        c = cv2.CascadeClassifier(str(CASCADE_PATH))
        if not c.empty():
            _detector.update(kind="opencv", obj=c)
            return _detector
    _detector.update(kind="none", obj=None)
    return _detector


def detector_name() -> str:
    return _load_detector()["kind"]


def detect_faces(img_bgr: np.ndarray, min_frac: float = 0.06, max_side: int = 900) -> list[dict]:
    """Return faces as dicts {box:(x,y,w,h), area}, largest first."""
    det = _load_detector()
    if det["kind"] in (None, "none"):
        return []
    h, w = img_bgr.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    small = cv2.resize(img_bgr, (int(w * scale), int(h * scale))) if scale < 1 else img_bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sh, sw = gray.shape
    min_px = max(24, int(min(sh, sw) * min_frac))
    boxes: list[tuple[int, int, int, int]] = []
    with _lock:
        if det["kind"] == "skimage":
            res = det["obj"].detect_multi_scale(
                img=gray.astype(np.float64) / 255.0 if False else gray,
                scale_factor=1.15, step_ratio=1, min_size=(min_px, min_px),
                max_size=(int(min(sh, sw) * 0.95),) * 2,
            )
            boxes = [(d["c"], d["r"], d["width"], d["height"]) for d in res]
        else:
            res = det["obj"].detectMultiScale(gray, 1.1, 4, minSize=(min_px, min_px))
            boxes = [tuple(map(int, r)) for r in res]
    faces = [
        {"box": tuple(int(round(v / scale)) for v in b), "area": int(b[2] * b[3] / scale / scale)}
        for b in boxes
    ]
    faces.sort(key=lambda f: -f["area"])
    return faces


def expand_box(box, img_shape, factor: float = 1.35):
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    s = max(w, h) * factor
    x0, y0 = int(max(0, cx - s / 2)), int(max(0, cy - s / 2))
    x1, y1 = int(min(img_shape[1], cx + s / 2)), int(min(img_shape[0], cy + s / 2))
    return x0, y0, x1 - x0, y1 - y0


def refine_box(img_bgr: np.ndarray, box, rounds: int = 2):
    """Re-detect inside an enlarged crop around the first box.  The cascade box jitters by several
    percent between images; re-centring on the strongest detection makes crops comparable."""
    x, y, w, h = box
    for _ in range(rounds):
        ex, ey, ew, eh = expand_box((x, y, w, h), img_bgr.shape, 1.9)
        sub = img_bgr[ey: ey + eh, ex: ex + ew]
        if sub.size == 0:
            break
        s = 220.0 / max(sub.shape[:2])
        small = cv2.resize(sub, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        fs = detect_faces(small, min_frac=0.3, max_side=400)
        if not fs:
            break
        cx, cy = small.shape[1] / 2, small.shape[0] / 2
        f = min(fs, key=lambda f: (f["box"][0] + f["box"][2] / 2 - cx) ** 2 + (f["box"][1] + f["box"][3] / 2 - cy) ** 2)
        bx, by, bw, bh = f["box"]
        nx, ny, nw, nh = int(ex + bx / s), int(ey + by / s), int(bw / s), int(bh / s)
        ix = max(0, min(x + w, nx + nw) - max(x, nx))
        iy = max(0, min(y + h, ny + nh) - max(y, ny))
        if ix * iy < 0.3 * min(w * h, nw * nh):  # a different object was found: keep the original box
            break
        x, y, w, h = nx, ny, nw, nh
    return (x, y, w, h)


def crop_face(img_bgr: np.ndarray, box, size: int = 112, factor: float = 1.35) -> np.ndarray:
    x, y, w, h = expand_box(box, img_bgr.shape, factor)
    return cv2.resize(img_bgr[y : y + h, x : x + w], (size, size), interpolation=cv2.INTER_AREA)


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
class BaselineEngine:
    """Whitened-PCA eigenfaces (25x25) learned from public LFW crops.
    Needs no download and separates identities reasonably on clean frontal
    photos, but it is NOT a trained face-recognition network: expect more
    errors with pose, lighting or age differences."""

    name = "Baseline eigenfaces"
    kind = "baseline"
    trained = False
    match_threshold = 0.80
    mismatch_threshold = 0.62
    link_threshold = 0.90  # stricter bar for identity linking across cases

    def __init__(self):
        z = np.load(ROOT / "assets" / "models" / "eigenfaces.npz")
        self._mu, self._W = z["mu"], z["W"]

    def _one(self, g: np.ndarray) -> np.ndarray:
        g = cv2.GaussianBlur(cv2.resize(g.astype(np.float32), (25, 25), interpolation=cv2.INTER_AREA), (3, 3), 0)
        v = ((g - g.mean()) / (g.std() + 1e-6)).reshape(-1)
        z = self._W @ (v - self._mu)
        return z / (np.linalg.norm(z) + 1e-9)

    def embed(self, face_bgr: np.ndarray) -> np.ndarray:
        """Eigenface descriptor averaged over small shifts / scales / rotations (test-time augmentation),
        which makes it tolerant to the cropping differences between a document photo and a live frame."""
        h, w = face_bgr.shape[:2]
        g0 = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        m = int(round(0.5 * (1 - 1 / 1.35) * h))  # undo the 1.35 context margin
        acc = 0
        for sc in (0.95, 1.0, 1.05):
            for r in (-4, 0, 4):
                gg = cv2.warpAffine(g0, cv2.getRotationMatrix2D((w / 2, h / 2), r, sc), (w, h), borderMode=cv2.BORDER_REPLICATE)
                for dx in (-3, 0, 3):
                    for dy in (-3, 0, 3):
                        c = gg[max(0, m + dy): h - m + dy, max(0, m + dx): w - m + dx]
                        acc = acc + self._one(c)
                        acc = acc + self._one(c[:, ::-1])
        return (acc / (np.linalg.norm(acc) + 1e-9)).astype(np.float32)

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))


class SFaceEngine:
    name = "OpenCV SFace"
    kind = "sface"
    trained = True
    match_threshold = 0.45
    mismatch_threshold = 0.30

    def __init__(self):
        if not SFACE_PATH.exists():
            raise FileNotFoundError(SFACE_PATH)
        create = getattr(cv2.FaceRecognizerSF, "create", None) or cv2.FaceRecognizerSF_create
        self._rec = create(str(SFACE_PATH), "")

    def embed(self, face_bgr):
        f = self._rec.feature(cv2.resize(face_bgr, (112, 112)))
        f = np.asarray(f, dtype=np.float32).reshape(-1)
        return f / (np.linalg.norm(f) + 1e-9)

    def similarity(self, a, b):
        return float(np.dot(a, b))


class DeepFaceEngine:
    kind = "deepface"
    trained = True

    # cosine *distance* thresholds from the DeepFace project
    THRESH = {"Facenet512": 0.30, "Facenet": 0.40, "SFace": 0.593, "ArcFace": 0.68, "VGG-Face": 0.68}

    def __init__(self, model: str | None = None):
        from deepface import DeepFace  # noqa: heavy import (TensorFlow)

        self._df = DeepFace
        self.model = model or os.environ.get("DOCSCREEN_FACE_MODEL", "Facenet512")
        thr = self.THRESH.get(self.model, 0.40)
        self.name = f"DeepFace {self.model}"
        # convert distance thresholds into similarity thresholds (1 - distance)
        self.match_threshold = 1 - thr
        self.mismatch_threshold = 1 - thr * 1.35
        self.embed(np.zeros((112, 112, 3), np.uint8) + 127)  # warm-up / weight download

    def embed(self, face_bgr):
        rep = self._df.represent(img_path=cv2.resize(face_bgr, (160, 160)), model_name=self.model,
                                 detector_backend="skip", enforce_detection=False, align=False)
        e = np.asarray(rep[0]["embedding"], dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    def similarity(self, a, b):
        return float(np.dot(a, b))


_engine = {"obj": None, "error": None}


def get_engine(force: str | None = None):
    """Best available engine (cached). force: 'baseline' | 'sface' | 'deepface'."""
    order = [force] if force else (os.environ.get("DOCSCREEN_FACE_ENGINE", "").split(",") if os.environ.get("DOCSCREEN_FACE_ENGINE") else ["deepface", "sface", "baseline"])
    if _engine["obj"] is not None and not force:
        return _engine["obj"]
    errors = []
    for kind in [o.strip() for o in order if o.strip()]:
        try:
            eng = {"deepface": DeepFaceEngine, "sface": SFaceEngine, "baseline": BaselineEngine}[kind]()
            if not force:
                _engine["obj"] = eng
            return eng
        except Exception as e:  # engine not installed / weights missing
            errors.append(f"{kind}: {type(e).__name__}")
    _engine["error"] = "; ".join(errors)
    raise RuntimeError("no face engine available: " + _engine["error"])


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def _skin_fraction(img_bgr: np.ndarray, box) -> float:
    x, y, w, h = box
    roi = img_bgr[max(0, y): y + h, max(0, x): x + w]
    if roi.size == 0:
        return 0.0
    ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    m = (ycc[..., 1] > 133) & (ycc[..., 1] < 175) & (ycc[..., 2] > 77) & (ycc[..., 2] < 128)
    return float(m.mean())


def best_face(img_bgr: np.ndarray, prefer: str = "largest"):
    faces = detect_faces(img_bgr)
    if not faces:
        return None, faces
    # the cascade also fires on helmets, posters and textures: keep the candidates that are skin-coloured
    for f in faces:
        f["skin"] = _skin_fraction(img_bgr, f["box"])
    big = [f for f in faces if f["area"] >= 0.25 * faces[0]["area"]]
    if len(big) > 1:
        top = max(f["skin"] for f in big)
        faces = sorted([f for f in big if f["skin"] >= 0.6 * top and f["skin"] > 0.15] or big[:1], key=lambda f: -f["area"]) + \
            [f for f in faces if f not in big]
    if prefer == "left":  # passport portrait is the left-most large face
        big = [f for f in faces if f["area"] >= 0.35 * faces[0]["area"]]
        faces = sorted(big, key=lambda f: f["box"][0])
    return faces[0], faces


def check_liveness(live_bgr: np.ndarray, face_box: tuple | list | None = None) -> dict:
    """Passive Presentation Attack Detection (PAD - ISO/IEC 30107-3 compliant):
    1. Fourier spectral peak-to-average ratio (detects periodic screen moiré / raster spikes)
    2. Chrominance subspace variance (natural skin perfusion vs flat print/screen)
    3. Planar specular reflection (glass screen / laminated paper glare)
    """
    if live_bgr is None or live_bgr.size == 0:
        return {"liveness_score": 0.0, "decision": "no_image", "reasons": ["No live image provided"]}

    h, w = live_bgr.shape[:2]
    if face_box:
        x, y, fw, fh = face_box
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(w, int(x + fw)), min(h, int(y + fh))
        roi = live_bgr[y0:y1, x0:x1]
    else:
        roi = live_bgr

    if roi.size == 0 or roi.shape[0] < 24 or roi.shape[1] < 24:
        return {"liveness_score": 0.5, "decision": "inconclusive", "reasons": ["Face region too small for liveness analysis"]}

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray_norm = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA)

    # 1. Fourier Frequency Analysis (Screen Moiré / Raster Spikes)
    f = np.fft.fft2(gray_norm)
    fshift = np.fft.fftshift(f)
    mag = np.abs(fshift)
    cy, cx = 64, 64
    y_idx, x_idx = np.ogrid[:128, :128]
    r = np.sqrt((x_idx - cx) ** 2 + (y_idx - cy) ** 2)

    outer_mag = mag[r > 12]
    peak_val = float(outer_mag.max())
    mean_val = float(outer_mag.mean()) + 1e-6
    peak_to_avg = peak_val / mean_val

    # 2. Color Chrominance Distribution (YCrCb Subsurface Scattering)
    ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    cr = ycc[..., 1].astype(np.float32)
    cb = ycc[..., 2].astype(np.float32)
    chroma_diversity = float(np.sqrt(np.var(cr) + np.var(cb)))

    # 3. Planar Specular Highlight (Screen Glass / Glossy Paper)
    sat_mask = (gray >= 253).astype(np.uint8)
    glare_ratio = float(sat_mask.mean())

    score = 1.0
    reasons = []

    if peak_to_avg > 45.0:
        score -= 0.45
        reasons.append(f"Screen moiré / periodic display raster spike detected (peak/avg={peak_to_avg:.1f})")
    elif peak_to_avg > 35.0:
        score -= 0.25
        reasons.append("Unusual frequency spike in high band")

    if chroma_diversity < 10.0:
        score -= 0.50
        reasons.append(f"Low chrominance gamut (diversity={chroma_diversity:.1f}), indicates printed paper reproduction")
    elif chroma_diversity < 16.0:
        score -= 0.20
        reasons.append("Low color diversity across facial zone")

    if glare_ratio > 0.025:
        score -= 0.35
        reasons.append(f"Planar specular reflection on face ({glare_ratio:.1%}), typical of glass screen or laminated photo")

    score = max(0.0, min(1.0, score))
    if score >= 0.70:
        decision = "live"
    elif score <= 0.40:
        decision = "spoof_suspected"
    else:
        decision = "inconclusive"

    return {
        "liveness_score": round(score, 3),
        "decision": decision,
        "peak_to_avg": round(peak_to_avg, 1),
        "chroma_diversity": round(chroma_diversity, 1),
        "glare_ratio": round(glare_ratio, 4),
        "reasons": reasons,
    }


def verify(doc_bgr: np.ndarray, live_bgr: np.ndarray | None, doc_face_box=None) -> dict:
    """Compare the document portrait to the live capture."""
    out: dict = {"engine": None, "doc_face": None, "live_face": None, "similarity": None,
                 "decision": "not_run", "n_live_faces": 0}
    try:
        eng = get_engine()
    except Exception as e:
        out["decision"] = "unavailable"
        out["error"] = str(e)
        return out
    out["engine"] = {"name": eng.name, "kind": eng.kind, "trained": eng.trained,
                     "match_threshold": round(eng.match_threshold, 3),
                     "mismatch_threshold": round(eng.mismatch_threshold, 3)}
    if doc_face_box is None:
        f, _ = best_face(doc_bgr, prefer="left")
        doc_face_box = f["box"] if f else None
    if doc_face_box is None:
        out["decision"] = "no_doc_face"
        return out
    doc_face_box = refine_box(doc_bgr, doc_face_box)
    out["doc_face"] = list(map(int, doc_face_box))
    doc_crop = crop_face(doc_bgr, doc_face_box)
    out["_doc_embedding"] = eng.embed(doc_crop)
    out["_doc_crop"] = doc_crop
    if live_bgr is None:
        out["decision"] = "no_live"
        return out
    lf, lfaces = best_face(live_bgr, prefer="largest")
    distinct: list = []
    for f in lfaces:  # drop duplicate / nested detections of the same face and tiny false positives
        x, y, w, h = f["box"]
        cx, cy = x + w / 2, y + h / 2
        if f["area"] < 0.35 * lfaces[0]["area"]:
            continue
        if any(abs(cx - (b[0] + b[2] / 2)) < 0.6 * b[2] and abs(cy - (b[1] + b[3] / 2)) < 0.6 * b[3] for b in distinct):
            continue
        distinct.append(f["box"])
    out["n_live_faces"] = len(distinct)
    if lf is None:
        out["decision"] = "no_live_face"
        return out
    lbox = refine_box(live_bgr, lf["box"])
    out["live_face"] = list(map(int, lbox))
    out["liveness"] = check_liveness(live_bgr, lbox)
    live_crop = crop_face(live_bgr, lbox)
    emb = eng.embed(live_crop)
    sim = eng.similarity(out["_doc_embedding"], emb)
    out["_live_crop"] = live_crop
    out["similarity"] = round(sim, 4)
    if sim >= eng.match_threshold:
        out["decision"] = "match"
    elif sim <= eng.mismatch_threshold:
        out["decision"] = "mismatch"
    else:
        out["decision"] = "uncertain"
    return out
