#!/usr/bin/env python3
"""
Read a DVD's own menus to recover the names of its extras.

Every disc that has extras has a menu naming them — that is what a menu is for.
The names are not text on the disc, though: they are pixels in the menu's video,
so they have to be found and read rather than looked up.

What makes that tractable is the button table. A DVD's buttons are not in the
IFO; they live in the highlight information of each menu VOBU's navigation pack,
and every button carries a screen rectangle. The rectangle says where its label
is drawn. So instead of OCRing a whole menu and guessing which words go with
which item, each button's own region is cropped and read on its own.

Accuracy is decent but never certain — menu text sits on top of moving video,
which is close to the worst case for OCR. Everything here is therefore a
*suggestion* that a human confirms, and it ranks below a TheDiscDb hit, which
is exact. See docs/EXTRAS.md.

    dvdmenu.py scan <iso>          list the menus and the labels read from them
    dvdmenu.py frames <iso> --out  dump the rendered menu frames to look at
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

SECTOR = 2048

# PCI packet layout (libdvdread nav_types.h), offsets within the PCI payload:
#   0   pci_gi     60 bytes
#   60  nsml_agli  36 bytes
#   96  hli        -> hl_gi 22, btn_colit 24, then up to 36 btni_t of 18 bytes
HL_GI_OFF = 96
# hl_gi_t is hli_ss(2) hli_s_ptm(4) hli_e_ptm(4) btn_se_e_ptm(4) + 2 bytes of
# btngr bitfields = 16, then btn_ofn(1), btn_ns(1). Reading the count at +16
# gets btn_ofn instead — the index this button group starts at, which on a
# paged menu looks like a plausible count (4, 8, 12 …) and quietly truncates
# every menu.
BTN_OFN_OFF = HL_GI_OFF + 16
BTN_NS_OFF = HL_GI_OFF + 17
BTNIT_OFF = HL_GI_OFF + 22 + 24

# Commands that mean "play something" or "go somewhere", by first byte.
CMD_JUMP = 0x30          # JumpTT / JumpVTS_TT / JumpVTS_PTT
CMD_LINK = 0x20          # LinkPGCN and friends, within the menu domain
CMD_SETLINK = 0x71       # set a register then link — how most extras menus work

# Menu furniture, not content. Matched against the OCR result to drop the
# navigation buttons that appear on every page.
CHROME = re.compile(
    r"^\W*(main\s*menu|resume|resume\s*film|back|more|next|previous|prev|play|"
    r"play\s*(all|movie|film)|scene\s*selection|special\s*features|setup|"
    r"languages?|language\s*selection|subtitles?|audio|top\s*menu|title\s*menu|"
    r"chapters?)\W*$", re.I)


# A _score at or above this reads like a real title — two or three clean words.
# Reaching it stops the search for a better rendering of the same button.
GOOD_ENOUGH = 18


class MenuError(Exception):
    pass


# ------------------------------------------------------------------ nav packs

def find_nav_packs(data):
    """Yield (sector_index, pci_payload) for every navigation pack in a VOB."""
    for i in range(0, len(data) - SECTOR + 1, SECTOR):
        s = data[i:i + SECTOR]
        if s[0:4] != b"\x00\x00\x01\xba":
            continue
        off = 14                                     # pack header
        if s[off:off + 4] == b"\x00\x00\x01\xbb":    # system header
            off += 6 + struct.unpack(">H", s[off + 4:off + 6])[0]
        if s[off:off + 4] != b"\x00\x00\x01\xbf":    # private stream 2
            continue
        length = struct.unpack(">H", s[off + 4:off + 6])[0]
        if s[off + 6] != 0x00:                       # 0x00 = PCI, 0x01 = DSI
            continue
        yield i // SECTOR, s[off + 7:off + 6 + length]


def parse_buttons(pci):
    """Button rectangles and VM commands from a PCI packet's highlight block."""
    if len(pci) < BTNIT_OFF:
        return []
    btn_ns = pci[BTN_NS_OFF]
    if not 1 <= btn_ns <= 36:
        return []
    out = []
    for n in range(btn_ns):
        b = pci[BTNIT_OFF + n * 18: BTNIT_OFF + (n + 1) * 18]
        if len(b) < 18:
            break
        w1 = struct.unpack(">I", b[0:3] + b"\x00")[0] >> 8      # 24 bits
        w2 = struct.unpack(">I", b[3:6] + b"\x00")[0] >> 8
        rect = ((w1 >> 12) & 0x3FF, (w2 >> 12) & 0x3FF,
                w1 & 0x3FF, w2 & 0x3FF)                          # x0,y0,x1,y1
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue                                             # empty button
        out.append({"n": n + 1, "rect": rect, "cmd": bytes(b[10:18])})
    return out


