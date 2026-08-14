"""
Which font on this machine can actually draw this text.

Shared because two tools draw words: the captions tool hands a family name to
libass, and the scrolling tool loads the same file through Pillow.

Font inspection is written against the raw file format on purpose.

The first version used fontTools, which isn't part of Python. On a Mac without
it every check silently returned "covers nothing", so the script announced that
no font could draw Devanagari while sitting on a machine that ships two of them.
A dependency that fails by reporting the opposite of the truth is worse than no
dependency, so this reads the tables directly using only the standard library.
"""

import os
import re
import struct
import subprocess

from .errors import Fail

# macOS ships all of these; the Linux ones are here so the script is testable.
DEVANAGARI_FONTS = (
    'Kohinoor Devanagari', 'Devanagari Sangam MN', 'Noto Sans Devanagari',
    'Mangal', 'Nirmala UI', 'Lohit Devanagari', 'Samyak Devanagari',
)
LATIN_FONTS = ('Helvetica Neue', 'Helvetica', 'Arial', 'DejaVu Sans', 'Liberation Sans')


def has_devanagari(text):
    return any('ऀ' <= ch <= 'ॿ' for ch in text)


def _faces(data):
    """Byte offsets of each face; a .ttc holds several."""
    if data[:4] == b'ttcf':
        count = struct.unpack('>I', data[8:12])[0]
        return list(struct.unpack(f'>{count}I', data[12:12 + 4 * count]))
    return [0]


def _tables(data, face):
    num = struct.unpack('>H', data[face + 4:face + 6])[0]
    out = {}
    for i in range(num):
        rec = face + 12 + 16 * i
        if rec + 16 > len(data):
            break
        tag, _, off, length = struct.unpack('>4sIII', data[rec:rec + 16])
        out[tag] = (off, length)
    return out


def _cmap_codepoints(data, off):
    """Every codepoint in one cmap table, from the formats fonts actually use."""
    codes = set()
    try:
        num = struct.unpack('>H', data[off + 2:off + 4])[0]
        for i in range(num):
            rec = off + 4 + 8 * i
            sub = off + struct.unpack('>I', data[rec + 4:rec + 8])[0]
            fmt = struct.unpack('>H', data[sub:sub + 2])[0]

            if fmt == 4:
                seg2 = struct.unpack('>H', data[sub + 6:sub + 8])[0]
                seg = seg2 // 2
                ends = struct.unpack(f'>{seg}H', data[sub + 14:sub + 14 + seg2])
                starts_at = sub + 16 + seg2
                starts = struct.unpack(f'>{seg}H', data[starts_at:starts_at + seg2])
                for s, e in zip(starts, ends):
                    if s == 0xFFFF:
                        continue
                    codes.update(range(s, min(e, 0xFFFE) + 1))

            elif fmt == 12:
                groups = struct.unpack('>I', data[sub + 12:sub + 16])[0]
                for g in range(min(groups, 20000)):
                    at = sub + 16 + 12 * g
                    s, e, _ = struct.unpack('>III', data[at:at + 12])
                    codes.update(range(s, min(e, s + 5000) + 1))

            elif fmt == 6:
                first, count = struct.unpack('>HH', data[sub + 6:sub + 10])
                codes.update(range(first, first + count))

            elif fmt == 0:
                codes.update(range(0, 256))
    except (struct.error, IndexError):
        pass
    return codes


def _family_name(data, off, length):
    """nameID 1 — the family name libass will match on."""
    try:
        count, str_off = struct.unpack('>HH', data[off + 2:off + 6])
        best, best_rank = None, -1
        for i in range(count):
            rec = off + 6 + 12 * i
            pid, eid, lid, nid, ln, o = struct.unpack('>6H', data[rec:rec + 12])
            if nid != 1:
                continue
            raw = data[off + str_off + o: off + str_off + o + ln]
            try:
                name = raw.decode('utf-16-be') if pid == 3 else raw.decode('mac-roman')
            except (UnicodeDecodeError, LookupError):
                continue
            name = name.strip()
            if not name:
                continue
            # Fonts carry the same family in several languages. Taking the last
            # one gave 'आयटीएफ देवनागरी' instead of 'ITF Devanagari'.
            if pid == 3 and lid == 0x409:
                rank = 4
            elif pid == 1 and lid == 0:
                rank = 3
            elif name.isascii():
                rank = 2
            elif pid == 3:
                rank = 1
            else:
                rank = 0
            if rank > best_rank:
                best, best_rank = name, rank
        return best
    except (struct.error, IndexError):
        return None


