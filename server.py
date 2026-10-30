"""Flask server: JSON API + static web UI.     python server.py   ->  http://127.0.0.1:5000"""
from __future__ import annotations

import json
import os
import secrets
import statistics
import sys
from collections import Counter
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory, session

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from docscreen import report  # noqa: E402
from docscreen.modules import face, ocr, risk  # noqa: E402
from docscreen.modules.db import DB  # noqa: E402
from docscreen.modules.pipeline import Screener  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
CHECKPOINT = os.environ.get("DOCSCREEN_CHECKPOINT", "Utopia International Airport (UTI)")
BOOTH = os.environ.get("DOCSCREEN_BOOTH", "E-07")
MAX_UPLOAD_MB = 40

app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
key_file = ROOT / "storage" / "secret.key"
key_file.parent.mkdir(exist_ok=True)
if not key_file.exists():
    key_file.write_text(secrets.token_hex(32))
app.secret_key = key_file.read_text()

db = DB()
screener = Screener(db)
USERS = {u["id"]: u for u in json.loads((ROOT / "config" / "users.json").read_text())}


# ------------------------------------------------------------------ helpers
def user():
    uid = session.get("uid")
    return USERS.get(uid) if uid else None


def login_required(role: str | None = None):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **k):
            u = user()
            if not u:
                return jsonify(error="login required"), 401
            if role and u["role"] != role:
                return jsonify(error=f"{role} role required"), 403
            return fn(*a, **k)
        return wrap
    return deco


def public_user(u):
    return {"id": u["id"], "name": u["name"], "role": u["role"]}


def samples():
    try:
        return json.loads((SAMPLES / "manifest.json").read_text())
    except Exception:
        return {"reference_date": None, "samples": []}


# ------------------------------------------------------------------ pages & auth
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify(ok=True, time=datetime.now().isoformat(timespec="seconds"))


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = USERS.get(str(d.get("id", "")).strip().upper())
    if not u or u["pin"] != str(d.get("pin", "")):
        db.audit(str(d.get("id", "?"))[:20], "unknown", "LOGIN_FAILED", None, {})
        return jsonify(error="Invalid officer ID or PIN"), 401
    session["uid"] = u["id"]
    db.audit(u["id"], u["role"], "LOGIN", None, {})
    return jsonify(user=public_user(u), checkpoint=CHECKPOINT, booth=BOOTH)


@app.post("/api/logout")
def logout():
    u = user()
    if u:
        db.audit(u["id"], u["role"], "LOGOUT", None, {})
    session.clear()
    return jsonify(ok=True)


@app.get("/api/session")
def get_session():
    u = user()
    return jsonify(user=public_user(u) if u else None, checkpoint=CHECKPOINT, booth=BOOTH, today=date.today().isoformat())


# ------------------------------------------------------------------ samples
@app.get("/api/samples")
@login_required()
def api_samples():
    m = samples()
    return jsonify(reference_date=m.get("reference_date"), samples=m["samples"])


@app.get("/sample/<path:p>")
@login_required()
def sample_file(p):
    return send_from_directory(SAMPLES, p)


# ------------------------------------------------------------------ screening
@app.post("/api/screen")
@login_required()
def api_screen():
    u = user()
    files = request.files.getlist("documents")
    if not files:
        return jsonify(error="No documents uploaded"), 400
    if len(files) > 6:
        return jsonify(error="At most 6 documents per case"), 400
    docs = []
    for f in files:
        data = f.read()
        if not data:
            continue
        docs.append({"name": f.filename or "document", "bytes": data, "role": request.form.get("role_" + (f.filename or ""))})
    live = request.files.get("live")
    live_bytes = live.read() if live else None
    try:
        ref = date.fromisoformat(request.form.get("reference_date") or "") if request.form.get("reference_date") else date.today()
    except ValueError:
        return jsonify(error="reference_date must be YYYY-MM-DD"), 400
    res = screener.screen(docs, live_bytes or None, ref, {"officer": u["id"], "role": u["role"], "checkpoint": CHECKPOINT, "booth": BOOTH})
    return jsonify(res)


# ------------------------------------------------------------------ cases
@app.get("/api/cases")
@login_required()
def api_cases():
    return jsonify(cases=db.cases(int(request.args.get("limit", 300))))


