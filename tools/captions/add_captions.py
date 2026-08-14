#!/usr/bin/env python3
"""
add_captions.py — burn captions into the video and write a .srt beside it.

Two ways to supply the words:

  captions.srt   already timed — used as-is, most accurate
  script.txt     the narration text — split into caption lines and spread
                 across the narration, which is approximate but needs nothing

Reads video.mp4, writes video_with_captions.mp4 and captions.srt.
The original is left alone.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import ffmpeg
from shared.errors import Fail
from shared.fonts import choose_font, has_devanagari

DEFAULTS = {
    'font': '',            # blank = pick automatically
    'size': 54,
    'colour': 'FFFFFF',
    'outline': 3,
    'shadow': 1,
    'bottom_margin': 90,
    'max_chars': 42,       # per line before wrapping
    'max_lines': 2,        # per caption
    'seconds_per_caption': 0,   # 0 = spread evenly across the narration
    'box': 0,              # 1 = solid background behind the text
}


# ----------------------------------------------------------------- captions

def srt_time(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path):
    text = open(path, encoding='utf-8-sig').read()
    blocks = re.split(r'\n\s*\n', text.strip())
    cues = []
    for b in blocks:
        lines = [l for l in b.strip().split('\n') if l.strip()]
        if len(lines) < 2:
            continue
        timing = next((l for l in lines if '-->' in l), None)
        if not timing:
            continue
        try:
            start, end = [t.strip().replace(',', '.') for t in timing.split('-->')]
            def secs(t):
                parts = [float(p) for p in t.split(':')]
                v = 0.0
                for p in parts:
                    v = v * 60 + p
                return v
            body = '\n'.join(lines[lines.index(timing) + 1:])
            cues.append((secs(start), secs(end), body))
        except Exception:
            continue
    if not cues:
        raise Fail(f"Couldn't read any captions out of {os.path.basename(path)}.")
    return cues


def wrap(text, max_chars, max_lines):
    words, lines, cur = text.split(), [], ''
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return '\n'.join(lines[:max_lines])


def captions_from_script(path, total_len, s):
    """
    Split the narration text into caption-sized chunks and spread them across
    the narration. Timing is proportional to how much text each chunk holds,
    which tracks a steady reading pace but is not lip-accurate.
    """
    raw = open(path, encoding='utf-8-sig').read()
    raw = re.sub(r'^\s*#.*$', '', raw, flags=re.M)          # strip comment lines
    # Split on sentence ends, including Devanagari danda.
    pieces = [p.strip() for p in re.split(r'(?<=[।?!.])\s+|\n{2,}', raw) if p.strip()]
    if not pieces:
        raise Fail("script.txt has no text in it.")

    limit = int(s['max_chars']) * int(s['max_lines'])
    chunks = []
    for piece in pieces:
        while len(piece) > limit:
            cut = piece.rfind(' ', 0, limit)
            cut = cut if cut > limit * 0.5 else limit
            chunks.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            chunks.append(piece)

    weights = [max(len(c), 1) for c in chunks]
    total_weight = sum(weights)
    fixed = float(s['seconds_per_caption'])

    cues, t = [], 0.0
    for chunk, weight in zip(chunks, weights):
        dur = fixed if fixed > 0 else total_len * weight / total_weight
        cues.append((t, min(t + dur, total_len), chunk))
        t += dur
        if t >= total_len:
            break
    return cues


def ass_time(seconds):
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{sec:02d}.{cs:02d}"


def write_ass(cues, path, s, font, width, height):
    """
    A real ASS file rather than an .srt plus force_style.

    force_style's sizes and margins are interpreted in ASS's default 384-tall
    canvas, so on a 1080p video the text lands around the middle of the frame at
    the wrong size. Declaring PlayResX/PlayResY as the actual frame size makes
    'size' and 'bottom margin' mean real pixels.
    """
    c = s['colour']
    primary = f"&H00{c[4:6]}{c[2:4]}{c[0:2]}"      # ASS is &HBBGGRR
    border_style = 3 if int(s['box']) else 1

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{int(s['size'])},{primary},&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,{border_style},{int(s['outline'])},{int(s['shadow'])},2,80,80,{int(s['bottom_margin'])},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(header)
        for start, end, text in cues:
            body = wrap(text, int(s['max_chars']), int(s['max_lines'])).replace('\n', r'\N')
            fh.write(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{body}\n")


def write_srt(cues, path, s):
    with open(path, 'w', encoding='utf-8') as fh:
        for i, (start, end, text) in enumerate(cues, 1):
            fh.write(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n"
                     f"{wrap(text, int(s['max_chars']), int(s['max_lines']))}\n\n")


# --------------------------------------------------------------------- run

def parse_settings(folder, s):
    path = os.path.join(folder, 'captions.txt')
    problems = []
    if not os.path.exists(path):
        return problems
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.split('#')[0].strip()
        if not line or ':' not in line:
            continue
        key, value = line.split(':', 1)
        key, value = key.strip().lower().replace(' ', '_'), value.strip()
        if key == 'font':
            s['font'] = value
        elif key in ('colour', 'color'):
            s['colour'] = value.lstrip('#').upper()
        elif key in s:
            try:
                s[key] = float(value) if '.' in value else int(value)
            except ValueError:
                problems.append(f"line {lineno}: '{value}' is not a number")
        else:
            problems.append(f"line {lineno}: don't know the setting '{key}'")
    return problems


SUBTITLE_FILTERS = (
    'subtitles=filename=burn.ass',   # explicit option name — widest support
    'subtitles=burn.ass',            # positional shorthand
    'ass=filename=burn.ass',         # the ass filter, same libass underneath
    'ass=burn.ass',
)


def pick_subtitle_filter(work, video):
    """
    Find a filter string this particular ffmpeg accepts, by trying one frame.

    Builds disagree about whether the filename may be given positionally. Some
    reject 'subtitles=burn.ass' with "No option name near 'burn.ass'" — a parse
    error, so it fails instantly and costs nothing to test. Rendering a single
    frame is far cheaper than finding out an hour into a 30-minute video.

    Returns (filter string, None) or (None, what each attempt actually said).
    Reporting ffmpeg's own words matters: the previous version asserted "built
    without libass", which was a guess dressed up as a diagnosis.
    """
    reasons = []
    for candidate in SUBTITLE_FILTERS:
        # Probe with the real video rather than a generated test pattern.
        # `-f lavfi` needs libavdevice, so a build without it fails every
        # candidate for a reason that has nothing to do with subtitles — and
        # the script would then blame libass.
        probe = subprocess.run(
            [ffmpeg.FFMPEG, '-v', 'error', '-i', video, '-vf', candidate,
             '-frames:v', '1', '-f', 'null', '-'],
            cwd=work, capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate, None
        first = next((l.strip() for l in (probe.stderr or '').splitlines()
                      if l.strip() and not l.startswith('  ')), '(no message)')
        reasons.append((candidate, first))
    return None, reasons


def main(folder):
    _, candidates, capable = ffmpeg.select()

    s = dict(DEFAULTS)
    problems = parse_settings(folder, s)

    video = os.path.join(folder, 'video.mp4')
    if not os.path.exists(video):
        raise Fail("No video.mp4 in this folder. Build the video first.")
    video_len = ffmpeg.duration_of(video)

    srt_in = next((os.path.join(folder, f) for f in sorted(os.listdir(folder))
                   if f.lower().endswith('.srt') and 'caption' in f.lower()), None)
    script = next((os.path.join(folder, f) for f in ('script.txt', 'narration.txt')
                   if os.path.exists(os.path.join(folder, f))), None)

    if srt_in:
        cues = parse_srt(srt_in)
        source = os.path.basename(srt_in)
    elif script:
        cues = captions_from_script(script, video_len, s)
        source = f"{os.path.basename(script)} (timing estimated)"
    else:
        raise Fail(
            "Nothing to make captions from.\n"
            "Put either of these in the folder:\n"
            "  captions.srt  — already timed, most accurate\n"
            "  script.txt    — the narration text, timed automatically"
        )

    all_text = ' '.join(c[2] for c in cues)
    font, warning = choose_font(all_text, s['font'])

    print(f"  Video      : video.mp4  ({video_len:.0f}s)")
    print(f"  Captions   : {len(cues)} from {source}")
    print(f"  Script     : {'Devanagari' if has_devanagari(all_text) else 'Latin'}")
    print(f"  Font       : {font}")
    if len(candidates) > 1 or not capable:
        print(f"  ffmpeg     : {ffmpeg.FFMPEG}"
              + ("" if capable else "   (no subtitle support)"))
    for p in problems:
        print(f"  captions.txt: {p}")
    if warning:
        print(f"  WARNING    : {warning}")
    print()

    out_srt = os.path.join(folder, 'captions.srt')
    write_srt(cues, out_srt, s)

    vw = int(subprocess.run(
        [ffmpeg.FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width',
         '-of', 'default=nw=1:nk=1', video], capture_output=True, text=True).stdout.strip() or 1920)
    vh = int(subprocess.run(
        [ffmpeg.FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=height',
         '-of', 'default=nw=1:nk=1', video], capture_output=True, text=True).stdout.strip() or 1080)

    work = tempfile.mkdtemp(prefix='captions-')
    burn_ass = os.path.join(work, 'burn.ass')
    write_ass(cues, burn_ass, s, font, vw, vh)

    graph, reasons = pick_subtitle_filter(work, os.path.abspath(video))
    if graph is None:
        no_filter = any('no such filter' in r.lower() for _, r in reasons)
        detail = '\n'.join(f"      {c}\n        → {r}" for c, r in reasons)
        shutil.rmtree(work, ignore_errors=True)
        if no_filter:
            # Show every ffmpeg on the machine and whether each one can do it.
            # "Reinstall ffmpeg" is useless advice when the problem is that a
            # different binary is being picked up.
            found = ffmpeg.candidates()
            inventory = '\n'.join(
                f"      {'can' if ffmpeg.has_subtitles_filter(b) else 'CANNOT'}  {b}"
                for b in found) or "      none found"
            raise Fail(
                "This copy of ffmpeg can't draw text onto video.\n\n"
                "    The 'subtitles' and 'ass' filters are missing — it was built\n"
                "    without libass. Your captions are fine; captions.srt is written:\n"
                f"      {out_srt}\n\n"
                "    Every ffmpeg I can find, and whether it can burn in captions:\n"
                f"{inventory}\n\n"
                "    If one says 'can', the problem is only which comes first on your\n"
                "    PATH — this script now prefers a capable one automatically, so\n"
                "    just run it again. If they all say CANNOT, install a full build:\n"
                "      brew install ffmpeg\n"
                "    and if Homebrew says it's already installed:\n"
                "      brew reinstall ffmpeg && brew link --overwrite ffmpeg\n\n"
                "    In the meantime the .srt is the normal way to caption on YouTube:\n"
                "    viewers can toggle it, it's searchable, and it auto-translates."
            )
        raise Fail(
            "Couldn't burn in the captions. Every way of invoking the subtitle\n"
            "    filter was refused. What ffmpeg said each time:\n"
            + detail
            + f"\n\n    captions.srt was still written next to your video:\n"
              f"      {out_srt}\n"
              "    Upload that to YouTube — the video itself is unchanged."
        )

    out_video = os.path.join(folder, 'video_with_captions.mp4')
    cmd = [ffmpeg.FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-stats',
           '-i', os.path.abspath(video), '-vf', graph,
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
           '-c:a', 'copy', '-movflags', '+faststart', os.path.abspath(out_video)]

    if os.environ.get('SHOW_FFMPEG'):
        print('  ' + ' '.join(cmd) + '\n')

    print("  Burning in the captions…\n")
    ok = subprocess.run(cmd, cwd=work).returncode == 0
    shutil.rmtree(work, ignore_errors=True)
    if not ok:
        raise Fail("ffmpeg couldn't burn in the captions. The message above says why.")

    print(f"\n  Done: {out_video}")
    print(f"        {out_srt}  ← upload this to YouTube as well")


if __name__ == '__main__':
    # The folder you're standing in, not the one this script lives in — the
    # launcher cd's to your video folder first, so this is right for
    # double-clicking too.
    target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    try:
        main(target)
    except Fail as exc:
        print(f"\n  {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
        sys.exit(130)