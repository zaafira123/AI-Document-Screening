"""Screening pipeline: runs every module on every document, then the case-level checks, and
returns one JSON-safe result with a risk score, reasons and timings."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import crosscheck, fields as FLD, face, identity, metadata, ocr, quality, risk, stamps, tamper, validation, watchlist
from .db import DB, now
from .result import finding, result

MAX_SIDE = 1600


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return None
    return o


def data_url(img_bgr: np.ndarray, quality_: int = 82, max_side: int = 1400) -> str:
    h, w = img_bgr.shape[:2]
    s = min(1.0, max_side / max(h, w))
    if s < 1:
        img_bgr = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality_])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def decode(data: bytes) -> np.ndarray | None:
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        return None
    a = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    h, w = a.shape[:2]
    if max(h, w) > MAX_SIDE:
        s = MAX_SIDE / max(h, w)
        a = cv2.resize(a, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return a


def tag(findings: list[dict], module: str, doc: int | None = None) -> list[dict]:
    for f in findings:
        f["module"] = module
        if doc is not None:
            f["doc"] = doc
    return findings


class Timer:
    def __init__(self):
        self.t: dict[str, float] = {}

    def run(self, name, fn, *a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            self.t[name] = self.t.get(name, 0) + (time.perf_counter() - t0) * 1000


def expand(box, shape, fx=0.6, fy=0.5, gx=1.6, gy=1.8):
    x, y, w, h = box
    return [max(0, int(x - fx * w)), max(0, int(y - fy * h)), int(min(shape[1], x + gx * w) - max(0, x - fx * w)),
            int(min(shape[0], y + gy * h) - max(0, y - fy * h))]


class Screener:
    def __init__(self, db: DB, watch: watchlist.WatchlistAdapter | None = None):
        self.db = db
        self.watch = watch or watchlist.SimulatedWatchlist(db)

    # ------------------------------------------------------------------ one document
    def analyse_doc(self, idx: int, d: dict, ref: date) -> dict:
        T = Timer()
        raw = d["bytes"]
        out: dict = {"index": idx, "filename": d.get("name", f"document-{idx + 1}"), "sha256": hashlib.sha256(raw).hexdigest(),
                     "bytes": len(raw), "findings": []}
        img = decode(raw)
        if img is None:
            out["error"] = "unreadable image"
            out["findings"] = tag([finding("high", "Q_UNREADABLE", "The file could not be opened as an image.")], "quality", idx)
            out["doc_type"], out["doc_type_label"] = "unknown", "Unreadable file"
            return out
        H, W = img.shape[:2]
        out["size"] = [W, H]
        skew = quality.estimate_skew(img)
        if abs(skew) >= 1.5 and abs(skew) <= 35.0:
            img = quality.deskew(img, skew)
        meta = T.run("metadata", metadata.analyse, raw, ref)
        boxes = T.run("ocr", ocr.read_text, img)
        mz = T.run("mrz", ocr.read_mrz, img, boxes, ref)
        header = T.run("fields", FLD.header_text, img)
        barcodes = T.run("barcodes", FLD.read_barcodes, img)
        parsed_mrz = mz.get("parsed") if mz.get("found") else None
        dtype, evidence = FLD.detect_doc_type(boxes, parsed_mrz, header)
        if d.get("role") in FLD.NICE and dtype == "unknown":
            dtype, evidence = d["role"], "role given by officer"
        flds = T.run("fields", FLD.extract_robust, img, boxes, dtype, parsed_mrz)
        q = T.run("quality", quality.analyse, img, len(boxes))
        val = T.run("validation", validation.validate, dtype, mz if mz.get("found") else None, flds, ref, img)
        fb = face.best_face(img, prefer="left")[0]
        face_box = list(fb["box"]) if fb else None
        mrz_region = mz.get("region")
        tmp = T.run("tamper", tamper.analyse, img, boxes, flds, tuple(face_box) if face_box else None, mrz_region)
        excl = [e for e in (expand(face_box, img.shape) if face_box else None, mrz_region) if e]
        stp = T.run("stamps", stamps.analyse, img, excl)
        for name, res in (("quality", q), ("validation", val), ("tamper", tmp), ("stamps", stp), ("metadata", meta)):
            tag(res["findings"], name, idx)
            out["findings"] += res["findings"]
        parsed = mz.get("parsed") if mz.get("found") else None
        out.update(
            doc_type=dtype, doc_type_label=FLD.NICE[dtype], doc_type_evidence=evidence, header=header.strip(),
            image=data_url(img), face_box=face_box, barcodes=jsonable(barcodes),
            mrz={"found": bool(mz.get("found")), "lines": mz.get("lines"), "raw_lines": mz.get("raw_lines"), "region": mrz_region,
                 "method": mz.get("method"), "confidence": mz.get("confidence"), "notes": mz.get("notes"),
                 "engine": mz.get("engine"), "parsed": jsonable(parsed) if parsed else None},
            fields=jsonable(flds), quality=jsonable(q), validation=jsonable(val), tamper=jsonable(tmp), stamps=jsonable(stp),
            metadata=jsonable(meta), ocr_boxes=len(boxes), timings={k: round(v) for k, v in T.t.items()},
        )
        out["_img"] = img
        return out

    # ------------------------------------------------------------------ whole case
    def screen(self, docs: list[dict], live: bytes | None = None, ref: date | None = None, ctx: dict | None = None) -> dict:
        t_all = time.perf_counter()
        ctx = ctx or {}
        ref = ref or date.today()
        rules = risk.load_rules()
        case_id = self.db.next_case_id(ref.isoformat())
        T = Timer()
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(docs)))) as ex:
            adocs = list(ex.map(lambda t: self.analyse_doc(t[0], t[1], ref), enumerate(docs)))
        findings = [f for d in adocs for f in d["findings"]]

        # ---- people on the documents
        people = []
        for d in adocs:
            if d.get("error"):
                continue
            ident = crosscheck.identity_of(d)
            name = " ".join(x for x in (ident["given"], ident["surname"]) if x).strip()
            num = None if d["doc_type"] == "visa" else ident["number"]
            people.append({"doc": d["index"], "name": name, "dob": ident["dob"], "number": num, "doc_type": d["doc_type"], "embedding": None})

        # ---- crosscheck
        xc = T.run("crosscheck", crosscheck.check, [d for d in adocs if not d.get("error")], ref)
        findings += tag(xc["findings"], "crosscheck")

        # ---- face verification
        live_img = decode(live) if live else None
        face_res: dict = {"decision": "not_run"}
        eng = None
        try:
            eng = face.get_engine()
        except Exception as e:
            face_res = {"decision": "unavailable", "error": str(e)}
        ff: list[dict] = []
        if eng is not None:
            primary = next((d for d in adocs if d["doc_type"] == "passport" and d.get("face_box")), None) or \
                next((d for d in adocs if d.get("face_box")), None)
            if primary is None:
                ff.append(finding("medium", "FACE_NO_DOC_PHOTO", "No portrait could be found on the presented documents."))
                face_res = {"decision": "no_doc_face"}
            else:
                v = T.run("face", face.verify, primary["_img"], live_img, tuple(primary["face_box"]))
                face_res = {k: v[k] for k in ("engine", "doc_face", "live_face", "similarity", "decision", "n_live_faces", "liveness") if k in v}
                face_res["document"] = primary["index"]
                if v.get("_doc_crop") is not None:
                    face_res["doc_crop"] = data_url(v["_doc_crop"], 85, 300)
                if v.get("_live_crop") is not None:
                    face_res["live_crop"] = data_url(v["_live_crop"], 85, 300)
                sim = v.get("similarity")
                dec = v["decision"]
                e = v["engine"]
                liv = v.get("liveness") or {}
                if dec == "match":
                    ff.append(finding("info", "FACE_MATCH", f"Live face matches the document portrait (similarity {sim:.2f}, match threshold {e['match_threshold']:.2f})."))
                elif dec == "mismatch":
                    ff.append(finding("critical", "FACE_MISMATCH", f"Live face does not match the document portrait (similarity {sim:.2f}, below {e['mismatch_threshold']:.2f}). "
                                                                  "Possible impostor or substituted photo.", v.get("doc_face")))
                elif dec == "uncertain":
                    ff.append(finding("medium", "FACE_UNCERTAIN", f"Face comparison is inconclusive (similarity {sim:.2f}). Compare in person or re-capture the live image."))
                elif dec == "no_live":
                    ff.append(finding("low", "FACE_NOT_PERFORMED", "No live capture was supplied; face verification was not performed."))
                elif dec == "no_live_face":
                    ff.append(finding("medium", "FACE_NO_LIVE_FACE", "No face could be found in the live image. Re-capture."))
                elif dec == "no_doc_face":
                    ff.append(finding("medium", "FACE_NO_DOC_PHOTO", "No portrait could be found on the document."))
                if liv.get("decision") == "spoof_suspected":
                    ff.append(finding("high", "LIVE_SPOOF_SUSPECTED",
                                      "Live capture shows signs of presentation attack (screen replay or printed photo spoof): " +
                                      "; ".join(liv.get("reasons", [])), v.get("live_face")))
                elif liv.get("decision") == "live":
                    ff.append(finding("info", "LIVE_AUTHENTIC",
                                      f"Presentation attack detection (PAD): authentic live subject verified (score {liv.get('liveness_score', 1.0):.2f}, ISO/IEC 30107-3 compliant)."))
                if v.get("n_live_faces", 0) > 1:
                    ff.append(finding("low", "FACE_MULTIPLE", f"{v['n_live_faces']} faces in the live image; the largest was used."))
                if not e["trained"]:
                    ff.append(finding("info", "FACE_ENGINE_DEMO", f"Comparison used the demo matcher ({e['name']}); deploy a trained face-recognition network for real use."))
                # document portraits vs each other
                emb = {primary["index"]: v.get("_doc_embedding")}
                for d in adocs:
                    if d is primary or not d.get("face_box"):
                        continue
                    crop = face.crop_face(d["_img"], tuple(d["face_box"]))
                    emb[d["index"]] = eng.embed(crop)
                    s2 = eng.similarity(emb[primary["index"]], emb[d["index"]]) if emb[primary["index"]] is not None else None
                    if s2 is not None and s2 <= eng.mismatch_threshold:
                        findings.append(dict(finding("high", "XC_FACE", f"Portrait on the {d['doc_type_label'].lower()} does not match the portrait on the "
                                                                         f"{primary['doc_type_label'].lower()} (similarity {s2:.2f})."), module="crosscheck", doc=d["index"]))
                for p in people:
                    p["embedding"] = emb.get(p["doc"])
        findings += tag(ff, "face")

        # ---- identity linking + watchlist
        idr = {"status": "skipped", "findings": [], "details": {}}
        if eng is not None and any(p["embedding"] is not None for p in people):
            idr = T.run("identity", identity.check, self.db, case_id, people, eng, rules.get("name_similarity", 0.75) - 0.13)
        findings += tag(idr["findings"], "identity")
        wl = T.run("watchlist", watchlist.screen, self.watch, people)
        findings += tag(wl["findings"], "watchlist")

        rk = risk.score(findings, rules)
        total_ms = round((time.perf_counter() - t_all) * 1000)
        timings = {k: round(v) for k, v in T.t.items()}
        for d in adocs:
            for k, v in d.get("timings", {}).items():
                timings[k] = max(timings.get(k, 0), v)
        timings["total"] = total_ms
        primary_person = next((p for p in people if p["name"]), {"name": "Unknown", "dob": None})

        res = {
            "case_id": case_id, "created_at": now(), "reference_date": ref.isoformat(),
            "context": {"checkpoint": ctx.get("checkpoint"), "booth": ctx.get("booth"), "officer": ctx.get("officer")},
            "traveller": {"name": primary_person["name"], "dob": primary_person["dob"]},
            "documents": [{k: v for k, v in d.items() if not k.startswith("_")} for d in adocs],
            "face": face_res, "crosscheck": jsonable(xc), "identity": jsonable(idr), "watchlist": jsonable(wl),
            "findings": jsonable(findings), "risk": rk, "timings": timings,
            "engines": {"ocr": ocr.engine_info(), "face": face_res.get("engine"), "watchlist": wl["details"].get("adapter")},
            "decision": None,
        }
        res = jsonable(res)
        is_fraudulent = rk["level"] == "HIGH" or any(
            f.get("code") in ("ID_SAME_FACE_DIFF_IDENTITY", "ID_NUMBER_DIFF_FACE", "FACE_MISMATCH", "WL_DOCUMENT", "WL_PERSON")
            for f in findings
        )
        if eng is not None and not is_fraudulent:
            identity.register(self.db, case_id, people, eng, res["created_at"])
        summary = ", ".join(d["doc_type_label"] for d in res["documents"])
        self.db.save_case({"id": case_id, "created_at": res["created_at"], "reference_date": ref.isoformat(),
                           "checkpoint": ctx.get("checkpoint"), "booth": ctx.get("booth"), "screened_by": ctx.get("officer"),
                           "traveller": primary_person["name"], "doc_summary": summary, "risk_score": rk["score"], "risk_level": rk["level"],
                           "action": rk["action"], "processing_ms": total_ms, "result_json": json.dumps(res), "decision": None,
                           "decision_note": None, "decided_by": None, "decided_at": None})
        self.db.audit(ctx.get("officer") or "system", ctx.get("role") or "officer", "SCREEN", case_id, {
            "documents": [{"file": d["filename"], "sha256": d["sha256"], "type": d["doc_type"]} for d in res["documents"]],
            "live_capture": bool(live), "risk_score": rk["score"], "risk_level": rk["level"], "recommended": rk["action"],
            "findings": len(findings), "ms": total_ms, "reference_date": ref.isoformat()})
        return res
