"""Document validation against standards: ICAO 9303 MRZ rules, field consistency
between the machine-readable and visual zones, date logic, country codes, layout."""
from __future__ import annotations

from datetime import date, timedelta
from difflib import SequenceMatcher

from . import fields as FLD
from . import mrz as M
from .result import finding, result

MRZ_TYPES = {"passport", "visa", "id_card", "permit"}
CODE_PREFIX = {"passport": ("P",), "visa": ("V",), "id_card": ("I", "A", "C"), "permit": ("IR", "AR", "AC", "I", "A")}
CHECK_LABEL = {"doc_number": "document number", "dob": "date of birth", "expiry": "date of expiry",
               "personal_number": "personal number", "composite": "composite (whole line)"}


def _d(iso):
    try:
        return date.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _norm_num(s: str | None) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _name_eq(a: str, b: str) -> bool:
    a, b = M.normalise_name(a), M.normalise_name(b)
    if not a or not b:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    sa, sb = a.replace(" ", ""), b.replace(" ", "")
    return (len(sb) >= 28 and sa.startswith(sb)) or (len(sa) >= 28 and sb.startswith(sa))


def _vf(fields: dict, key: str):
    f = fields.get(key)
    return f if f and f.get("value") not in (None, "") else None


def validate(doc_type: str, mrz_read: dict | None, fields: dict, ref: date, img=None) -> dict:
    fs: list[dict] = []
    det: dict = {"doc_type": doc_type, "mrz_checks": [], "comparisons": []}
    parsed = (mrz_read or {}).get("parsed") or None
    mrz_box = (mrz_read or {}).get("region")

    # ---- 1. MRZ presence and ICAO check digits --------------------------------
    if doc_type in MRZ_TYPES:
        if not parsed or not parsed.get("format"):
            fs.append(finding("medium", "MRZ_NOT_FOUND",
                              "No machine-readable zone could be read from this page. Read it manually or re-scan.", mrz_box))
            parsed = None
    elif doc_type != "unknown":
        fs.append(finding("info", "MRZ_NOT_REQUIRED", f"{FLD.NICE[doc_type]} has no machine-readable zone; checked from visual fields only."))

    if parsed:
        det["mrz_format"] = parsed["format"]
        det["mrz_method"] = (mrz_read or {}).get("method")
        failed = []
        for c in parsed.get("checks", []):
            det["mrz_checks"].append({"name": c["name"], "ok": c["ok"], "found": c["found"], "expected": c["expected"]})
            if not c["ok"]:
                failed.append(c)
        for c in failed:
            if c["name"] == "composite" and len(failed) > 1:
                continue
            fs.append(finding("high", f"MRZ_CHECK_{c['name'].upper()}",
                              f"MRZ check digit failed for {CHECK_LABEL.get(c['name'], c['name'])} "
                              f"(printed {c['found']}, calculated {c['expected']}). The MRZ content does not match its own checksum - "
                              f"it was altered or misprinted.", mrz_box))
        if (mrz_read or {}).get("notes") and (mrz_read or {}).get("method") != "glyph":
            fs.append(finding("low", "MRZ_OCR_REPAIRED",
                              "OCR confusions in the MRZ were corrected using the check digits: " + "; ".join(mrz_read["notes"][:3]), mrz_box))
        # code and country checks
        code = parsed.get("doc_code", "")
        if doc_type in CODE_PREFIX and not code.startswith(CODE_PREFIX[doc_type]):
            fs.append(finding("medium", "VAL_DOC_CODE", f"MRZ document code '{code}' does not fit a {FLD.NICE[doc_type].lower()}.", mrz_box))
        for lab, val in (("issuing state", parsed.get("issuer")), ("nationality", parsed.get("nationality"))):
            if val and val not in M.ISO3:
                fs.append(finding("medium", "VAL_COUNTRY_CODE", f"MRZ {lab} code '{val}' is not a valid ICAO/ISO 3166 code.", mrz_box))
        if parsed.get("sex") not in ("M", "F", "<", "X"):
            fs.append(finding("low", "VAL_SEX_CODE", "MRZ sex field has an invalid value.", mrz_box))

    # ---- 2. MRZ vs visual zone --------------------------------------------------
    def compare(name: str, viz_key: str, mrz_val, eq, label: str, sev: str = "high"):
        f = _vf(fields, viz_key)
        if f is None or not mrz_val:
            return
        if f.get("source") == "mrz" or f.get("mrz_fallback"):
            return
        ok = eq(f["value"], mrz_val)
        if not ok and img is not None and f.get("bbox"):
            again = FLD.reread_value(img, f["bbox"], viz_key)
            if again and again.get("value") not in (None, "") and eq(again["value"], mrz_val):
                ok, f = True, dict(f, value=again["value"], reread=True)
        det["comparisons"].append({"field": name, "viz": f["value"], "mrz": mrz_val, "match": ok})
        if not ok:
            conf = f.get("conf", 100)
            fs.append(finding(sev if conf >= 70 else "medium", f"VAL_MRZ_VIZ_{name.upper()}",
                              f"{label} differs between the printed field ({f['value']}) and the MRZ ({mrz_val}).", f.get("bbox"),
                              viz=f["value"], mrz=mrz_val))

    if parsed:
        compare("surname", "surname", parsed.get("surname"), _name_eq, "Surname")
        compare("given", "given", parsed.get("given_names"), _name_eq, "Given names")
        compare("dob", "dob", parsed.get("dob_iso"), lambda a, b: a == b, "Date of birth")
        compare("expiry", "expiry", parsed.get("expiry_iso"), lambda a, b: a == b, "Date of expiry")
        compare("sex", "sex", parsed.get("sex") if parsed.get("sex") in ("M", "F", "X") else None, lambda a, b: a == b, "Sex", "medium")
        num_key = "visa_no" if doc_type == "visa" else "number"
        compare("number", num_key, (parsed.get("doc_number") or "").replace("<", ""), lambda a, b: _norm_num(a) == _norm_num(b), "Document number")

    # ---- 3. date logic ----------------------------------------------------------
    exp = _d(parsed.get("expiry_iso")) if parsed else None
    exp_src = "MRZ"
    if exp is None and _vf(fields, "expiry"):
        exp, exp_src = _d(fields["expiry"]["value"]), "printed"
    dob = _d(parsed.get("dob_iso")) if parsed else None
    if dob is None and _vf(fields, "dob"):
        dob = _d(fields["dob"]["value"])
    issue = _d(fields["issue"]["value"]) if _vf(fields, "issue") else None
    if exp:
        if exp < ref:
            days = (ref - exp).days
            fs.append(finding("high", "VAL_EXPIRED", f"Document expired on {exp.isoformat()} ({days} days before the screening date).",
                              (fields.get("expiry") or {}).get("bbox")))
        elif (exp - ref).days < 180 and doc_type in ("passport",):
            fs.append(finding("low", "VAL_EXPIRES_SOON", f"Passport expires within 6 months ({exp.isoformat()}); many states refuse entry.",
                              (fields.get("expiry") or {}).get("bbox")))
    if issue:
        if issue > ref:
            fs.append(finding("high", "VAL_ISSUE_FUTURE", f"Date of issue ({issue.isoformat()}) is after the screening date.",
                              (fields.get("issue") or {}).get("bbox")))
        if exp and issue >= exp:
            fs.append(finding("high", "VAL_DATE_ORDER", "Date of issue is not before the date of expiry.", (fields.get("issue") or {}).get("bbox")))
        if dob and issue < dob:
            fs.append(finding("high", "VAL_DATE_ORDER", "Document was issued before the holder was born.", (fields.get("issue") or {}).get("bbox")))
        if exp and doc_type == "passport" and exp > issue + timedelta(days=10 * 366 + 31):
            fs.append(finding("medium", "VAL_VALIDITY_PERIOD", "Validity period is longer than the 10 years allowed for passports.",
                              (fields.get("expiry") or {}).get("bbox")))
    if dob:
        age = (ref - dob).days / 365.25
        if dob >= ref or age > 120:
            fs.append(finding("high", "VAL_DOB_IMPLAUSIBLE", f"Date of birth {dob.isoformat()} is not plausible.", (fields.get("dob") or {}).get("bbox")))

    # ---- 4. layout ----------------------------------------------------------------
    need = FLD.REQUIRED.get(doc_type, [])
    missing = [k for k in need if not _vf(fields, k)]
    det["fields_found"] = len(need) - len(missing)
    det["fields_expected"] = len(need)
    if need:
        if len(missing) > len(need) / 2:
            fs.append(finding("medium", "VAL_LAYOUT", f"Only {len(need) - len(missing)} of {len(need)} expected fields were found; the layout differs "
                                                     "from the issuing-state template or the image is unreadable."))
        elif missing:
            fs.append(finding("low", "VAL_FIELD_MISSING", "Could not read: " + ", ".join(missing) + "."))
    det["expiry"], det["dob"] = (exp.isoformat() if exp else None), (dob.isoformat() if dob else None)
    return result(fs, det)