def describe_cmd(cmd):
    """A short label for what a button does, where it is worth knowing."""
    if cmd[0] == CMD_JUMP:
        sub = cmd[1]
        if sub == 0x02:
            return "JumpTT", cmd[5]
        if sub == 0x03:
            return "JumpVTS_TT", cmd[5]
        if sub == 0x05:
            return "JumpVTS_PTT", cmd[5]
        return "Jump%02x" % sub, None
    if cmd[0] == CMD_LINK:
        return "Link", None
    if cmd[0] == CMD_SETLINK:
        return "SetLink", None
    if not any(cmd):
        return "NOP", None
    return "cmd%02x" % cmd[0], None


# ------------------------------------------------------------------- the disc

def list_menu_vobs(iso_path):
    out = subprocess.run(["isoinfo", "-l", "-i", iso_path],
                         capture_output=True, timeout=300)
    text = out.stdout.decode("utf-8", "replace")
    return sorted(set(re.findall(r"(VIDEO_TS\.VOB|VTS_\d+_0\.VOB);1", text)))


def extract(iso_path, name, dest):
    with open(dest, "wb") as fh:
        proc = subprocess.run(["isoinfo", "-i", iso_path, "-x",
                               "/VIDEO_TS/%s;1" % name],
                              stdout=fh, stderr=subprocess.DEVNULL, timeout=900)
    return proc.returncode == 0 and os.path.getsize(dest) > 0


def find_menus(vob_path):
    """Distinct menus in a VOB: one entry per unique set of buttons."""
    with open(vob_path, "rb") as fh:
        data = fh.read()
    menus, seen = [], set()
    for sector, pci in find_nav_packs(data):
        btns = parse_buttons(pci)
        if not btns:
            continue
        key = tuple((b["cmd"], b["rect"]) for b in btns)
        if key in seen:
            continue
        seen.add(key)
        menus.append({"sector": sector, "buttons": btns})
    return menus


# ------------------------------------------------------------------------ OCR

def render_frames(vob_path, sector, out_dir, count=4):
    """A few frames from where a menu starts.

    Menu text is static while the video behind it moves, so reading several
    frames and taking the best result beats trusting whichever frame happened
    to be first.
    """
    chunk = os.path.join(out_dir, "chunk.vob")
    with open(vob_path, "rb") as src, open(chunk, "wb") as dst:
        src.seek(sector * SECTOR)
        dst.write(src.read(3000 * SECTOR))
    frames = []
    for n in range(count):
        path = os.path.join(out_dir, "f%d.png" % n)
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", chunk,
             "-vf", "select=eq(n\\,%d)" % (n * 6), "-frames:v", "1", path],
            capture_output=True, timeout=300)
        if proc.returncode == 0 and os.path.exists(path):
            frames.append(path)
    return frames


def _score(text):
    """How much this reads like a title rather than OCR noise."""
    if not text:
        return -1
    letters = sum(c.isalpha() for c in text)
    junk = sum(1 for c in text if not (c.isalnum() or c in " '&#!?,.:-—’()/"))
    words = [w for w in re.split(r"\s+", text) if len(w) > 1]
    if letters < 3:
        return -1
    # Long runs of real words score well; stray punctuation is penalised hard.
    return letters + 3 * len(words) - 5 * junk - max(0, len(text) - 60)


def _clean(text):
    text = " ".join(text.split())
    text = re.sub(r"[|_~^`<>{}\[\]\\]+", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"(^|\s)[^\w'&#(]+(\s|$)", " ", text)
    return " ".join(text.split()).strip(" -–—.,:;")


def ocr_button(frames, rect, work_dir, tag):
    """Read one button's label, best-of across frames and thresholds.

    The crop runs from the left edge of the screen to the button's right edge:
    labels are drawn left-aligned and the highlight rectangle tends to sit over
    the tail of the text, so anchoring on its right edge keeps the label and
    excludes whatever busy video sits beside it.
    """
    x0, y0, x1, y1 = rect
    top = max(0, y0 - 7)
    height = (y1 - y0) + 14
    width = max(40, x1 + 8 - 40)
    best, best_score = "", -1
    # Thresholds in the order that has worked best on real menus, and stop as
    # soon as a read looks like a title: every combination tried on every
    # button costs a whole disc scan several minutes for no extra accuracy.
    for fi, frame in enumerate(frames):
        if best_score >= GOOD_ENOUGH:
            break
        for thresh in (200, 170, 225):
            if best_score >= GOOD_ENOUGH:
                break
            png = os.path.join(work_dir, "ocr_%s_%d_%d.png" % (tag, fi, thresh))
            vf = ("crop=%d:%d:40:%d,format=gray,"
                  "lut=y='if(gt(val,%d),0,255)',scale=iw*5:ih*5:flags=lanczos"
                  % (width, height, top, thresh))
            r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                                "-i", frame, "-vf", vf, png],
                               capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(png):
                continue
            t = subprocess.run(["tesseract", png, "stdout", "--psm", "6"],
                               capture_output=True, timeout=120)
            text = _clean(t.stdout.decode("utf-8", "replace"))
            s = _score(text)
            if s > best_score:
                best, best_score = text, s
            try:
                os.remove(png)
            except OSError:
                pass
    return best, best_score


