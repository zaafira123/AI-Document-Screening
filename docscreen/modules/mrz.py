"""ICAO 9303 machine-readable zone (MRZ) parsing, check-digit validation and
checksum-guided repair of OCR errors.

Supported formats
  TD3   passports                  2 lines x 44 characters
  MRV-A visas (type A)             2 lines x 44 characters
  TD1   ID cards / permits         3 lines x 30 characters
"""
from __future__ import annotations

import itertools
import re
import unicodedata
from datetime import date

WEIGHTS = (7, 3, 1)
ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"
MRZ_CHARS = ALPHA + DIGITS + "<"

# characters OCR engines commonly confuse (read -> candidates)
TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"}
TO_ALPHA = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}
CONFUSIONS = {
    "0": "OD", "O": "0D", "D": "0O", "1": "IL7", "I": "1L", "L": "1I", "2": "Z", "Z": "2",
    "5": "S", "S": "5", "8": "B", "B": "8", "6": "G", "G": "6", "7": "1T", "U": "V", "V": "U",
    "M": "N", "N": "M", "H": "N", "<": "KC", "K": "<", "C": "<G", "Q": "O0", "E": "F", "F": "E",
}


def char_value(c: str) -> int:
    if c in DIGITS:
        return int(c)
    if c in ALPHA:
        return ord(c) - 55
    return 0  # '<' and anything else


def check_digit(data: str) -> str:
    total = sum(char_value(c) * WEIGHTS[i % 3] for i, c in enumerate(data))
    return str(total % 10)


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------
def parse_yymmdd(s: str, kind: str, today: date | None = None):
    """Return a date or None. kind: 'dob' | 'expiry' | 'issue'."""
    today = today or date.today()
    if not re.fullmatch(r"\d{6}", s or ""):
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    if kind == "dob":
        year = 2000 + yy if 2000 + yy <= today.year else 1900 + yy
    elif kind == "issue":
        year = 2000 + yy if 2000 + yy <= today.year + 1 else 1900 + yy
    else:  # expiry: documents expire at most ~ 25 years ahead
        year = 2000 + yy if 2000 + yy <= today.year + 25 else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def fmt_yymmdd(d: date | None) -> str:
    return d.strftime("%y%m%d") if d else ""


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------
def split_name(field: str) -> tuple[str, str]:
    field = field.rstrip("<")
    if "<<" in field:
        sur, giv = field.split("<<", 1)
    else:
        sur, giv = field, ""
    return sur.replace("<", " ").strip(), giv.replace("<", " ").strip()


