"""Builds the specimen test catalog: genuine documents and simulated forgeries.

    python -m docscreen.samples.catalog [--out data/samples] [--ref 2026-10-18]

Dates are generated relative to the reference date, so the catalog stays
valid whatever day the demo is given.  All persons, numbers and the state
("Utopia", UTO) are fictional.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from docscreen.modules import face as F
from docscreen.modules import mrz as M
from . import forge as X
from . import render as R

ROOT = Path(__file__).resolve().parents[2]
FACE_DIR = ROOT / "data" / "faces"


# --------------------------------------------------------------------------
# faces
# --------------------------------------------------------------------------
class FaceSrc:
    def __init__(self, key: str, rgb: np.ndarray, live_rgb: np.ndarray | None = None):
        self.key, self.rgb, self.live_rgb = key, rgb, live_rgb
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        faces = F.detect_faces(bgr)
        if not faces:
            raise ValueError(f"no face found in source {key}")
        self.box = faces[0]["box"]

    def portrait(self, size=(262, 340)) -> Image.Image:
        return R.portrait_from(self.rgb, self.box, size)


def load_faces() -> list[FaceSrc]:
    """Two public-domain portraits that ship with scikit-image / matplotlib, plus any
    consented photos placed in data/faces/ (name.jpg; optional name_live.jpg)."""
    import skimage.data as sd
    import matplotlib.cbook as cb
    import matplotlib.image as mi

    out = [FaceSrc("astronaut", sd.astronaut())]
    gh = mi.imread(cb.get_sample_data("grace_hopper.jpg"))
    out.append(FaceSrc("hopper", gh))
    if FACE_DIR.exists():
        for p in sorted(FACE_DIR.glob("*")):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and not p.stem.endswith("_live"):
                try:
                    rgb = np.array(Image.open(p).convert("RGB"))
                    lp = next((q for q in FACE_DIR.glob(p.stem + "_live.*")), None)
                    live = np.array(Image.open(lp).convert("RGB")) if lp else None
                    out.append(FaceSrc(p.stem, rgb, live))
                except Exception:
                    continue
    return out


def live_capture(src: FaceSrc, seed: int) -> Image.Image:
    """A 'live camera' frame: the consented live photo if one was supplied, otherwise the
    source photo with simulated camera variation (exposure, small rotation, noise)."""
    rng = np.random.default_rng(seed)
    rgb = src.live_rgb if src.live_rgb is not None else src.rgb
    a = rgb.astype(np.float32)
    if src.live_rgb is None:
        h, w = a.shape[:2]
        M_ = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-1.5, 1.5), rng.uniform(0.985, 1.015))
        a = cv2.warpAffine(a, M_, (w, h), borderMode=cv2.BORDER_REPLICATE)
        a = a * rng.uniform(0.95, 1.05) + rng.uniform(-6, 6)
        a = cv2.GaussianBlur(a, (0, 0), 0.7)
        a += rng.normal(0, 2.5, a.shape)
    return X.jpeg_roundtrip(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), 88)


# --------------------------------------------------------------------------
# people
# --------------------------------------------------------------------------
def people() -> dict[str, R.Person]:
    return {
        "A": R.Person("A", "ERIKSSON", "ANNA MARIA", "F", date(1974, 8, 12), "Zenith", passport_no="UT482917",
                      id_no="UD5539021", licence_no="DL-0048213", permit_no="WP7731045",
                      address="14 Harbour Row, Zenith", face_index=0),
        "B": R.Person("B", "HARTMANN", "TOMAS JOSEF", "M", date(1988, 3, 27), "Meridian", passport_no="UT771205",
                      id_no="UD6612480", permit_no="WP5540912", address="3 Mill Lane, Meridian", face_index=1),
        "C": R.Person("C", "KROWE", "VIKTOR ALDRIDGE", "M", date(1980, 2, 3), "Arcadia", passport_no="UT305588",
                      face_index=1),
        "D": R.Person("D", "BRANDT", "MARIA ELENA", "F", date(1979, 1, 22), "Zenith", passport_no="UT660431",
                      id_no="UD7781203", face_index=0),
    }


def _yrs(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------
class Builder:
    def __init__(self, out: Path, ref: date, faces: list[FaceSrc]):
        self.out, self.ref, self.faces = out, ref, faces
        self.P = people()
        self.manifest: list[dict] = []
        self.seed = 10

    def face(self, p: R.Person) -> FaceSrc:
        return self.faces[p.face_index % len(self.faces)]

    def portrait(self, p, size=(262, 340)):
        return self.face(p).portrait(size)

    # --- document factories (return PIL image + layout + mrz lines) -------------
    def passport(self, p, number=None, expiry=None, issue=None, **kw):
        issue = issue or self.ref - timedelta(days=3 * 365)
        expiry = expiry or _yrs(issue, 10)
        self.seed += 1
        return R.render_passport(p, self.portrait(p), issue, expiry, number or p.passport_no, seed=self.seed, **kw) + (issue, expiry)

    def visa(self, p, visa_no="VS4410287", passport_no=None, valid_from=None, valid_until=None, stay=30, stamps=None, **kw):
        valid_from = valid_from or self.ref - timedelta(days=12)
        valid_until = valid_until or self.ref + timedelta(days=78)
        self.seed += 1
        return R.render_visa_page(p, self.portrait(p, (190, 246)), visa_no, passport_no or p.passport_no, valid_from,
                                  valid_until, "MULTIPLE", stay, stamps=stamps, seed=self.seed, **kw)

    def card(self, p, kind, number, issue=None, expiry=None, **kw):
        issue = issue or self.ref - timedelta(days=2 * 365)
        expiry = expiry or _yrs(issue, 8)
        self.seed += 1
        return R.render_card(p, self.portrait(p, (218, 282)), kind, number, issue, expiry, seed=self.seed, **kw)

    def stamps_default(self):
        d1 = self.ref - timedelta(days=11)
        return [
            {"lines": ["UTOPIA", "ENTRY", R.fmt_date(d1)], "center": 250, "cy": 850, "angle": -8, "size": 270, "seed": 21},
            {"lines": ["UTOPIA", "AIRPORT", "CONTROL"], "center": 660, "cy": 850, "angle": 6, "size": 280, "seed": 22,
             "rect": True, "color": (160, 40, 44)},
        ]

    # --- saving ----------------------------------------------------------------
    def save(self, sid: str, name: str, img: Image.Image, **meta) -> str:
        d = self.out / sid
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.jpg"
        X.save_jpeg(img, path, 90, **meta)
        return f"{sid}/{name}.jpg"

    def genuine_scan(self, img, seed=0):
        return X.scan(img, seed)

    def add(self, sid, title, group, description, docs, live=None, expect_level=("LOW",), expect_codes=(), requires=(), tags=()):
        self.manifest.append({"id": sid, "title": title, "group": group, "description": description,
                              "docs": docs, "live": live, "expect": {"levels": list(expect_level), "codes": list(expect_codes)},
                              "requires": list(requires), "tags": list(tags)})

    # --- scenarios -----------------------------------------------------------------
    def build(self):
        P, ref = self.P, self.ref
        A, B, C, D = P["A"], P["B"], P["C"], P["D"]
        cam = ("Specimen Scanner", "DS-100")

        def photo_meta(base_dt):
            return dict(camera=cam, taken=base_dt, modified=base_dt)

        t0 = datetime(ref.year, ref.month, ref.day, 9, 15)

        # ---------------- genuine ----------------
        im, lay, _, _, _ = self.passport(A)
        self.add("genuine_passport", "Genuine passport - Utopia", "genuine",
                 "Clean specimen passport, live face matches.",
                 [{"role": "passport", "file": self.save("genuine_passport", "passport", self.genuine_scan(im, 1), **photo_meta(t0))}],
                 self.save("genuine_passport", "live", live_capture(self.face(A), 1)), ("LOW",))

        im, *_ = self.passport(B)
        self.add("genuine_passport_b", "Genuine passport - second traveller", "genuine", "Clean specimen passport.",
                 [{"role": "passport", "file": self.save("genuine_passport_b", "passport", self.genuine_scan(im, 2), **photo_meta(t0))}],
                 self.save("genuine_passport_b", "live", live_capture(self.face(B), 2)), ("LOW",))

        pim, *_ = self.passport(A)
        vim, *_ = self.visa(A, stamps=self.stamps_default())
        self.add("genuine_visa_pair", "Genuine passport + visa page", "genuine",
                 "Visa consistent with passport (number, name, DOB, validity).",
                 [{"role": "passport", "file": self.save("genuine_visa_pair", "passport", self.genuine_scan(pim, 3), **photo_meta(t0))},
                  {"role": "visa", "file": self.save("genuine_visa_pair", "visa", self.genuine_scan(vim, 4), **photo_meta(t0))}],
                 self.save("genuine_visa_pair", "live", live_capture(self.face(A), 3)), ("LOW",))

        im, *_ = self.card(B, "id_card", B.id_no)
        self.add("genuine_id_card", "Genuine national ID card", "genuine", "TD1 card with MRZ.",
                 [{"role": "id_card", "file": self.save("genuine_id_card", "id_card", self.genuine_scan(im, 5), **photo_meta(t0))}],
                 self.save("genuine_id_card", "live", live_capture(self.face(B), 4)), ("LOW",))

        im, *_ = self.card(A, "driving_licence", A.licence_no)
        self.add("genuine_licence", "Genuine driving licence", "genuine", "Licence has no MRZ; validated from visual fields only.",
                 [{"role": "driving_licence", "file": self.save("genuine_licence", "licence", self.genuine_scan(im, 6), **photo_meta(t0))}],
                 self.save("genuine_licence", "live", live_capture(self.face(A), 5)), ("LOW", "MEDIUM"))

        im, *_ = self.card(B, "permit", B.permit_no)
        self.add("genuine_permit", "Genuine work permit", "genuine", "TD1 permit with MRZ.",
                 [{"role": "permit", "file": self.save("genuine_permit", "permit", self.genuine_scan(im, 7), **photo_meta(t0))}],
                 self.save("genuine_permit", "live", live_capture(self.face(B), 6)), ("LOW",))

        # ---------------- tampered / risky ----------------
        # 1 DOB edited on the visual zone only
        im, lay, _, _, _ = self.passport(A)
        base = self.genuine_scan(im, 11)
        forged = X.edit_text(base, lay.boxes["dob"], R.fmt_date(date(1979, 8, 12)), 29)
        self.add("tamper_dob_viz", "Date of birth edited (visual zone)", "tampered",
                 "DOB retyped in a different font; MRZ still carries the original date.",
                 [{"role": "passport", "file": self.save("tamper_dob_viz", "passport", forged,
                                                         camera=cam, taken=t0, modified=t0)}],
                 self.save("tamper_dob_viz", "live", live_capture(self.face(A), 7)), ("HIGH",),
                 ["VAL_MRZ_VIZ_DOB", "TMP_"])

        # 2 MRZ edited without recomputing the check digit
        lines = M.build_td3(issuer="UTO", surname=A.surname, given=A.given, number=A.passport_no,
                            nationality="UTO", dob=A.dob, sex=A.sex, expiry=_yrs(self.ref - timedelta(days=3 * 365), 10))
        l2 = lines[1]
        new_dob = date(1979, 8, 12).strftime("%y%m%d")
        l2 = l2[:13] + new_dob + l2[19:]  # check digit at [19] left unchanged -> stale
        im, lay, *_ = self.passport(A, mrz_lines=[lines[0], l2])
        self.add("tamper_dob_mrz", "MRZ edited, check digit stale", "tampered",
                 "Birth date in the MRZ changed without recomputing the ICAO check digit.",
                 [{"role": "passport", "file": self.save("tamper_dob_mrz", "passport", self.genuine_scan(im, 12), **photo_meta(t0))}],
                 self.save("tamper_dob_mrz", "live", live_capture(self.face(A), 8)), ("HIGH",), ["MRZ_CHECK"])

        # 3 photo replaced
        im, lay, _, _, _ = self.passport(A)
        base = self.genuine_scan(im, 13)
        forged = X.swap_photo(base, lay.boxes["photo"], self.portrait(B))
        self.add("tamper_photo_swap", "Holder photo replaced", "tampered",
                 "Portrait swapped for another person; the live traveller is the original holder.",
                 [{"role": "passport", "file": self.save("tamper_photo_swap", "passport", forged, camera=cam, taken=t0, modified=t0)}],
                 self.save("tamper_photo_swap", "live", live_capture(self.face(A), 9)), ("HIGH",), ["FACE_MISMATCH"])

        # 4 expired
        issue = ref - timedelta(days=11 * 365)
        im, *_ = self.passport(A, issue=_yrs(ref - timedelta(days=200), -10), expiry=ref - timedelta(days=200))
        self.add("expired_passport", "Expired passport", "risky", "Genuine-looking passport that expired 200 days ago.",
                 [{"role": "passport", "file": self.save("expired_passport", "passport", self.genuine_scan(im, 14), **photo_meta(t0))}],
                 self.save("expired_passport", "live", live_capture(self.face(A), 10)), ("MEDIUM", "HIGH"), ["VAL_EXPIRED"])

        # 5 editor metadata only
        im, *_ = self.passport(A)
        self.add("tamper_editor_meta", "Edited in image software (metadata)", "tampered",
                 "Pixels look normal but the file was saved by an image editor after capture.",
                 [{"role": "passport", "file": self.save("tamper_editor_meta", "passport", self.genuine_scan(im, 15),
                                                         camera=cam, taken=t0, modified=t0 + timedelta(days=3, hours=4),
                                                         software="Adobe Photoshop 25.3 (Windows)")}],
                 self.save("tamper_editor_meta", "live", live_capture(self.face(A), 11)), ("MEDIUM", "HIGH"), ["META_EDITOR"])

        # 6 duplicated stamp (copy-move)
        pim, *_ = self.passport(A)
        vim, vlay, _ = self.visa(A, stamps=self.stamps_default()[:1])
        base = self.genuine_scan(vim, 16)
        sx, sy, sw, sh = vlay.boxes["stamps"][0]
        forged = X.copy_region(base, (sx, sy, sw, sh), (sx + 430, sy + 10))
        self.add("tamper_stamp_copy", "Entry stamp duplicated", "tampered", "A genuine stamp cloned to a second position.",
                 [{"role": "passport", "file": self.save("tamper_stamp_copy", "passport", self.genuine_scan(pim, 17), **photo_meta(t0))},
                  {"role": "visa", "file": self.save("tamper_stamp_copy", "visa", forged, camera=cam, taken=t0, modified=t0)}],
                 self.save("tamper_stamp_copy", "live", live_capture(self.face(A), 12)), ("MEDIUM", "HIGH"), ["STP_COPY_MOVE"])

        # 7 digitally drawn stamp (too clean)
        vim, vlay, _ = self.visa(A, stamps=self.stamps_default()[:1])
        base = self.genuine_scan(vim, 18)
        layer = R.ink_stamp(270, ["UTOPIA", "ENTRY", R.fmt_date(ref - timedelta(days=40))], (48, 56, 150), seed=9)
        arr = np.array(layer)
        arr[..., 3] = np.where(arr[..., 3] > 60, 255, 0).astype(np.uint8)
        forged = base.copy()
        R.paste_rotated(forged, Image.fromarray(arr), (690, 850), 5)
        pim, *_ = self.passport(A)
        self.add("tamper_stamp_digital", "Digitally drawn stamp", "tampered", "A crisp vector-like stamp added without ink texture.",
                 [{"role": "passport", "file": self.save("tamper_stamp_digital", "passport", self.genuine_scan(pim, 19), **photo_meta(t0))},
                  {"role": "visa", "file": self.save("tamper_stamp_digital", "visa", forged, camera=cam, taken=t0, modified=t0)}],
                 self.save("tamper_stamp_digital", "live", live_capture(self.face(A), 13)), ("MEDIUM", "HIGH"), ["STP_"])

        # 8 visa does not match passport
        pim, *_ = self.passport(A)
        vim, *_ = self.visa(A, passport_no="UT481917", stamps=self.stamps_default())
        self.add("tamper_visa_mismatch", "Visa issued for a different passport", "tampered",
                 "Visa references passport number UT481917 but the passport is UT482917.",
                 [{"role": "passport", "file": self.save("tamper_visa_mismatch", "passport", self.genuine_scan(pim, 20), **photo_meta(t0))},
                  {"role": "visa", "file": self.save("tamper_visa_mismatch", "visa", self.genuine_scan(vim, 21), **photo_meta(t0))}],
                 self.save("tamper_visa_mismatch", "live", live_capture(self.face(A), 14)), ("HIGH",), ["XC_NUMBER"])

        # 9 surname edited on an ID card
        im, lay, _ = self.card(B, "id_card", B.id_no)
        base = self.genuine_scan(im, 22)
        forged = X.edit_text(base, lay.boxes["surname"], "HARTMAN", 27)
        self.add("tamper_name_edit", "Surname edited on ID card", "tampered", "Name retyped; MRZ carries the original spelling.",
                 [{"role": "id_card", "file": self.save("tamper_name_edit", "id_card", forged, camera=cam, taken=t0, modified=t0)}],
                 self.save("tamper_name_edit", "live", live_capture(self.face(B), 15)), ("HIGH",), ["VAL_MRZ_VIZ_SURNAME"])

        # 10 impostor: genuine passport, someone else at the booth
        im, *_ = self.passport(A)
        self.add("impostor_live", "Impostor with genuine passport", "risky", "The document is genuine, the traveller is not the holder.",
                 [{"role": "passport", "file": self.save("impostor_live", "passport", self.genuine_scan(im, 23), **photo_meta(t0))}],
                 self.save("impostor_live", "live", live_capture(self.face(B), 16)), ("HIGH",), ["FACE_MISMATCH"])

        # 11 alias: same face, different identity (needs the first passport in the database)
        im, *_ = self.card(D, "id_card", D.id_no)
        self.add("alias_identity", "Same face, different identity", "risky",
                 "Valid-looking ID card in another name whose photo matches a person screened earlier.",
                 [{"role": "id_card", "file": self.save("alias_identity", "id_card", self.genuine_scan(im, 24), **photo_meta(t0))}],
                 self.save("alias_identity", "live", live_capture(self.face(D), 17)), ("HIGH",), ["ID_SAME_FACE"],
                 requires=["genuine_passport"])

        # 12 watchlist name + DOB (spelling variant)
        wl = R.Person("C", "KROWE", "VIKTOR ALDRIDGE", "M", date(1980, 2, 3), "Arcadia", passport_no=C.passport_no, face_index=1)
        im, *_ = self.passport(wl)
        self.add("watchlist_person", "Name and birth date on a SIMULATED watchlist", "risky",
                 "Traveller whose name (spelling variant) and DOB match a simulated alert entry.",
                 [{"role": "passport", "file": self.save("watchlist_person", "passport", self.genuine_scan(im, 25), **photo_meta(t0))}],
                 self.save("watchlist_person", "live", live_capture(self.face(wl), 18)), ("HIGH",), ["WL_PERSON"])

        # 13 stolen document number
        im, *_ = self.passport(A, number="UT990314")
        self.add("stolen_document", "Document number reported stolen (SIMULATED)", "risky",
                 "Passport number appears in the simulated lost/stolen registry.",
                 [{"role": "passport", "file": self.save("stolen_document", "passport", self.genuine_scan(im, 26), **photo_meta(t0))}],
                 self.save("stolen_document", "live", live_capture(self.face(A), 19)), ("HIGH",), ["WL_DOCUMENT"])

        # 14 poor capture quality
        im, *_ = self.passport(A)
        self.add("poor_quality", "Poor capture (blur, shadow, glare)", "risky", "Image quality too low for reliable reading - re-scan.",
                 [{"role": "passport", "file": self.save("poor_quality", "passport", X.damage(self.genuine_scan(im, 27)), **photo_meta(t0))}],
                 self.save("poor_quality", "live", live_capture(self.face(A), 20)), ("MEDIUM", "HIGH"), ["Q_"])

        # 15 issue date in the future (VIZ only)
        im, lay, _, _, _ = self.passport(A)
        base = self.genuine_scan(im, 28)
        fut = ref + timedelta(days=45)
        forged = X.edit_text(base, lay.boxes["issue"], R.fmt_date(fut), 29)
        self.add("tamper_issue_future", "Issue date set in the future", "tampered", "Date of issue retyped to a date after today.",
                 [{"role": "passport", "file": self.save("tamper_issue_future", "passport", forged, camera=cam, taken=t0, modified=t0)}],
                 self.save("tamper_issue_future", "live", live_capture(self.face(A), 21)), ("MEDIUM", "HIGH"), ["VAL_ISSUE_FUTURE"])
        return self.manifest


def build_catalog(out: Path | str = ROOT / "data" / "samples", ref: date | None = None, clean: bool = True) -> list[dict]:
    out = Path(out)
    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    ref = ref or date.today()
    b = Builder(out, ref, load_faces())
    manifest = b.build()
    (out / "manifest.json").write_text(json.dumps({"reference_date": ref.isoformat(), "samples": manifest}, indent=1))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "samples"))
    ap.add_argument("--ref", default=None, help="reference date YYYY-MM-DD (default: today)")
    a = ap.parse_args()
    m = build_catalog(a.out, date.fromisoformat(a.ref) if a.ref else None)
    print(f"built {len(m)} samples in {a.out}")