# ---------------------------------------------------------------------- scan

def scan(iso_path, work_dir=None, want_frames=3, skip_chapters=True):
    """Every menu on the disc, with a label read for each content button."""
    owns_dir = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="dvdmenu-")
    results = []
    read_buttons = set()
    try:
        for name in list_menu_vobs(iso_path):
            vob = os.path.join(work_dir, name)
            if not extract(iso_path, name, vob):
                continue
            for menu in find_menus(vob):
                # Buttons that go somewhere content-ish. Pure Link buttons are
                # page furniture (next/back/main menu) far more often than not,
                # but they are kept when a menu is nothing but links, because
                # some discs really do reach their extras that way.
                content = [b for b in menu["buttons"]
                           if b["cmd"][0] in (CMD_JUMP, CMD_SETLINK)]
                if not content:
                    continue
                # Scene-selection pages jump to chapters of the feature. They
                # name chapters, not extras, and a paged one carries fifteen
                # buttons — by far the most expensive thing on the disc to OCR
                # for no benefit. Drop them before rendering, not after.
                if skip_chapters and all(
                        describe_cmd(b["cmd"])[0] == "JumpVTS_PTT"
                        for b in content):
                    continue
                # The same button often appears on several pages of a paged
                # menu. Read each distinct one once.
                content = [b for b in content
                           if (b["cmd"], b["rect"]) not in read_buttons]
                if not content:
                    continue
                for b in content:
                    read_buttons.add((b["cmd"], b["rect"]))

                frames = render_frames(vob, menu["sector"], work_dir,
                                       count=want_frames)
                if not frames:
                    continue
                items = []
                for b in content:
                    label, score = ocr_button(
                        frames, b["rect"], work_dir,
                        "%s_%d_%d" % (name, menu["sector"], b["n"]))
                    kind, target = describe_cmd(b["cmd"])
                    if not label or CHROME.match(label):
                        continue
                    items.append({"button": b["n"], "label": label,
                                  "score": score, "kind": kind,
                                  "target": target, "rect": b["rect"],
                                  "cmd": b["cmd"].hex()})
                for f in frames:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                if items:
                    results.append({"vob": name, "sector": menu["sector"],
                                    "items": items})
    finally:
        if owns_dir:
            subprocess.run(["rm", "-rf", work_dir], check=False)
    return results


def menu_names(iso_path, menus=None, **kw):
    """A flat, ordered list of the extra names the menus advertise.

    Order is menu order then button order, which is the order a disc lists its
    extras in and — usually, not always — the order of their title numbers.
    Deduplicated, because paged menus repeat their neighbours.
    """
    names, seen = [], set()
    for menu in (menus if menus is not None else scan(iso_path, **kw)):
        # Scene-selection pages are chapter jumps into the feature; they name
        # chapters, not extras.
        if all(i["kind"] == "JumpVTS_PTT" for i in menu["items"]):
            continue
        for item in menu["items"]:
            key = item["label"].lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(item)
    return names


# ------------------------------------------------------------------------ CLI

def cmd_scan(args):
    menus = scan(args.iso, want_frames=args.frames)
    if not menus:
        print("no menus with readable buttons found")
        return
    for m in menus:
        kinds = {i["kind"] for i in m["items"]}
        print("\n%s sector %d — %d items (%s)"
              % (m["vob"], m["sector"], len(m["items"]), ", ".join(sorted(kinds))))
        for i in m["items"]:
            print("   btn %2d  %-38s [%s%s] score=%d"
                  % (i["button"], i["label"][:38], i["kind"],
                     "" if i["target"] is None else " -> title %s" % i["target"],
                     i["score"]))
    print("\n--- extras advertised by the menus, in order ---")
    for n, item in enumerate(menu_names(args.iso, menus=menus), 1):
        print("  %2d. %s" % (n, item["label"]))


def cmd_frames(args):
    os.makedirs(args.out, exist_ok=True)
    for name in list_menu_vobs(args.iso):
        vob = os.path.join(args.out, name)
        if not extract(args.iso, name, vob):
            continue
        for menu in find_menus(vob):
            frames = render_frames(vob, menu["sector"], args.out, count=1)
            for f in frames:
                dest = os.path.join(args.out, "%s_s%d.png" % (name, menu["sector"]))
                os.replace(f, dest)
                print(dest)
        os.remove(vob)


def main():
    p = argparse.ArgumentParser(description="Read DVD menus for extra names.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="list menus and read their button labels")
    s.add_argument("iso")
    s.add_argument("--frames", type=int, default=3)
    s.set_defaults(func=cmd_scan)
    f = sub.add_parser("frames", help="dump menu frames as PNGs to look at")
    f.add_argument("iso")
    f.add_argument("--out", required=True)
    f.set_defaults(func=cmd_frames)
    args = p.parse_args()
    try:
        args.func(args)
    except MenuError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
