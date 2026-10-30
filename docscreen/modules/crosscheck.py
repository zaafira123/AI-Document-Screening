"""Consistency between the documents presented together (passport vs visa / permit / ID card / licence)."""
from __future__ import annotations

from datetime import date

from . import mrz as M
from .result import finding, result


def identity_of(doc: dict) -> dict:
    p = ((doc.get("mrz") or {}).get("parsed")) or {}
    f = doc.get("fields") or {}

    def v(k):
        x = f.get(k)
        return x.get("value") if x else None

    return {
        "surname": p.get("surname") or v("surname"),
        "given": p.get("given_names") or v("given"),
        "dob": p.get("dob_iso") or v("dob"),
        "sex": p.get("sex") if p.get("sex") in ("M", "F", "X") else v("sex"),
        "number": ((p.get("doc_number") or "").replace("<", "")) or v("number"),
        "expiry": p.get("expiry_iso") or v("expiry"),
        "optional": (p.get("optional") or p.get("personal_number") or "").replace("<", ""),
    }


def _n(s):
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _d(iso):
    try:
        return date.fromisoformat(iso)
    except Exception:
        return None


def _name_match(a: str | None, b: str | None) -> bool:
    na, nb = M.normalise_name(a or ""), M.normalise_name(b or "")
    if not na or not nb:
        return False
    if na == nb or na.replace(" ", "") == nb.replace(" ", ""):
        return True
    sa, sb = na.replace(" ", ""), nb.replace(" ", "")
    return (len(sb) >= 28 and sa.startswith(sb)) or (len(sa) >= 28 and sb.startswith(sa))


def check(docs: list[dict], ref: date) -> dict:
    """docs: [{role, doc_type, mrz, fields}]"""
    fs, det = [], {"pairs": []}
    if len(docs) < 2:
        return result([], {"reason": "single document - nothing to cross-check"}, status="skipped")
    base = next((d for d in docs if d["doc_type"] == "passport"), docs[0])
    bi = identity_of(base)
    for d in docs:
        if d is base:
            continue
        oi = identity_of(d)
        dt = d["doc_type"]
        label = f"{dt.replace('_', ' ')} vs {base['doc_type'].replace('_', ' ')}"
        row = {"pair": label, "checks": []}
        # name
        name_ok = _name_match(oi["surname"], bi["surname"]) and _name_match(oi["given"], bi["given"])
        row["checks"].append({"field": "name", "ok": name_ok, "a": f"{oi['given']} {oi['surname']}", "b": f"{bi['given']} {bi['surname']}"})
        if not name_ok and oi["surname"] and bi["surname"]:
            sev = "high" if dt in ("visa", "permit") else "medium"
            fs.append(finding(sev, "XC_NAME", f"Name on the {dt.replace('_', ' ')} ({oi['given']} {oi['surname']}) differs from the passport "
                                              f"({bi['given']} {bi['surname']})."))
        # date of birth
        dob_ok = (oi["dob"] == bi["dob"]) or not (oi["dob"] and bi["dob"])
        row["checks"].append({"field": "dob", "ok": dob_ok, "a": oi["dob"], "b": bi["dob"]})
        if not dob_ok:
            fs.append(finding("high", "XC_DOB", f"Date of birth on the {dt.replace('_', ' ')} ({oi['dob']}) differs from the passport ({bi['dob']})."))
        if oi["sex"] and bi["sex"] and oi["sex"] != bi["sex"]:
            fs.append(finding("medium", "XC_SEX", f"Sex on the {dt.replace('_', ' ')} differs from the passport."))
        if dt == "visa":
            ref_nos = []
            vf = (d.get("fields") or {}).get("passport_no")
            if vf and vf.get("value"):
                ref_nos.append(("printed", vf["value"], vf.get("bbox")))
            if oi["optional"]:
                ref_nos.append(("MRZ", oi["optional"], None))
            pn = _n(bi["number"])
            ok = True
            for src, val, bb in ref_nos:
                if pn and _n(val) != pn and not (len(_n(val)) < len(pn) and pn.startswith(_n(val))):
                    ok = False
                    fs.append(finding("high", "XC_NUMBER", f"Visa ({src}) was issued for passport {val}, but the passport presented is {bi['number']}.", bb))
            row["checks"].append({"field": "passport number on visa", "ok": ok, "a": [r[1] for r in ref_nos], "b": bi["number"]})
            vexp = _d(oi["expiry"])
            vfrom = _d(((d.get("fields") or {}).get("issue") or {}).get("value") or "")
            if vfrom and vfrom > ref:
                fs.append(finding("medium", "XC_VISA_NOT_YET", f"Visa is not valid until {vfrom.isoformat()}."))
            if vexp and vexp < ref:
                fs.append(finding("high", "XC_VISA_EXPIRED", f"Visa expired on {vexp.isoformat()}."))
            pexp = _d(bi["expiry"])
            if vexp and pexp and pexp < vexp:
                fs.append(finding("medium", "XC_PASSPORT_EXPIRES_BEFORE_VISA",
                                  f"Passport expires ({pexp.isoformat()}) before the visa does ({vexp.isoformat()})."))
        det["pairs"].append(row)
    return result(fs, det)