@app.get("/api/cases/<cid>")
@login_required()
def api_case(cid):
    c = db.case(cid)
    if not c:
        abort(404)
    res = json.loads(c["result_json"])
    res["decision"] = ({"decision": c["decision"], "note": c["decision_note"], "by": c["decided_by"], "at": c["decided_at"]}
                       if c["decision"] else None)
    u = user()
    db.audit(u["id"], u["role"], "CASE_VIEWED", cid, {})
    return jsonify(res)


@app.post("/api/cases/<cid>/decision")
@login_required()
def api_decision(cid):
    u = user()
    c = db.case(cid)
    if not c:
        abort(404)
    if c["decision"]:
        return jsonify(error=f"A decision was already recorded by {c['decided_by']}"), 409
    d = request.get_json(silent=True) or {}
    decision, note = d.get("decision"), (d.get("note") or "").strip()
    if decision not in ("clear", "secondary", "reject"):
        return jsonify(error="decision must be clear, secondary or reject"), 400
    override = (decision == "clear" and c["risk_level"] == "HIGH") or (decision == "reject" and c["risk_level"] == "LOW")
    if decision == "reject" and not note:
        return jsonify(error="A note is required when refusing entry"), 400
    if override and not note:
        return jsonify(error="A note is required when the decision departs from the system recommendation"), 400
    if len(note) > 1000:
        return jsonify(error="Note is too long (max 1000 characters)"), 400
    db.decide(cid, decision, note, u["id"])
    rec = db.audit(u["id"], u["role"], "DECISION", cid, {"decision": decision, "note": note, "system_level": c["risk_level"],
                                                          "system_action": c["action"], "override": override})
    return jsonify(ok=True, decision=decision, override=override, audit_id=rec["id"])


@app.get("/report/<cid>.pdf")
@login_required()
def api_report(cid):
    c = db.case(cid)
    if not c:
        abort(404)
    u = user()
    pdf = report.build(c, db.audit_rows(100, cid))
    db.audit(u["id"], u["role"], "REPORT_EXPORTED", cid, {})
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename={cid}.pdf"})


# ------------------------------------------------------------------ audit
@app.get("/api/audit")
@login_required()
def api_audit():
    rows = db.audit_rows(int(request.args.get("limit", 300)), request.args.get("case") or None)
    for r in rows:
        try:
            r["details"] = json.loads(r["details"])
        except Exception:
            pass
    return jsonify(rows=rows, verify=db.verify_audit())


@app.get("/api/audit/verify")
@login_required()
def api_audit_verify():
    u = user()
    v = db.verify_audit()
    db.audit(u["id"], u["role"], "AUDIT_VERIFIED", None, {"ok": v["ok"], "records": v["count"]})
    return jsonify(v)