def font_faces(path):
    """[(family name, codepoints)] for every face in the file."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError:
        return []
    out = []
    for face in _faces(data):
        tabs = _tables(data, face)
        if b'cmap' not in tabs:
            continue
        codes = _cmap_codepoints(data, tabs[b'cmap'][0])
        name = _family_name(data, *tabs[b'name']) if b'name' in tabs else None
        out.append((name or os.path.splitext(os.path.basename(path))[0], codes))
    return out


def font_coverage(path, needed):
    """
    How many of the characters actually used does this font contain?

    Asking "has any Devanagari" isn't enough — a font can carry a handful of
    glyphs and still drop the conjuncts and matras this script needs, and the
    missing ones render as empty boxes without any error.
    """
    if not needed:
        return 1.0
    best = 0.0
    for _, codes in font_faces(path):
        best = max(best, len(needed & codes) / len(needed))
    return best


def font_files():
    """Every font file we can see, macOS or Linux."""
    files = []
    out = subprocess.run(['fc-list', '--format', '%{file}\n'],
                         capture_output=True, text=True)
    if out.returncode == 0:
        files += [l.strip() for l in out.stdout.splitlines() if l.strip()]
    # macOS has no fc-list by default.
    for d in ('/System/Library/Fonts', '/System/Library/Fonts/Supplemental',
              '/Library/Fonts', os.path.expanduser('~/Library/Fonts')):
        if os.path.isdir(d):
            files += [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.lower().endswith(('.ttf', '.ttc', '.otf', '.otc'))]
    seen, out_files = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out_files.append(f)
    return out_files


def installed_fonts():
    """
    (family name, file) for every face on the machine.

    The name is read from the font's own name table, not the filename. macOS
    stores 'Devanagari Sangam MN' in DevanagariSangamMN.ttc, so matching on the
    filename missed exactly the fonts this needs to find.
    """
    found = []
    for path in font_files():
        for name, _ in font_faces(path):
            # macOS hides its internal fonts behind a leading dot — ".SF Bangla",
            # ".Helvetica Neue DeskInterface". They are not meant to be requested
            # by name and libass may not resolve them, so they must not be picked.
            if name.startswith('.'):
                continue
            found.append((name, path))
    return found


def choose_font(text, requested):
    """
    Returns (font name, warning or None).

    Hindi rendered in a font without Devanagari coverage comes out as a row of
    empty boxes — and it renders "successfully", so nothing fails and you only
    find out by looking. Worth checking properly.
    """
    if requested:
        return requested, None

    fonts = installed_fonts()
    names = {fam for fam, _ in fonts}

    if has_devanagari(text):
        needed = {ord(c) for c in text if 'ऀ' <= c <= 'ॿ'}
        by_name = {}
        for fam, path in fonts:
            by_name.setdefault(fam, path)

        # Preferred families first, but only if they really cover the text.
        # Tracked as two variables rather than a tuple: max() on tuples falls
        # through to comparing the names when coverage ties, and a name against
        # None raises.
        best_cov, best_fam = 0.0, None

        def consider(family, path):
            nonlocal best_cov, best_fam
            cov = font_coverage(path, needed)
            if cov > best_cov:
                best_cov, best_fam = cov, family
            return cov

        for want in DEVANAGARI_FONTS:
            if want in by_name and consider(want, by_name[want]) >= 0.999:
                return want, None

        # A font built for Devanagari beats one that merely happens to include
        # the codepoints — ".SF Bangla" covers them but is a Bengali face, and
        # the shaping of conjuncts is what suffers.
        def devanagari_first(item):
            name = item[0].lower()
            return 0 if re.search(r'devanagari|kohinoor|nirmala|mangal|lohit|hind|mukta', name) else 1

        for fam, path in sorted(by_name.items(), key=devanagari_first):
            if consider(fam, path) >= 0.999:
                return fam, None

        cov, fam = best_cov, best_fam
        if fam and cov > 0.5:
            return (fam,
                    f"'{fam}' is the best font available but it's missing "
                    f"{round((1 - cov) * 100)}% of the\n"
                    "               Hindi characters — those will show as empty boxes.")
        return (LATIN_FONTS[0],
                "No font on this computer can draw Devanagari. The Hindi text will\n"
                "               come out as empty boxes. On macOS install one from Font Book,\n"
                "               or set 'font: Kohinoor Devanagari' in captions.txt once you have it.")

    for want in LATIN_FONTS:
        if want in names:
            return want, None
    return 'sans-serif', None


def load_font(text, requested, size):
    """
    A ready-to-draw Pillow font for this text, with complex-script shaping on.

    Shared because drawing Hindi with Pillow — the scrolling script's caption
    column, and the character tool's label bar — needs the same three things
    every time: pick a family that actually covers the text (choose_font,
    above), find its file (installed_fonts), and load it with RAQM so
    Devanagari conjuncts join instead of sitting apart like broken type.

    Returns (ImageFont, family name, whether RAQM was available, warning or
    None). The warning is choose_font's own — surface it, the way captions.py
    does, rather than rendering a row of empty boxes with no explanation.
    """
    from PIL import ImageFont, features
    have_raqm = features.check('raqm')
    engine = ImageFont.Layout.RAQM if have_raqm else ImageFont.Layout.BASIC

    if requested:
        for name, path in installed_fonts():
            if name.lower() == requested.lower():
                return ImageFont.truetype(path, size, layout_engine=engine), name, have_raqm, None
        raise Fail(f"No font called '{requested}' is installed.")

    family, warning = choose_font(text, '')
    for name, path in installed_fonts():
        if name == family:
            return ImageFont.truetype(path, size, layout_engine=engine), family, have_raqm, warning
    raise Fail(f"Found the font '{family}' by name but couldn't open its file.")
