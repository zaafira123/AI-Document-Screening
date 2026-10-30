"""Identity linking across screenings: the same face under different identities, or the same
document number presented with a different face.  Only face templates are stored (never images)."""
from __future__ import annotations

import numpy as np

from . import mrz as M
from .result import finding, result
from .watchlist import name_similarity


def _emb(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def register(db, case_id: str, people: list[dict], engine, ts: str):
    for p in people:
        if p.get("embedding") is None:
            continue
        db.x("INSERT INTO identities(case_id,name_norm,dob,doc_number,doc_type,embedding,engine,created_at) VALUES(?,?,?,?,?,?,?,?)",
             (case_id, M.normalise_name(p.get("name", "")), p.get("dob"), "".join(c for c in (p.get("number") or "").upper() if c.isalnum()),
              p.get("doc_type"), p["embedding"].astype(np.float32).tobytes(), engine.kind, ts))


def check(db, case_id: str, people: list[dict], engine, name_threshold: float = 0.75) -> dict:
    fs, links = [], []
    rows = db.q("SELECT * FROM identities WHERE engine=? AND case_id<>?", (engine.kind, case_id))
    for p in people:
        if p.get("embedding") is None:
            continue
        pname = M.normalise_name(p.get("name", ""))
        pnum = "".join(c for c in (p.get("number") or "").upper() if c.isalnum())
        for r in rows:
            sim = engine.similarity(p["embedding"], _emb(r["embedding"]))
            same_face = sim >= getattr(engine, 'link_threshold', engine.match_threshold)
            diff_face = sim <= engine.mismatch_threshold
            same_name = name_similarity(pname, r["name_norm"]) >= name_threshold if (pname and r["name_norm"]) else False
            same_dob = bool(p.get("dob") and r["dob"] and p["dob"] == r["dob"])
            same_num = bool(pnum and r["doc_number"] and pnum == r["doc_number"])
            if same_face and not (same_name and same_dob) and not same_num:
                links.append({"type": "same_face_different_identity", "case": r["case_id"], "similarity": round(float(sim), 3),
                              "other_name": r["name_norm"], "other_dob": r["dob"]})
                fs.append(finding("critical", "ID_SAME_FACE_DIFF_IDENTITY",
                                  f"This face matches a traveller screened earlier in case {r['case_id']} ({r['name_norm'].title()}, born {r['dob']}) "
                                  f"but appears here under a different identity ({pname.title()}, born {p.get('dob')}). Possible alias."))
            elif same_num and diff_face:
                links.append({"type": "same_number_different_face", "case": r["case_id"], "similarity": round(float(sim), 3)})
                fs.append(finding("high", "ID_NUMBER_DIFF_FACE",
                                  f"Document number {p.get('number')} was screened earlier in case {r['case_id']} with a different face. "
                                  f"Possible document sharing or substitution."))
            elif same_face and same_name and same_dob:
                links.append({"type": "repeat_traveller", "case": r["case_id"], "similarity": round(float(sim), 3)})
    uniq, seen = [], set()
    for f in fs:
        if f["text"] not in seen:
            seen.add(f["text"])
            uniq.append(f)
    repeats = sorted({l["case"] for l in links if l["type"] == "repeat_traveller"})
    if repeats and not uniq:
        uniq.append(finding("info", "ID_REPEAT_TRAVELLER", f"Same traveller (face, name, date of birth) was screened before: {', '.join(repeats[:3])}."))
    return result(uniq, {"compared_with": len(rows), "links": links})
