"""Image-file metadata: editing-software tags, timestamp consistency, provenance."""
from __future__ import annotations

import io
import re
from datetime import datetime, date

from PIL import Image

from .result import finding, result

EDITORS = re.compile(r"photoshop|gimp|lightroom|paint\.net|pixlr|affinity|canva|picsart|snapseed|fotor|illustrator|"
                     r"coreldraw|photopea|paintshop|krita|inkscape|mspaint|microsoft paint|ps express", re.I)
GENERIC_SW = re.compile(r"adobe|acrobat|preview|imagemagick|pillow|libvips|photo", re.I)


def _dt(s):
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s)[:19], fmt)
        except ValueError:
            pass
    return None


def analyse(data: bytes, ref: date) -> dict:
    fs, det = [], {"has_exif": False}
    try:
        im = Image.open(io.BytesIO(data))
        exif = im.getexif()
    except Exception as e:
        return result([finding("low", "META_UNREADABLE", "Image metadata could not be read.")], {"error": type(e).__name__})
    ifd = {}
    try:
        ifd = exif.get_ifd(0x8769)
    except Exception:
        pass
    sw = exif.get(0x0131)
    make, model = exif.get(0x010F), exif.get(0x0110)
    mod, orig = _dt(exif.get(0x0132)), _dt(ifd.get(0x9003))
    xmp = b"<x:xmpmeta" in data[:200000]
    tool = None
    if xmp:
        m = re.search(rb'CreatorTool="([^"]+)"|<xmp:CreatorTool>([^<]+)<', data[:200000])
        if m:
            tool = (m.group(1) or m.group(2)).decode("utf8", "ignore")
    det.update(has_exif=bool(len(exif)), software=sw, make=make, model=model, modified=str(mod) if mod else None,
               original=str(orig) if orig else None, xmp=xmp, xmp_tool=tool, format=im.format, dpi=im.info.get("dpi"))
    soft = " / ".join(x for x in (sw, tool) if x)
    if soft and EDITORS.search(soft):
        fs.append(finding("high", "META_EDITOR_SOFTWARE", f"File was last saved by image-editing software ({soft}). A genuine scan or camera file "
                                                         "does not normally carry an editor tag."))
    elif soft and not GENERIC_SW.search(soft):
        fs.append(finding("low", "META_SOFTWARE", f"File carries a software tag ({soft})."))
    if mod and orig and abs((mod - orig).total_seconds()) > 86400:
        fs.append(finding("medium", "META_DATE_MISMATCH",
                          f"File was modified {mod:%Y-%m-%d %H:%M} but captured {orig:%Y-%m-%d %H:%M} - it was changed after capture."))
    for lab, t in (("captured", orig), ("modified", mod)):
        if t and t.date() > ref:
            fs.append(finding("medium", "META_DATE_FUTURE", f"File {lab} date ({t:%Y-%m-%d}) is after the screening date."))
    if not len(exif) and not xmp:
        fs.append(finding("info", "META_NONE", "No capture metadata in the file (normal for screenshots, forwarded or cleaned images)."))
    elif make or model:
        fs.append(finding("info", "META_DEVICE", f"Captured with {' '.join(str(x) for x in (make, model) if x)}."))
    return result(fs, det)