@app.post("/api/audit/demo-tamper")
@login_required("supervisor")
def audit_tamper():
    """Demonstration only: silently edit one stored audit record so that verification fails."""
    rows = db.q("SELECT id FROM audit ORDER BY id")
    if len(rows) < 3:
        return jsonify(error="Not enough audit records yet"), 400
    rid = rows[len(rows) // 2]["id"]
    old = db.q("SELECT details FROM audit WHERE id=?", (rid,))[0]["details"]
    (ROOT / "storage" / "tamper_backup.json").write_text(json.dumps({"id": rid, "details": old}))
    db.x("UPDATE audit SET details=? WHERE id=?", (old.replace('"', "'", 1) if '"' in old else old + " ", rid))
    return jsonify(ok=True, record=rid)


@app.post("/api/audit/demo-restore")
@login_required("supervisor")
def audit_restore():
    p = ROOT / "storage" / "tamper_backup.json"
    if not p.exists():
        return jsonify(error="Nothing to restore"), 400
    b = json.loads(p.read_text())
    db.x("UPDATE audit SET details=? WHERE id=?", (b["details"], b["id"]))
    p.unlink()
    return jsonify(ok=True)


# ------------------------------------------------------------------ analytics
@app.get("/api/analytics")
@login_required()
def api_analytics():
    cases = db.q("SELECT id,created_at,risk_score,risk_level,action,processing_ms,decision,result_json FROM cases")
    n = len(cases)
    levels = Counter(c["risk_level"] for c in cases)
    decisions = Counter(c["decision"] or "pending" for c in cases)
    codes: Counter = Counter()
    hours: Counter = Counter()
    times = []
    overrides = decided = 0
    expected = {"LOW": "clear", "MEDIUM": "secondary", "HIGH": "secondary"}
    for c in cases:
        try:
            res = json.loads(c["result_json"])
            codes.update({f["code"] for f in res["findings"] if f["severity"] != "info"})
        except Exception:
            pass
        hours[(c["created_at"] or "")[11:13] or "--"] += 1
        times.append(c["processing_ms"] or 0)
        if c["decision"]:
            decided += 1
            if c["decision"] != expected[c["risk_level"]] and not (c["risk_level"] == "MEDIUM" and c["decision"] == "clear"):
                overrides += 1
    times.sort()
    p95 = times[min(len(times) - 1, int(0.95 * len(times)))] if times else 0
    return jsonify(
        total=n, levels={k: levels.get(k, 0) for k in ("LOW", "MEDIUM", "HIGH")}, decisions=dict(decisions),
        avg_score=round(statistics.mean(c["risk_score"] for c in cases), 1) if cases else 0,
        avg_ms=round(statistics.mean(times)) if times else 0, p95_ms=p95,
        top_codes=[{"code": k, "count": v} for k, v in codes.most_common(10)],
        per_hour=[{"hour": h, "count": hours[h]} for h in sorted(hours)],
        decided=decided, overrides=overrides, override_rate=round(overrides / decided, 3) if decided else 0)


# ------------------------------------------------------------------ rules
@app.get("/api/rules")
@login_required()
def get_rules():
    return jsonify(rules=risk.load_rules(), default=json.loads(risk.DEFAULT_PATH.read_text()))


@app.put("/api/rules")
@login_required("supervisor")
def put_rules():
    u = user()
    try:
        clean = risk.save_rules(request.get_json(silent=True) or {})
    except (ValueError, KeyError, TypeError) as e:
        return jsonify(error=str(e)), 400
    db.audit(u["id"], u["role"], "RULES_CHANGED", None, {"rules": clean})
    return jsonify(rules=clean)


@app.post("/api/rules/reset")
@login_required("supervisor")
def reset_rules():
    u = user()
    r = risk.reset_rules()
    db.audit(u["id"], u["role"], "RULES_RESET", None, {})
    return jsonify(rules=r)


# ------------------------------------------------------------------ integration + demo
@app.get("/api/integration")
@login_required()
def integration():
    try:
        feng = face.get_engine()
        fe = {"name": feng.name, "kind": feng.kind, "trained": feng.trained, "match_threshold": feng.match_threshold,
              "mismatch_threshold": feng.mismatch_threshold}
    except Exception as e:
        fe = {"name": None, "error": str(e)}
    return jsonify(ocr=ocr.engine_info(), face=fe, face_detector=face.detector_name(), watchlist=screener.watch.status(),
                   audit=db.verify_audit(), database=str(db.path.replace(str(ROOT), ".")),
                   adapters=[
                       {"name": "Passport / ID scanner (page images)", "type": "input", "status": "file upload in prototype", "protocol": "HTTP multipart"},
                       {"name": "Live camera at the booth", "type": "input", "status": "browser camera / upload", "protocol": "getUserMedia"},
                       {"name": "National alert list / lost-stolen registry", "type": "lookup", "status": "SIMULATED (SQLite)", "protocol": "adapter interface"},
                       {"name": "ICAO PKD / chip (eMRTD) authentication", "type": "lookup", "status": "not in prototype - planned", "protocol": "ICAO 9303 part 11"},
                       {"name": "APIS / PNR / border management system", "type": "output", "status": "not in prototype - JSON result is the interface", "protocol": "REST / JSON"}])


@app.post("/api/demo/reset")
@login_required("supervisor")
def demo_reset():
    u = user()
    db.reset_demo()
    db.audit(u["id"], u["role"], "DEMO_RESET", None, {})
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Document screening prototype -> http://127.0.0.1:{port}")
    print("OCR :", ocr.engine_info())
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
