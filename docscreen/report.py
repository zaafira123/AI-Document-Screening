"""PDF case report (reportlab)."""
from __future__ import annotations

import base64
import io
import json

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0B3D6E")
GREY = colors.HexColor("#5A6672")
LINE = colors.HexColor("#C7CFD8")
LEVEL = {"LOW": colors.HexColor("#1E7B3A"), "MEDIUM": colors.HexColor("#B7791F"), "HIGH": colors.HexColor("#B3261E")}
DECISION = {"clear": "Cleared", "secondary": "Referred to secondary inspection", "reject": "Entry refused (officer decision)"}


def _img(data_url: str, w_mm: float, h_mm: float | None = None):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    from PIL import Image as PI
    im = PI.open(io.BytesIO(raw))
    w = w_mm * mm
    h = (h_mm * mm) if h_mm else w * im.height / im.width
    return Image(io.BytesIO(raw), width=w, height=h)


def build(case: dict, audit_rows: list[dict]) -> bytes:
    res = json.loads(case["result_json"])
    rk = res["risk"]
    buf = io.BytesIO()
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=8.6, leading=11.5)
    small = ParagraphStyle("s", parent=base, fontSize=7.4, leading=9.6, textColor=GREY)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=9.4, textColor=NAVY, spaceBefore=9, spaceAfter=3)
    mono = ParagraphStyle("m", parent=base, fontName="Courier", fontSize=8.2, leading=10)

    def header(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 17 * mm, A4[0], 17 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(15 * mm, A4[1] - 10.5 * mm, "BORDER DOCUMENT SCREENING - CASE REPORT")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 15 * mm, A4[1] - 10.5 * mm, f"Case {case['id']}")
        canvas.setFillColor(GREY)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 9 * mm, "Decision-support output. The recommendation is advisory; the border officer makes the decision. "
                                           "Specimen data only - prototype.")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=23 * mm, bottomMargin=16 * mm,
                            title=f"Case report {case['id']}", author="Document Screening Prototype")
    st = []
    ctx = res.get("context", {})
    meta = [["Case", case["id"], "Screened", res["created_at"].replace("T", " ")],
            ["Traveller", f"{res['traveller']['name'] or '-'}  (DOB {res['traveller']['dob'] or '-'})", "Reference date", res["reference_date"]],
            ["Checkpoint / booth", f"{ctx.get('checkpoint') or '-'} / {ctx.get('booth') or '-'}", "Screened by", ctx.get("officer") or "-"],
            ["Processing time", f"{res['timings'].get('total', 0) / 1000:.1f} s", "Documents", case["doc_summary"]]]
    t = Table(meta, colWidths=[30 * mm, 66 * mm, 28 * mm, 56 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.4), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.2), ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.2),
                           ("TEXTCOLOR", (0, 0), (0, -1), GREY), ("TEXTCOLOR", (2, 0), (2, -1), GREY), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    st += [t, Spacer(1, 6)]
    badge = Table([[f"RISK {rk['level']}", f"Score {rk['score']} / 100", rk["action_text"]]], colWidths=[30 * mm, 28 * mm, 122 * mm])
    badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), LEVEL[rk["level"]]), ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
                               ("FONT", (0, 0), (0, 0), "Helvetica-Bold", 10), ("FONT", (1, 0), (1, 0), "Helvetica-Bold", 9), ("FONT", (2, 0), (2, 0), "Helvetica", 8.4),
                               ("BOX", (0, 0), (-1, -1), 0.6, LEVEL[rk["level"]]), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    st += [badge]

    st.append(Paragraph("Why this recommendation", h2))
    rows = [["Severity", "Module", "Finding"]]
    for r in rk["reasons"]:
        rows.append([r["severity"].upper(), r["module"], Paragraph(r["text"].replace("&", "&amp;").replace("<", "&lt;"), base)])
    if len(rows) == 1:
        rows.append(["-", "-", "No findings above informational level."])
    rt = Table(rows, colWidths=[20 * mm, 24 * mm, 136 * mm], repeatRows=1)
    sev_col = {"CRITICAL": "#B3261E", "HIGH": "#B3261E", "MEDIUM": "#B7791F", "LOW": "#5A6672"}
    style = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF2")),
             ("FONT", (0, 1), (1, -1), "Helvetica", 8), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for i, r in enumerate(rk["reasons"], start=1):
        style.append(("TEXTCOLOR", (0, i), (0, i), colors.HexColor(sev_col.get(r["severity"].upper(), "#5A6672"))))
        style.append(("FONT", (0, i), (0, i), "Helvetica-Bold", 8))
    rt.setStyle(TableStyle(style))
    st.append(rt)

    st.append(Paragraph("Score breakdown", h2))
    cb = [["Module", "Points", "Cap"]] + [[c["label"], str(c["points"]), str(c["cap"])] for c in rk["contributions"]] or [["-", "0", "-"]]
    ct = Table(cb, colWidths=[70 * mm, 20 * mm, 20 * mm])
    ct.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF2")), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE)]))
    st.append(ct)

    fc = res.get("face") or {}
    if fc.get("doc_crop") or fc.get("live_crop"):
        st.append(Paragraph("Face verification", h2))
        cells = []
        for key, cap in (("doc_crop", "Document portrait"), ("live_crop", "Live capture")):
            cells.append([_img(fc[key], 30, 30) if fc.get(key) else Paragraph("n/a", base), Paragraph(cap, small)])
        eng = fc.get("engine") or {}
        liv = fc.get("liveness") or {}
        liv_str = f"Liveness (PAD): {liv.get('decision', 'live').upper()} ({int((liv.get('liveness_score') or 1.0) * 100)}% - ISO/IEC 30107-3)"
        txt = (f"Engine: {eng.get('name', '-')}{'' if eng.get('trained') else ' (demo matcher, not production grade)'}<br/>"
               f"Similarity: {fc.get('similarity')}  |  match &gt;= {eng.get('match_threshold')}  |  mismatch &lt;= {eng.get('mismatch_threshold')}<br/>"
               f"Result: <b>{(fc.get('decision') or '').upper()}</b> &nbsp;|&nbsp; {liv_str}")
        ft = Table([[cells[0][0], cells[1][0], Paragraph(txt, base)], [cells[0][1], cells[1][1], ""]], colWidths=[34 * mm, 34 * mm, 112 * mm])
        ft.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        st.append(ft)

    st.append(Paragraph("Documents examined", h2))
    drows = [["#", "Type", "File", "SHA-256 (evidence)", "MRZ"]]
    for d in res["documents"]:
        mz = d.get("mrz") or {}
        drows.append([str(d["index"] + 1), d["doc_type_label"], Paragraph(d["filename"], small), Paragraph(d["sha256"][:32] + "...", ParagraphStyle("mm", parent=small, fontName="Courier")),
                      "valid" if (mz.get("parsed") or {}).get("ok") else ("check failed" if mz.get("found") else "not read")])
    dt = Table(drows, colWidths=[8 * mm, 32 * mm, 52 * mm, 60 * mm, 28 * mm], repeatRows=1)
    dt.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF2")), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    st.append(dt)
    for d in res["documents"]:
        lines = (d.get("mrz") or {}).get("lines")
        if lines:
            st.append(Spacer(1, 3))
            st.append(Paragraph(f"MRZ - {d['doc_type_label']}", small))
            for ln in lines:
                st.append(Paragraph(ln.replace("<", "&lt;"), mono))

    wl = (res.get("watchlist") or {}).get("details", {})
    if wl:
        st.append(Paragraph("Watchlist (SIMULATED)", h2))
        hits = wl.get("hits", [])
        st.append(Paragraph("No hits." if not hits else "; ".join(f"{h['ref']} - {h.get('category') or h.get('status')}" for h in hits), base))

    st.append(Paragraph("Officer decision", h2))
    if case.get("decision"):
        dec = [["Decision", DECISION.get(case["decision"], case["decision"])], ["Officer", case.get("decided_by") or "-"], ["Time", (case.get("decided_at") or "").replace("T", " ")],
               ["Note", Paragraph((case.get("decision_note") or "-").replace("&", "&amp;").replace("<", "&lt;"), base)]]
        dtb = Table(dec, colWidths=[28 * mm, 152 * mm])
        dtb.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.4), ("FONT", (1, 0), (1, -1), "Helvetica", 8.4), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE)]))
        st.append(dtb)
    else:
        st.append(Paragraph("No decision has been recorded for this case yet.", base))

    st.append(Paragraph("Audit trail (hash-chained)", h2))
    arows = [["#", "Time", "Actor", "Action", "Hash"]]
    for a in sorted(audit_rows, key=lambda r: r["id"]):
        arows.append([str(a["id"]), a["ts"].replace("T", " ")[:19], a["actor"], a["action"], Paragraph(a["hash"][:24] + "...", ParagraphStyle("mm2", parent=small, fontName="Courier"))])
    at = Table(arows, colWidths=[10 * mm, 38 * mm, 30 * mm, 28 * mm, 74 * mm], repeatRows=1)
    at.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7.8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.8),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF2")), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE)]))
    st.append(at)
    doc.build(st, onFirstPage=header, onLaterPages=header)
    return buf.getvalue()
