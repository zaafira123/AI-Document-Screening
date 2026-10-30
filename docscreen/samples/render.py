"""Specimen document renderer.

Everything produced here is fictional: the issuing state is "Utopia" (ICAO
code UTO, the specimen state used in ICAO Doc 9303), every page carries a
SPECIMEN marking and all personal data is invented.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from docscreen.modules import mrz as M

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "assets" / "fonts"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


SANS, SANSB = "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"
SERIFB = "LiberationSerif-Bold.ttf"
MONO, MONOB = "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"
ALT_SANSB = "DejaVuSans-Bold.ttf"  # used by the "forger" - a different font


def fmt_date(d: date) -> str:
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"


@dataclass
class Person:
    key: str
    surname: str
    given: str
    sex: str
    dob: date
    birthplace: str
    nationality: str = "UTO"
    passport_no: str = ""
    id_no: str = ""
    licence_no: str = ""
    permit_no: str = ""
    address: str = ""
    face_index: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.given} {self.surname}"


@dataclass
class Layout:
    """Pixel boxes (x, y, w, h) of the editable fields, used by the forger."""
    boxes: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def guilloche(w: int, h: int, base=(232, 240, 246), line=(150, 185, 205), seed=1) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (w, h), base)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    xs = np.linspace(0, w, 140)
    for k in range(-20, h + 20, 7):
        ph, amp, fr = rng.uniform(0, 6.28), rng.uniform(5, 13), rng.uniform(0.012, 0.02)
        pts = [(x, k + amp * math.sin(x * fr + ph) + 4 * math.sin(x * 0.047 + k * 0.05)) for x in xs]
        d.line(pts, fill=line + (70,), width=1)
    img.paste(ov, (0, 0), ov)
    return img.filter(ImageFilter.GaussianBlur(0.35))


def text(d, xy, s, f, fill=(22, 28, 38), anchor="la"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def label_value(d, x, y, label, value, vfont=None, vfill=(22, 28, 38), lw=None):
    text(d, (x, y), label, font(SANS, 15), (86, 98, 114))
    vf = vfont or font(SANSB, 29)
    text(d, (x, y + 19), value, vf, vfill)
    width = int(d.textlength(value, font=vf))
    return (x, y + 17, max(width, lw or 0), 38)


def specimen_mark(img: Image.Image, strength=34, text_="SPECIMEN") -> Image.Image:
    w, h = img.size
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = font(SANSB, int(h * 0.16))
    tw = int(d.textlength(text_, font=f))
    tmp = Image.new("RGBA", (tw + 40, int(h * 0.22)), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((20, 0), text_, font=f, fill=(180, 40, 40, strength))
    tmp = tmp.rotate(18, expand=True, resample=Image.BICUBIC)
    ov.paste(tmp, ((w - tmp.width) // 2, (h - tmp.height) // 2), tmp)
    out = img.convert("RGBA")
    out.alpha_composite(ov)
    return out.convert("RGB")


def portrait_from(face_img: np.ndarray, box, size: tuple[int, int], bg=(228, 232, 238)) -> Image.Image:
    """Crop a passport-style portrait (face ~ 55% of width) from an RGB array."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h * 0.52
    cw = w / 0.52
    ch = cw * size[1] / size[0]
    x0, y0 = int(cx - cw / 2), int(cy - ch * 0.47)
    H, W = face_img.shape[:2]
    pad = int(max(cw, ch))
    padded = cv2.copyMakeBorder(face_img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    crop = padded[y0 + pad : y0 + pad + int(ch), x0 + pad : x0 + pad + int(cw)]
    img = Image.fromarray(crop).resize(size, Image.LANCZOS)
    return img


def draw_signature(d, x, y, seed):
    rng = np.random.default_rng(seed)
    pts, px, py = [], x, y
    for i in range(46):
        px += rng.uniform(2, 6)
        py = y + 14 * math.sin(i * 0.55 + seed) + rng.uniform(-6, 6) * (1 if i % 7 == 0 else 0.3)
        pts.append((px, py))
    d.line(pts, fill=(25, 35, 90), width=3, joint="curve")


def ink_stamp(size: int, lines: list[str], color=(48, 56, 150), seed=0, rect=False) -> Image.Image:
    """Round (or rectangular) rubber stamp on a transparent layer, with ink texture."""
    rng = np.random.default_rng(seed)
    S = size
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = color + (255,)
    if rect:
        d.rectangle((6, S * 0.22, S - 6, S * 0.78), outline=c, width=5)
        d.rectangle((14, S * 0.22 + 8, S - 14, S * 0.78 - 8), outline=c, width=2)
        ys = np.linspace(S * 0.30, S * 0.70, len(lines))
    else:
        d.ellipse((6, 6, S - 6, S - 6), outline=c, width=5)
        d.ellipse((22, 22, S - 22, S - 22), outline=c, width=2)
        ys = np.linspace(S * 0.30, S * 0.70, len(lines))
    for i, (ln, y) in enumerate(zip(lines, ys)):
        f = font(SANSB, int(S * (0.15 if i == len(lines) // 2 else 0.095)))
        d.text((S / 2, y), ln, font=f, fill=c, anchor="mm")
    a = np.array(im)
    noise = cv2.GaussianBlur(rng.random((S, S)).astype(np.float32), (0, 0), 1.3)
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    mask = (noise > 0.22).astype(np.float32) * (0.62 + 0.38 * noise)
    a[..., 3] = (a[..., 3] * mask).astype(np.uint8)
    return Image.fromarray(a)


def paste_rotated(base: Image.Image, layer: Image.Image, center, angle):
    r = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    base.paste(r, (int(center[0] - r.width / 2), int(center[1] - r.height / 2)), r)


# ---------------------------------------------------------------------------
# document renderers - every function returns (PIL image, Layout, mrz_lines)
# ---------------------------------------------------------------------------
def render_passport(p: Person, portrait: Image.Image, issue: date, expiry: date, number: str,
                    seed: int = 1, mrz_lines: list[str] | None = None) -> tuple[Image.Image, Layout, list[str]]:
    W, H = 1000, 704
    img = guilloche(W, H, seed=seed)
    d = ImageDraw.Draw(img)
    lay = Layout()
    d.rectangle((0, 0, W, 74), fill=(24, 62, 112))
    text(d, (40, 18), "REPUBLIC OF UTOPIA", font(SANSB, 32), (255, 255, 255))
    text(d, (W - 40, 22), "PASSPORT  /  PASSEPORT", font(SANSB, 24), (240, 205, 80), "ra")
    # photo + ghost image
    pw, ph = 262, 340
    img.paste(portrait.resize((pw, ph)), (40, 100))
    d.rectangle((40, 100, 40 + pw, 100 + ph), outline=(60, 80, 100), width=2)
    lay.boxes["photo"] = (40, 100, pw, ph)
    ghost = portrait.convert("L").resize((130, 168)).convert("RGB")
    ghost = Image.blend(ghost, Image.new("RGB", ghost.size, (232, 240, 246)), 0.66)
    img.paste(ghost, (838, 450))
    lay.boxes["ghost"] = (838, 450, 130, 168)

    x1, x2, x3 = 330, 450, 640
    text(d, (x1, 90), "Type / Type", font(SANS, 15), (86, 98, 114))
    text(d, (x1, 109), "P", font(SANSB, 29))
    label_value(d, x2, 90, "Code / Code", "UTO")
    lay.boxes["number"] = label_value(d, x3, 90, "Passport No. / N° du passeport", number)
    lay.boxes["surname"] = label_value(d, x1, 160, "Surname / Nom", p.surname.upper())
    lay.boxes["given"] = label_value(d, x1, 230, "Given names / Prénoms", p.given.upper())
    label_value(d, x1, 300, "Nationality / Nationalité", "UTOPIAN")
    lay.boxes["dob"] = label_value(d, x3, 300, "Date of birth / Date de naissance", fmt_date(p.dob))
    label_value(d, x1, 370, "Sex / Sexe", p.sex)
    label_value(d, x2 + 20, 370, "Place of birth / Lieu de naissance", p.birthplace.upper())
    lay.boxes["issue"] = label_value(d, x1, 440, "Date of issue / Date de délivrance", fmt_date(issue))
    lay.boxes["expiry"] = label_value(d, x3, 440, "Date of expiry / Date d'expiration", fmt_date(expiry))
    label_value(d, x1, 510, "Authority / Autorité", "IMMIGRATION SERVICE", font(SANSB, 24))
    text(d, (40, 456), "Holder's signature / Signature du titulaire", font(SANS, 14), (86, 98, 114))
    draw_signature(d, 60, 500, seed)

    if mrz_lines is None:
        mrz_lines = M.build_td3(issuer="UTO", surname=p.surname, given=p.given, number=number,
                                nationality=p.nationality, dob=p.dob, sex=p.sex, expiry=expiry)
    d.rectangle((0, 566, W, H), fill=(252, 252, 250))
    mf = font(MONO, 35)
    for i, ln in enumerate(mrz_lines):
        text(d, (36, 582 + i * 50), ln, mf, (18, 18, 22))
    lay.boxes["mrz"] = (30, 575, 940, 110)
    lay.boxes["mrz_line1"] = (30, 575, 940, 50)
    lay.boxes["mrz_line2"] = (30, 625, 940, 50)
    return specimen_mark(img), lay, mrz_lines


def render_visa_page(p: Person, portrait: Image.Image, visa_no: str, passport_no: str, valid_from: date,
                     valid_until: date, entries: str, stay_days: int, visa_type: str = "TOURIST",
                     stamps: list[dict] | None = None, seed: int = 3,
                     mrz_lines: list[str] | None = None) -> tuple[Image.Image, Layout, list[str]]:
    W, H = 1000, 1090
    page = guilloche(W, H, base=(246, 242, 232), line=(200, 190, 160), seed=seed)
    img = page.copy()
    d = ImageDraw.Draw(img)
    lay = Layout()
    # sticker
    sx0, sy0, sx1, sy1 = 30, 30, 970, 650
    d.rectangle((sx0, sy0, sx1, sy1), fill=(238, 244, 240), outline=(40, 110, 90), width=4)
    sticker = guilloche(sx1 - sx0 - 8, sy1 - sy0 - 8, base=(236, 245, 240), line=(120, 190, 165), seed=seed + 9)
    img.paste(sticker, (sx0 + 4, sy0 + 4))
    d = ImageDraw.Draw(img)
    d.rectangle((sx0 + 4, sy0 + 4, sx1 - 4, sy0 + 72), fill=(30, 100, 82))
    text(d, (56, 46), "REPUBLIC OF UTOPIA", font(SANSB, 30), (255, 255, 255))
    text(d, (sx1 - 28, 50), "VISA  /  VISA", font(SANSB, 26), (240, 215, 110), "ra")
    img.paste(portrait.resize((190, 246)), (56, 112))
    d.rectangle((56, 112, 246, 358), outline=(50, 80, 70), width=2)
    lay.boxes["photo"] = (56, 112, 190, 246)
    x1, x2, x3 = 276, 560, 770
    lay.boxes["visa_no"] = label_value(d, x1, 100, "Visa No. / N° de visa", visa_no)
    label_value(d, x3, 100, "Type / Catégorie", visa_type, font(SANSB, 25))
    lay.boxes["valid_from"] = label_value(d, x1, 168, "Valid from / Valable du", fmt_date(valid_from))
    lay.boxes["valid_until"] = label_value(d, x2, 168, "Valid until / Jusqu'au", fmt_date(valid_until))
    label_value(d, x1, 236, "Entries / Entrées", entries, font(SANSB, 26))
    lay.boxes["stay"] = label_value(d, x2, 236, "Duration of stay / Durée du séjour", f"{stay_days} DAYS", font(SANSB, 26))
    lay.boxes["surname"] = label_value(d, x1, 304, "Surname / Nom", p.surname.upper(), font(SANSB, 26))
    lay.boxes["given"] = label_value(d, x2, 304, "Given names / Prénoms", p.given.upper(), font(SANSB, 26))
    lay.boxes["passport_no"] = label_value(d, x1, 372, "Passport No. / N° du passeport", passport_no, font(SANSB, 26))
    label_value(d, x2, 372, "Date of birth / Date de naissance", fmt_date(p.dob), font(SANSB, 26))
    label_value(d, x1, 440, "Nationality / Nationalité", "UTOPIAN", font(SANSB, 24))
    label_value(d, x2, 440, "Sex / Sexe", p.sex, font(SANSB, 24))
    if mrz_lines is None:
        mrz_lines = M.build_mrva(issuer="UTO", surname=p.surname, given=p.given, number=visa_no,
                                 nationality=p.nationality, dob=p.dob, sex=p.sex, expiry=valid_until,
                                 optional=passport_no)
    d.rectangle((sx0 + 4, 530, sx1 - 4, sy1 - 4), fill=(252, 252, 250))
    mf = font(MONO, 33)
    for i, ln in enumerate(mrz_lines):
        text(d, (48, 548 + i * 48), ln, mf, (18, 18, 22))
    lay.boxes["mrz"] = (44, 540, 900, 102)
    # stamps below the sticker
    lay.boxes["stamps"] = []
    for i, st in enumerate(stamps or []):
        layer = ink_stamp(st.get("size", 250), st["lines"], st.get("color", (48, 56, 150)), seed=st.get("seed", i), rect=st.get("rect", False))
        cx, cy, ang = st["center"], st.get("cy", 860), st.get("angle", 0)
        paste_rotated(img, layer, (cx, cy), ang)
        s = st.get("size", 250)
        lay.boxes["stamps"].append((int(cx - s / 2), int(cy - s / 2), s, s))
    return specimen_mark(img, 26), lay, mrz_lines


def render_card(p: Person, portrait: Image.Image, kind: str, number: str, issue: date, expiry: date,
                seed: int = 5, mrz_lines: list[str] | None = None, permit: dict | None = None):
    """kind: id_card | permit (TD1 MRZ) or driving_licence (no MRZ)."""
    W, H = 1000, 631
    palette = {"id_card": ((228, 236, 246), (140, 170, 210), (28, 66, 128), "NATIONAL IDENTITY CARD"),
               "permit": ((240, 236, 226), (200, 180, 140), (120, 60, 30), "WORK PERMIT"),
               "driving_licence": ((246, 232, 236), (215, 150, 170), (150, 30, 70), "DRIVING LICENCE")}
    base, line, head, title = palette[kind]
    img = guilloche(W, H, base=base, line=line, seed=seed)
    d = ImageDraw.Draw(img)
    lay = Layout()
    d.rectangle((0, 0, W, 68), fill=head)
    text(d, (36, 16), "REPUBLIC OF UTOPIA", font(SANSB, 30), (255, 255, 255))
    text(d, (W - 36, 22), title, font(SANSB, 24), (245, 220, 120), "ra")
    pw, ph = 218, 282
    py = 92
    img.paste(portrait.resize((pw, ph)), (36, py))
    d.rectangle((36, py, 36 + pw, py + ph), outline=(60, 70, 90), width=2)
    lay.boxes["photo"] = (36, py, pw, ph)
    ghost = portrait.convert("L").resize((98, 126)).convert("RGB")
    ghost = Image.blend(ghost, Image.new("RGB", ghost.size, base), 0.68)
    img.paste(ghost, (W - 150, 84 if kind != "driving_licence" else 330))
    x1, x2 = 290, 640
    if kind == "driving_licence":
        text(d, (x1, 84), "1.", font(SANSB, 18), head)
        lay.boxes["surname"] = label_value(d, x1 + 26, 80, "Surname", p.surname.upper(), font(SANSB, 27))
        text(d, (x1, 150), "2.", font(SANSB, 18), head)
        lay.boxes["given"] = label_value(d, x1 + 26, 146, "Given names", p.given.upper(), font(SANSB, 27))
        text(d, (x1, 216), "3.", font(SANSB, 18), head)
        lay.boxes["dob"] = label_value(d, x1 + 26, 212, "Date and place of birth", f"{fmt_date(p.dob)}  {p.birthplace.upper()}", font(SANSB, 23))
        text(d, (x1, 282), "4a.", font(SANSB, 18), head)
        lay.boxes["issue"] = label_value(d, x1 + 38, 278, "Issued", fmt_date(issue), font(SANSB, 25))
        text(d, (x2, 282), "4b.", font(SANSB, 18), head)
        lay.boxes["expiry"] = label_value(d, x2 + 38, 278, "Expires", fmt_date(expiry), font(SANSB, 25))
        text(d, (x1, 348), "5.", font(SANSB, 18), head)
        lay.boxes["number"] = label_value(d, x1 + 26, 344, "Licence No.", number, font(SANSB, 27))
        text(d, (x2, 348), "9.", font(SANSB, 18), head)
        label_value(d, x2 + 26, 344, "Categories", "A  B  C1", font(SANSB, 27))
        text(d, (x1, 414), "8.", font(SANSB, 18), head)
        label_value(d, x1 + 26, 410, "Address", p.address.upper(), font(SANSB, 22))
        text(d, (36, 400), "Holder's signature", font(SANS, 14), (86, 98, 114))
        draw_signature(d, 50, 450, seed)
        d.rectangle((0, 540, W, H), fill=head)
        text(d, (36, 566), "SPECIMEN - NOT VALID FOR DRIVING", font(SANSB, 26), (255, 255, 255))
        return specimen_mark(img, 26), lay, []

    lay.boxes["surname"] = label_value(d, x1, 84, "Surname / Nom", p.surname.upper(), font(SANSB, 27))
    lay.boxes["given"] = label_value(d, x1, 150, "Given names / Prénoms", p.given.upper(), font(SANSB, 27))
    label_value(d, x1, 216, "Sex / Sexe", p.sex, font(SANSB, 25))
    label_value(d, x1 + 110, 216, "Nationality / Nationalité", "UTOPIAN", font(SANSB, 25))
    lay.boxes["dob"] = label_value(d, x2 + 20, 216, "Date of birth / Date de naissance", fmt_date(p.dob), font(SANSB, 25))
    lay.boxes["number"] = label_value(d, x1, 282, "Document No. / N° du document", number, font(SANSB, 25))
    lay.boxes["expiry"] = label_value(d, x2 + 20, 282, "Date of expiry / Date d'expiration", fmt_date(expiry), font(SANSB, 25))
    if kind == "permit":
        permit = permit or {}
        label_value(d, x1, 348, "Permit type / Type de permis", permit.get("type", "WORK - SKILLED"), font(SANSB, 22))
        lay.boxes["issue"] = label_value(d, x2 + 20, 348, "Valid from / Valable du", fmt_date(issue), font(SANSB, 22))
    else:
        label_value(d, x1, 348, "Place of birth / Lieu de naissance", p.birthplace.upper(), font(SANSB, 22))
        lay.boxes["issue"] = label_value(d, x2 + 20, 348, "Date of issue / Date de délivrance", fmt_date(issue), font(SANSB, 22))
    if mrz_lines is None:
        code = "IR" if kind == "permit" else "I"
        mrz_lines = M.build_td1(doc_code=code, issuer="UTO", surname=p.surname, given=p.given, number=number,
                                nationality=p.nationality, dob=p.dob, sex=p.sex, expiry=expiry)
    d.rectangle((0, 424, W, H), fill=(252, 252, 250))
    mf = font(MONO, 50)
    for i, ln in enumerate(mrz_lines):
        text(d, (22, 432 + i * 63), ln, mf, (18, 18, 22))
    lay.boxes["mrz"] = (18, 430, 960, 195)
    return specimen_mark(img, 26), lay, mrz_lines
