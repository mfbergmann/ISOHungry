#!/usr/bin/env python3
"""
Read a DVD case's back cover for the list of special features.

The disc's menus garble under OCR because their text sits on moving video. The
back of the case does not: it is flat, printed, high-contrast, and whoever is
holding it controls the framing and the light. It is close to tesseract's best
case where the menu is close to its worst.

What the cover cannot do is say which title each feature is. It lists them as
marketing copy, with no durations and no guaranteed order. So this is the other
half of a pair: the menu knows how many extras there are and which title each
one plays, the cover knows how they are spelled.

    cover_ocr.py <image>            print the features found on a cover photo
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

# Where the list starts. Studios are not consistent, so this is generous.
HEADINGS = re.compile(
    r"\b(special\s+features?|bonus\s+(features?|materials?|content)|"
    r"extras?|additional\s+(features?|content)|special\s+bonus|"
    r"disc\s+extras?|features?\s+include)\b", re.I)

# Where it stops: the legal and technical block that follows on every case.
ENDINGS = re.compile(
    r"\b(closed\s+caption|subtitl|aspect\s+ratio|dolby|dts|surround|"
    r"running\s+time|rated\s|not\s+rated|all\s+rights\s+reserved|"
    r"©|\(c\)\s*\d{4}|distributed\s+by|manufactured|warner|paramount|"
    r"universal\s+studios|columbia|region\s+[0-9]|anamorphic|widescreen\s+version|"
    r"languages?:|audio:|video:|presented\s+in)\b", re.I)

# A leading bullet in any of the forms print and OCR produce between them.
# Tesseract renders a round bullet as "e", "o", "0", "@" or "©" more often than
# not, so those count too - but only when what follows starts like a title,
# otherwise a sentence beginning "or ..." would be mistaken for an item.
BULLET = re.compile(r"^\s*[•·▪●∙\*\-–—o0e@©]\s+(?=[\"\x27A-Z0-9])")

# Studios pad the list with these; they are not extras anyone wants filed.
NOT_A_FEATURE = re.compile(
    r"^\W*(and\s+more|much\s+more|plus\s+more|more!?|"
    r"scene\s+selection|chapter\s+selection|interactive\s+menus?|"
    r"languages?|subtitles?|audio\s+options?)\W*$", re.I)


def ocr_image(path, psm="6", extra_vf=None):
    """OCR an image, upscaling small photos so tesseract has pixels to work with."""
    with tempfile.TemporaryDirectory(prefix="cover-") as work:
        prepared = os.path.join(work, "p.png")
        vf = extra_vf or "scale='min(2400,iw*2)':-1:flags=lanczos,format=gray"
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-vf", vf, prepared],
            capture_output=True, timeout=180)
        if proc.returncode != 0 or not os.path.exists(prepared):
            prepared = path                      # let tesseract try the original
        out = subprocess.run(["tesseract", prepared, "stdout", "--psm", psm],
                             capture_output=True, timeout=180)
        return out.stdout.decode("utf-8", "replace")


def _clean_item(text):
    text = BULLET.sub("", text)
    text = " ".join(text.split())
    text = text.strip(" .;:*-–—•·")
    # OCR turns a bullet into a leading "e" or "©" surprisingly often.
    text = re.sub(r"^[e©@]\s+(?=[A-Z])", "", text)
    return text


def _plausible(text):
    if len(text) < 3 or len(text) > 90:
        return False
    if NOT_A_FEATURE.match(text):
        return False
    letters = sum(c.isalpha() for c in text)
    return letters >= 3 and letters >= len(text) * 0.5


def parse_features(text):
    """Pull the special-features list out of OCRed cover text.

    Two shapes have to work. Most covers bullet the list, in which case the
    bullets are the items. Some run it as prose after the heading, in which
    case the separators are the only structure available.
    """
    lines = [l.rstrip() for l in text.splitlines()]

    start = None
    for i, line in enumerate(lines):
        if HEADINGS.search(line):
            start = i
            break
    if start is None:
        return [], "no special-features heading found"

    # The heading line itself sometimes carries the first item after a colon.
    items, tail = [], []
    head_rest = re.split(r"[:–\-]", lines[start], 1)
    if len(head_rest) > 1 and len(head_rest[1].strip()) > 3:
        tail.append(head_rest[1])

    for line in lines[start + 1:]:
        if ENDINGS.search(line):
            break
        if not line.strip():
            # A blank line after we have items usually means the list ended.
            if items or tail:
                blanks = 1
                continue
        tail.append(line)

    bulleted = [l for l in tail if BULLET.match(l)]
    if len(bulleted) >= 2:
        source = bulleted                         # trust the printed bullets
    else:
        # Prose: split on the separators studios use between features.
        joined = " ".join(l.strip() for l in tail if l.strip())
        source = re.split(r"\s*[•·▪●]\s*|\s\|\s|;\s*", joined)
        if len(source) < 2:
            source = re.split(r",\s+(?=[A-Z0-9])", joined)

    for raw in source:
        item = _clean_item(raw)
        if _plausible(item):
            items.append(item)

    # OCR repeats lines when a photo is skewed; keep first occurrences.
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out, None if out else "heading found but no features parsed under it"


def read_cover(path):
    """Best-effort features list, trying a couple of page-segmentation modes."""
    best, best_err = [], "could not read the image"
    for psm in ("6", "4", "3"):
        text = ocr_image(path, psm=psm)
        items, err = parse_features(text)
        if len(items) > len(best):
            best, best_err = items, err
        if len(best) >= 3:
            break
    return best, (None if best else best_err)


def main():
    p = argparse.ArgumentParser(description="Read a DVD back cover for extras.")
    p.add_argument("image")
    p.add_argument("--raw", action="store_true", help="dump the raw OCR text too")
    args = p.parse_args()
    if args.raw:
        print(ocr_image(args.image))
        print("-" * 40)
    items, err = read_cover(args.image)
    if err:
        print("error: %s" % err, file=sys.stderr)
        sys.exit(1)
    for n, item in enumerate(items, 1):
        print("%2d. %s" % (n, item))


if __name__ == "__main__":
    main()