def normalise_name(s: str) -> str:
    """Compare-friendly name: strip accents, punctuation, case and spacing."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z ]", " ", s.upper().replace("'", "").replace("-", " "))
    return " ".join(s.split())


# --------------------------------------------------------------------------
# format detection and parsing
# --------------------------------------------------------------------------
def detect_format(lines: list[str]) -> str | None:
    if len(lines) == 3 and all(len(l) == 30 for l in lines):
        return "TD1"
    if len(lines) == 2 and all(len(l) == 44 for l in lines):
        return "MRVA" if lines[0][0] == "V" else "TD3"
    return None


def _chk(name: str, value: str, found: str, label: str | None = None) -> dict:
    if name == "personal_number" and found == "<":
        # Under ICAO Doc 9303 Part 4, character position 43 may be '<' when optional data
        # is unutilized or when the issuing authority does not utilize a check digit for it.
        return {"name": name, "label": label or name, "value": value, "expected": "<", "found": found, "ok": True}
    expected = check_digit(value)
    if found == "<" and value.strip("<") == "":
        ok = True
    else:
        ok = expected == found
    return {"name": name, "label": label or name, "value": value, "expected": expected, "found": found, "ok": ok}


def parse_mrz(lines: list[str], today: date | None = None) -> dict:
    today = today or date.today()
    fmt = detect_format(lines)
    out: dict = {"format": fmt, "lines": lines, "checks": [], "ok": False}
    if fmt is None:
        out["error"] = "Unsupported MRZ geometry"
        return out

    if fmt in ("TD3", "MRVA"):
        l1, l2 = lines
        out.update(
            doc_code=l1[0:2].replace("<", ""),
            issuer=l1[2:5].replace("<", ""),
            nationality=l2[10:13].replace("<", ""),
            sex=l2[20] if l2[20] in "MF" else ("X" if l2[20] == "X" else ""),
        )
        out["surname"], out["given_names"] = split_name(l1[5:44])
        number = l2[0:9]
        out["doc_number"] = number.replace("<", "")
        out["dob_raw"], out["expiry_raw"] = l2[13:19], l2[21:27]
        out["checks"] = [
            _chk("doc_number", number, l2[9], "Document number"),
            _chk("dob", l2[13:19], l2[19], "Date of birth"),
            _chk("expiry", l2[21:27], l2[27], "Date of expiry"),
        ]
        if fmt == "TD3":
            out["personal_number"] = l2[28:42].replace("<", "")
            out["checks"].append(_chk("personal_number", l2[28:42], l2[42], "Personal number"))
            comp = l2[0:10] + l2[13:20] + l2[21:43]
            out["checks"].append(_chk("composite", comp, l2[43], "Composite"))
        else:
            out["optional"] = l2[28:44].replace("<", "")
    else:  # TD1
        l1, l2, l3 = lines
        out.update(
            doc_code=l1[0:2].replace("<", ""),
            issuer=l1[2:5].replace("<", ""),
            nationality=l2[15:18].replace("<", ""),
            sex=l2[7] if l2[7] in "MFX" else "",
        )
        out["surname"], out["given_names"] = split_name(l3)
        number = l1[5:14]
        out["doc_number"] = number.replace("<", "")
        out["dob_raw"], out["expiry_raw"] = l2[0:6], l2[8:14]
        out["optional"] = (l1[15:30] + l2[18:29]).replace("<", "")
        comp = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
        out["checks"] = [
            _chk("doc_number", number, l1[14], "Document number"),
            _chk("dob", l2[0:6], l2[6], "Date of birth"),
            _chk("expiry", l2[8:14], l2[14], "Date of expiry"),
            _chk("composite", comp, l2[29], "Composite"),
        ]

    out["dob"] = parse_yymmdd(out["dob_raw"], "dob", today)
    out["expiry"] = parse_yymmdd(out["expiry_raw"], "expiry", today)
    out["dob_iso"] = out["dob"].isoformat() if out["dob"] else None
    out["expiry_iso"] = out["expiry"].isoformat() if out["expiry"] else None
    out["ok"] = all(c["ok"] for c in out["checks"])
    return out


# --------------------------------------------------------------------------
# candidate extraction + repair
# --------------------------------------------------------------------------
def clean_line(s: str) -> str:
    s = s.upper().replace(" ", "").replace("«", "<").replace("‹", "<").replace("〈", "<").replace("＜", "<")
    return re.sub(r"[^A-Z0-9<]", "<", s)


def pick_mrz_lines(text_lines: list[str]) -> list[str] | None:
    """From raw OCR lines pick the MRZ block (2x44 or 3x30)."""
    cands = [clean_line(l) for l in text_lines]
    cands = [c for c in cands if len(c) >= 24 and c.count("<") >= 2]
    if len(cands) < 2:
        return None
    best = None
    for n, width in ((3, 30), (2, 44)):
        for i in range(len(cands) - n + 1):
            block = cands[i : i + n]
            dev = sum(abs(len(b) - width) for b in block)
            if dev <= 6 * n and (best is None or dev < best[0]):
                best = (dev, block, width)
    if not best:
        return None
    _, block, width = best
    return [b[:width].ljust(width, "<") for b in block]


def _field_spans(fmt: str):
    """(line, start, end, kind) for every character field. kind: n=numeric a=alpha x=alnum"""
    if fmt in ("TD3", "MRVA"):
        s = [(0, 0, 1, "a"), (0, 2, 5, "a"), (0, 5, 44, "a"),
             (1, 0, 9, "x"), (1, 9, 10, "n"), (1, 10, 13, "a"), (1, 13, 19, "n"), (1, 19, 20, "n"),
             (1, 20, 21, "a"), (1, 21, 27, "n"), (1, 27, 28, "n")]
        if fmt == "TD3":
            s += [(1, 28, 42, "x"), (1, 42, 43, "n"), (1, 43, 44, "n")]
        return s
    return [(0, 0, 1, "a"), (0, 2, 5, "a"), (0, 5, 14, "x"), (0, 14, 15, "n"),
            (1, 0, 6, "n"), (1, 6, 7, "n"), (1, 7, 8, "a"), (1, 8, 14, "n"), (1, 14, 15, "n"),
            (1, 15, 18, "a"), (1, 29, 30, "n"), (2, 0, 30, "a")]


def _coerce(lines: list[str], fmt: str) -> tuple[list[str], list[str]]:
    """Force each field into its character class (digits / letters)."""
    chars = [list(l) for l in lines]
    notes = []
    for li, a, b, kind in _field_spans(fmt):
        for i in range(a, b):
            c = chars[li][i]
            if kind == "n" and c in ALPHA and c in TO_DIGIT:
                notes.append(f"line {li + 1} col {i + 1}: {c}->{TO_DIGIT[c]}")
                chars[li][i] = TO_DIGIT[c]
            elif kind == "a" and c in DIGITS and c in TO_ALPHA:
                notes.append(f"line {li + 1} col {i + 1}: {c}->{TO_ALPHA[c]}")
                chars[li][i] = TO_ALPHA[c]
    return ["".join(c) for c in chars], notes


def repair(lines: list[str], today: date | None = None, max_edits: int = 2) -> tuple[list[str], dict, list[str]]:
    """Return (lines, parsed, notes). Tries character-class coercion and then a
    small check-digit-guided search over frequent OCR confusions."""
    fmt = detect_format(lines)
    if fmt is None:
        return lines, parse_mrz(lines, today), []
    fixed, notes = _coerce(lines, fmt)
    parsed = parse_mrz(fixed, today)
    if parsed["ok"]:
        return fixed, parsed, notes

    # search: for each failing check digit field, try limited substitutions
    spans = {
        "TD3": {"doc_number": (1, 0, 9, 9), "dob": (1, 13, 19, 19), "expiry": (1, 21, 27, 27),
                "personal_number": (1, 28, 42, 42)},
        "MRVA": {"doc_number": (1, 0, 9, 9), "dob": (1, 13, 19, 19), "expiry": (1, 21, 27, 27)},
        "TD1": {"doc_number": (0, 5, 14, 14), "dob": (1, 0, 6, 6), "expiry": (1, 8, 14, 14)},
    }[fmt]
    work = [list(l) for l in fixed]
    for chk in parsed["checks"]:
        if chk["ok"] or chk["name"] not in spans:
            continue
        li, a, b, cpos = spans[chk["name"]]
        original = "".join(work[li][a:b])
        found = work[li][cpos]
        options = []
        for i, ch in enumerate(original):
            # date fields can only contain digits: never "repair" them into letters
            alts = [ch] + [x for x in CONFUSIONS.get(ch, "") if chk["name"] not in ("dob", "expiry") or x.isdigit()]
            options.append(alts[:4])
        done = False
        for k in range(1, max_edits + 1):
            for idxs in itertools.combinations(range(len(original)), k):
                for combo in itertools.product(*[options[i][1:] for i in idxs]):
                    cand = list(original)
                    for i, ch in zip(idxs, combo):
                        cand[i] = ch
                    cand_s = "".join(cand)
                    if check_digit(cand_s) == found:
                        work[li][a:b] = cand
                        notes.append(f"{chk['name']}: '{original}' -> '{cand_s}' (check digit guided)")
                        done = True
                        break
                if done:
                    break
            if done:
                break
    fixed2 = ["".join(l) for l in work]
    parsed2 = parse_mrz(fixed2, today)
    # keep the repair only if it did not make things worse
    if sum(c["ok"] for c in parsed2["checks"]) >= sum(c["ok"] for c in parsed["checks"]):
        return fixed2, parsed2, notes
    return fixed, parsed, notes


# --------------------------------------------------------------------------
# builders (used by the sample generator and tests)
# --------------------------------------------------------------------------
def _pad(s: str, n: int) -> str:
    s = s.upper()
    return (s + "<" * n)[:n]


def name_field(surname: str, given: str, width: int) -> str:
    def clean(x):
        x = normalise_name(x)
        return x.replace(" ", "<")

    return _pad(f"{clean(surname)}<<{clean(given)}", width)


def build_td3(*, doc_code="P", issuer, surname, given, number, nationality, dob: date, sex, expiry: date, personal="") -> list[str]:
    l1 = _pad(f"{doc_code:<<1}", 2) + _pad(issuer, 3) + name_field(surname, given, 39)
    num = _pad(number, 9)
    per = _pad(personal, 14)
    d, e = fmt_yymmdd(dob), fmt_yymmdd(expiry)
    body = num + check_digit(num) + _pad(nationality, 3) + d + check_digit(d) + sex + e + check_digit(e) + per
    pc = check_digit(per) if per.strip("<") else "<"
    l2 = body + pc
    comp = l2[0:10] + l2[13:20] + l2[21:43]
    l2 += check_digit(comp)
    return [l1, l2]


def build_mrva(*, issuer, surname, given, number, nationality, dob: date, sex, expiry: date, optional="") -> list[str]:
    l1 = "V<" + _pad(issuer, 3) + name_field(surname, given, 39)
    num = _pad(number, 9)
    d, e = fmt_yymmdd(dob), fmt_yymmdd(expiry)
    l2 = num + check_digit(num) + _pad(nationality, 3) + d + check_digit(d) + sex + e + check_digit(e) + _pad(optional, 16)
    return [l1, l2]


def build_td1(*, doc_code="I", issuer, surname, given, number, nationality, dob: date, sex, expiry: date, optional1="", optional2="") -> list[str]:
    num = _pad(number, 9)
    d, e = fmt_yymmdd(dob), fmt_yymmdd(expiry)
    l1 = _pad(f"{doc_code:<<1}", 2) + _pad(issuer, 3) + num + check_digit(num) + _pad(optional1, 15)
    l2 = d + check_digit(d) + sex + e + check_digit(e) + _pad(nationality, 3) + _pad(optional2, 11)
    comp = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    l2 += check_digit(comp)
    l3 = name_field(surname, given, 30)
    return [l1, l2, l3]


# ISO 3166-1 alpha-3 plus the ICAO document-specific codes
ISO3 = set(
    "AFG ALA ALB DZA ASM AND AGO AIA ATA ATG ARG ARM ABW AUS AUT AZE BHS BHR BGD BRB BLR BEL BLZ BEN BMU BTN BOL BES BIH BWA BVT BRA "
    "IOT BRN BGR BFA BDI CPV KHM CMR CAN CYM CAF TCD CHL CHN CXR CCK COL COM COG COD COK CRI CIV HRV CUB CUW CYP CZE DNK DJI DMA DOM "
    "ECU EGY SLV GNQ ERI EST SWZ ETH FLK FRO FJI FIN FRA GUF PYF ATF GAB GMB GEO DEU GHA GIB GRC GRL GRD GLP GUM GTM GGY GIN GNB GUY "
    "HTI HMD VAT HND HKG HUN ISL IND IDN IRN IRQ IRL IMN ISR ITA JAM JPN JEY JOR KAZ KEN KIR PRK KOR KWT KGZ LAO LVA LBN LSO LBR LBY "
    "LIE LTU LUX MAC MDG MWI MYS MDV MLI MLT MHL MTQ MRT MUS MYT MEX FSM MDA MCO MNG MNE MSR MAR MOZ MMR NAM NRU NPL NLD NCL NZL NIC "
    "NER NGA NIU NFK MKD MNP NOR OMN PAK PLW PSE PAN PNG PRY PER PHL PCN POL PRT PRI QAT REU ROU RUS RWA BLM SHN KNA LCA MAF SPM VCT "
    "WSM SMR STP SAU SEN SRB SYC SLE SGP SXM SVK SVN SLB SOM ZAF SGS SSD ESP LKA SDN SUR SJM SWE CHE SYR TWN TJK TZA THA TLS TGO TKL "
    "TON TTO TUN TUR TKM TCA TUV UGA UKR ARE GBR USA UMI URY UZB VUT VEN VNM VGB VIR WLF ESH YEM ZMB ZWE "
    "UTO D GBD GBN GBO GBP GBS RKS UNO UNA UNK XXA XXB XXC XXX EUE".split()
)
