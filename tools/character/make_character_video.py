#!/usr/bin/env python3
"""
make_character_video.py — a looping stock video background with your channel
character standing over it.

An alternative to Make Video and Make Scrolling Video: instead of a slideshow
of stills or a script crawling up the side, this one puts your character
(a cutout PNG) in front of a stock/backdrop clip that loops or trims to fit
the narration, with an optional audio-reactive waveform line across the frame
and an optional teal callout bar — the look of a daily-message / "today is
for you" style video.

Reads video.mp4-shaped inputs — a stock clip, a character cutout, narration,
music — and writes video.mp4, so Add Captions / Add Sticker / Compress work
on it exactly as they do after the other two builders. The stock clip's own
soundtrack is silent by default (only narration/music are ever used); set
'stock_audio: 1' in character.txt to mix it in quietly under the narration.

    Double-click "Make Character Video.command", or:
    python3 tools/character/make_character_video.py /path/to/folder
"""

import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.audio import MUSIC_HINTS, NARRATION_HINTS, build_audio_graph, find_audio
from shared.errors import Fail
from shared.ffmpeg import duration_of, stream_field
from shared.fonts import load_font

VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}
IMAGE_EXTS = {'.png', '.webp'}   # a cutout needs transparency; jpg can't carry it

STOCK_HINTS = ('stock', 'background', 'backdrop', 'footage', 'loop', 'bg')
CHARACTER_HINTS = ('character', 'host', 'presenter', 'anchor', 'avatar', 'narrator')

DEFAULTS = {
    'width': 1920,
    'height': 1080,
    'fps': 30,
    'quality': 18,           # x264 CRF; lower is better and bigger

    'character_file': '',    # explicit filename/path — otherwise found by name (see below)
    'stock_file': '',        # same, for the backdrop clip

    'character_width': 950,  # pixels; height follows the source aspect ratio
    'side': 'left',          # left | right | center — which side the character stands
    'margin': 60,            # pixels from the side edge
    'crop_bottom': 0,        # pixels of the character hidden below the frame —
                              # raise this to get the "cropped at the waist" look
                              # without shrinking the character

    'waveform': 1,            # 1/0 — draw a line across the frame reacting to the voice
    'waveform_height': 160,
    'waveform_y': 0.42,       # fraction down the frame the line sits on; 0 = top
    'waveform_colour': 'FFFFFF',
    'waveform_opacity': 1.0,  # below 1, the colour blends with what's behind it — a
                               # "white" line over a saturated background will pick up
                               # that colour rather than staying white. 1.0 is the true colour

    'label': '',              # optional callout bar, e.g. 'आज का दिन आपके लिए'
    'label_side': '',         # left | right | center; '' = opposite the character
    'label_size': 44,
    'label_colour': '111111',
    'label_bg': '2FD1C5',
    'label_margin': 60,
    'font': '',                # blank = pick automatically

    'zoom': 0,                 # slow push on the stock footage; 0 = as filmed
    'start': 0,                 # seconds into the stock clip to begin from — skips an intro
    'music_volume': 0.18,      # before ducking
    'stock_audio': 0,           # 1 to mix the stock clip's own sound in, quietly, under the narration
    'stock_audio_volume': 0.15, # before ducking
    'length': 0,                # seconds; 0 = follow narration, then music, then the clip itself
}

SIDES = ('left', 'right', 'center')


def parse_time(text):
    """Accepts 8, 90, 1:30 or 01:30:00 — the same forms sticker.txt uses for 'at'."""
    parts = text.strip().split(':')
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"'{text}' isn't a time")
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def parse_colour(text):
    """
    A plain colour name ('white') or a hex code ('FFFFFF') — either way,
    returns clean 'RRGGBB' hex.

    Every other tool here only takes hex, so nothing had to handle names. This
    one hands a colour straight to ffmpeg's showwaves filter, which is fussy
    about it: give it something that isn't valid 0xRRGGBB hex — 'white', say —
    and it doesn't error, it silently substitutes its own default. Rendering
    the wrong colour with no complaint is worse than refusing outright, so
    this accepts what people actually type and turns it into hex up front.

    Write the hex form without a leading '#' — '#' starts a comment on this
    line the same as everywhere else in this file, so 'colour: #FFFFFF' is
    parsed as 'colour:' with nothing after it, not as a colour with a hash.
    """
    from PIL import ImageColor
    v = text.strip()
    if not v:
        raise ValueError(
            "no colour given — if you wrote '#RRGGBB', the # starts a comment "
            "here and ate the rest of the line; write it without the #, e.g. FFFFFF"
        )
    spec = v if v.startswith('#') else (f'#{v}' if re.fullmatch(r'[0-9A-Fa-f]{6}', v) else v)
    try:
        r, g, b = ImageColor.getrgb(spec)[:3]
    except ValueError:
        raise ValueError(f"'{text}' isn't a colour — use a name like 'white' or a hex code like 2FD1C5")
    return f'{r:02X}{g:02X}{b:02X}'


def extract_audio(work, name, video_path):
    """
    The audio track out of a video file, on its own, in one pass.

    Only used for the stock clip. Looping that clip's *audio* by handing the
    full video straight to render_bed would make '-stream_loop' re-demux the
    whole video — potentially several gigabytes of 4K — two or three times
    over just to reach the sound. Pulling the audio out once first means every
    loop after that is looping a small standalone audio file instead, which is
    fast rather than merely correct. '-stats' here too: this is the step that,
    without it, looked like the whole run had frozen on a large clip.
    """
    out = os.path.join(work, f'{name}.m4a')
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats',
           '-i', video_path, '-vn', '-c:a', 'aac', '-b:a', '192k', out]
    if subprocess.run(cmd).returncode != 0:
        raise Fail(f"Couldn't pull the audio out of the stock clip.")
    return out


def render_bed(work, name, path, volume, video_len):
    """
    One audio source, looped/padded to exactly video_len and scaled to volume —
    used for both the stock clip's own ambient sound and, when the two need
    combining, the user's music.mp3. Two beds prepared this way can just be
    summed afterwards, since both already end at the same length.

    Expects `path` to already be audio-only and reasonably small — see
    extract_audio for why the stock clip is never passed here directly.
    """
    src_len = duration_of(path)
    repeats = 0 if src_len <= 0 else max(0, math.ceil(video_len / src_len))
    out = os.path.join(work, f'{name}.m4a')
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats',
        '-stream_loop', str(repeats), '-i', path, '-vn',
        '-af', (f'aformat=sample_rates=48000:channel_layouts=stereo,'
                f'apad=whole_dur={video_len:.3f},atrim=duration={video_len:.3f},'
                f'asetpts=N/SR/TB,volume={volume}'),
        '-c:a', 'aac', '-b:a', '192k', out,
    ]
    if subprocess.run(cmd).returncode != 0:
        raise Fail(f"Couldn't prepare the {name} audio track.")
    return out


# --------------------------------------------------------------- ingredients

def resolve_named(folder, name, what):
    """
    A file named explicitly in character.txt — 'character: ...' or 'stock: ...'.

    Accepts a bare filename (looked up inside the folder) or a full path
    elsewhere on disk, the same as pointing to any other file on your machine.
    """
    candidate = name if os.path.isabs(name) else os.path.join(folder, name)
    if os.path.exists(candidate):
        return candidate
    raise Fail(
        f"character.txt names {what} '{name}', but there's nothing there.\n"
        f"Looked for:\n  {candidate}"
    )


def find_stock(folder, named=None):
    if named:
        return resolve_named(folder, named, 'the stock clip as')

    def videos_in(root):
        if not os.path.isdir(root):
            return []
        return [os.path.join(root, f) for f in sorted(os.listdir(root))
                if os.path.splitext(f)[1].lower() in VIDEO_EXTS and not f.startswith('.')
                and f not in ('video.mp4', 'video_with_captions.mp4', 'video_with_sticker.mp4')
                and not f.lower().endswith('_youtube.mp4')]

    for root in (os.path.join(folder, 'stock'), folder):
        found = videos_in(root)
        if not found:
            continue
        hinted = [p for p in found if any(h in os.path.basename(p).lower() for h in STOCK_HINTS)]
        if hinted:
            return hinted[0]
        if len(found) == 1:
            return found[0]
        raise Fail(
            "More than one video here and I can't tell which is the backdrop.\n"
            "Name it with 'stock' or 'background' in the filename, put it alone\n"
            "in a 'stock' folder, or add 'stock: yourfile.mp4' to character.txt."
        )
    raise Fail(
        "No stock/backdrop video found.\n"
        f"Put one in:\n  {folder}\n"
        "or in a 'stock' subfolder — name it e.g. stock.mp4 or background.mp4,\n"
        "or point to it with 'stock: yourfile.mp4' in character.txt."
    )


def find_character(folder, named=None):
    if named:
        return resolve_named(folder, named, 'the character as')

    candidates = [f for f in sorted(os.listdir(folder))
                  if os.path.splitext(f)[1].lower() in IMAGE_EXTS and not f.startswith('.')
                  and 'sticker' not in f.lower()]
    hinted = [f for f in candidates if any(h in f.lower() for h in CHARACTER_HINTS)]
    if hinted:
        return os.path.join(folder, hinted[0])
    if len(candidates) == 1:
        return os.path.join(folder, candidates[0])
    if len(candidates) > 1:
        raise Fail(
            "More than one image here and I can't tell which is the character.\n"
            "Name it with 'character' or 'host' in the filename, e.g. character.png,\n"
            "or add 'character: yourfile.png' to character.txt."
        )
    raise Fail(
        "No character cutout found.\n"
        f"Put a transparent .png (or .webp) of your channel character in:\n  {folder}\n"
        "Name it with 'character' in the filename, e.g. character.png,\n"
        "or point to it with 'character: yourfile.png' in character.txt."
    )


# ------------------------------------------------------------------ settings

def parse_settings(folder, s):
    path = os.path.join(folder, 'character.txt')
    problems = []
    if not os.path.exists(path):
        return problems
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.split('#')[0].strip()
        if not line or ':' not in line:
            if line:
                problems.append(f"line {lineno}: expected 'name: value'")
            continue
        key, value = line.split(':', 1)
        key, value = key.strip().lower().replace(' ', '_'), value.strip()

        if key == 'label':
            s['label'] = value
        elif key == 'font':
            s['font'] = value
        elif key in ('character', 'character_file'):
            s['character_file'] = value
        elif key in ('stock', 'stock_file'):
            s['stock_file'] = value
        elif key in ('colour', 'color', 'label_colour', 'label_color'):
            try:
                s['label_colour'] = parse_colour(value)
            except ValueError as exc:
                problems.append(f"line {lineno}: {exc}")
        elif key in ('label_bg', 'label_background'):
            try:
                s['label_bg'] = parse_colour(value)
            except ValueError as exc:
                problems.append(f"line {lineno}: {exc}")
        elif key in ('waveform_colour', 'waveform_color'):
            try:
                s['waveform_colour'] = parse_colour(value)
            except ValueError as exc:
                problems.append(f"line {lineno}: {exc}")
        elif key == 'side':
            if value.lower() in SIDES:
                s['side'] = value.lower()
            else:
                problems.append(f"line {lineno}: side should be one of {', '.join(SIDES)}")
        elif key == 'label_side':
            if value.lower() in SIDES or value.lower() == '':
                s['label_side'] = value.lower()
            else:
                problems.append(f"line {lineno}: label side should be one of {', '.join(SIDES)}")
        elif key in ('size', 'resolution'):
            m = re.match(r'(\d+)\s*[x×]\s*(\d+)$', value)
            if m:
                s['width'], s['height'] = int(m.group(1)), int(m.group(2))
            else:
                problems.append(f"line {lineno}: size should look like 1920x1080")
        elif key == 'waveform':
            s['waveform'] = 0 if value.lower() in ('0', 'no', 'off', 'false') else 1
        elif key == 'stock_audio':
            s['stock_audio'] = 0 if value.lower() in ('0', 'no', 'off', 'false') else 1
        elif key == 'start':
            try:
                s['start'] = parse_time(value)
            except ValueError as exc:
                problems.append(f"line {lineno}: {exc}")
        elif key in DEFAULTS:
            try:
                s[key] = float(value) if ('.' in value or key in
                                           ('waveform_y', 'waveform_opacity', 'zoom')) else int(value)
            except ValueError:
                problems.append(f"line {lineno}: '{value}' is not a number")
        else:
            problems.append(f"line {lineno}: don't know the setting '{key}'")
    return problems


# ---------------------------------------------------------------- the label

def wrap_to_width(text, font, probe, max_w):
    words, lines, cur = text.split(), [], ''
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and probe.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or ['']


def render_label(text, s, video_w):
    """A rounded, self-sizing callout bar — wider text makes a wider bar."""
    from PIL import Image, ImageDraw

    size = int(s['label_size'])
    font, family, have_raqm, warning = load_font(text, s['font'], size)

    probe = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    max_w = int(video_w * 0.5)
    lines = wrap_to_width(text, font, probe, max_w)

    line_h = int(size * 1.35)
    pad_x, pad_y = int(size * 0.9), int(size * 0.55)
    text_w = max(int(probe.textlength(l, font=font)) for l in lines)
    box_w = text_w + pad_x * 2
    box_h = line_h * len(lines) + pad_y * 2

    img = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = tuple(int(s['label_bg'][i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    fg = tuple(int(s['label_colour'][i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=int(box_h * 0.22), fill=bg)

    y = pad_y
    for line in lines:
        lw = probe.textlength(line, font=font)
        d.text(((box_w - lw) / 2, y), line, font=font, fill=fg)
        y += line_h

    return img, family, warning


# --------------------------------------------------------------------- main

def human(seconds):
    m, sec = divmod(int(round(seconds)), 60)
    return f"{m}:{sec:02d}"


def main(folder):
    s = dict(DEFAULTS)
    problems = parse_settings(folder, s)

    stock = find_stock(folder, s['stock_file'] or None)
    character = find_character(folder, s['character_file'] or None)
    narration = find_audio(folder, NARRATION_HINTS)
    music = find_audio(folder, MUSIC_HINTS)

    stock_has_audio = bool(stream_field(stock, 'a:0', 'codec_type').strip())
    do_stock_audio = bool(int(s['stock_audio']))
    if do_stock_audio and not stock_has_audio:
        problems.append("stock_audio is on but the stock clip has no audio track — skipped")
    do_stock_audio = do_stock_audio and stock_has_audio

    stock_len = duration_of(stock)
    start = float(s['start'])
    if stock_len > 0 and start >= stock_len:
        raise Fail(
            f"'start' ({human(start)}) is at or past the end of the stock clip "
            f"({human(stock_len)}).\nPick an earlier time, or 0 to use it from the beginning."
        )
    playable = (stock_len - start) if stock_len > 0 else 0

    video_len = (float(s['length']) or (duration_of(narration) if narration else 0)
                 or (duration_of(music) if music else 0) or playable)
    if video_len <= 0:
        raise Fail(
            "I don't know how long the video should be.\n"
            "Add a narration file, or put 'length: 600' (seconds) in character.txt."
        )
    length_from = ('length: setting' if s['length'] else
                   'narration' if narration else 'music' if music else 'the stock clip itself')

    # How many times the stock clip needs to repeat (from `start`) to cover
    # video_len — used both for the picture and, if wanted, its own audio.
    repeats = 0 if playable <= 0 else max(0, math.ceil(video_len / playable))

    W, H, fps = int(s['width']), int(s['height']), int(s['fps'])

    # ---- character sizing --------------------------------------------------
    cw = int(s['character_width'])
    src_w = int(stream_field(character, 'v:0', 'width') or cw)
    src_h = int(stream_field(character, 'v:0', 'height') or int(cw * 1.6))
    ch = max(1, round(cw * src_h / src_w))

    margin = int(s['margin'])
    side = s['side']
    x_char = margin if side == 'left' else (W - cw - margin if side == 'right' else (W - cw) // 2)
    y_char = H - ch + int(s['crop_bottom'])

    # ---- waveform ------------------------------------------------------------
    wave_source = narration or music
    do_waveform = bool(int(s['waveform'])) and wave_source is not None
    if bool(int(s['waveform'])) and wave_source is None:
        problems.append("waveform is on but there's no narration or music to draw it from — skipped")

    # ---- label -----------------------------------------------------------
    label_text = s['label'].strip()
    label_img = None
    label_family = None
    label_warning = None
    if label_text:
        label_img, label_family, label_warning = render_label(label_text, s, W)

    print(f"  Stock      : {os.path.basename(stock)}"
          + (f", starting at {human(start)}" if start > 0 else "")
          + (f"  (loops to fill {human(video_len)})" if playable < video_len - 0.5 else ""))
    print(f"  Character  : {os.path.basename(character)}  ({side}, {cw}x{ch})")
    print(f"  Narration  : {os.path.basename(narration) if narration else 'none'}")
    print(f"  Music      : {os.path.basename(music) if music else 'none'}")
    if stock_has_audio:
        print(f"  Stock audio: {'mixed in under the narration' if do_stock_audio else 'off — has sound, but stock_audio is 0'}")
    print(f"  Length     : {human(video_len)}  (from {length_from})")
    print(f"  Waveform   : {'on, from ' + os.path.basename(wave_source) if do_waveform else 'off'}")
    if label_text:
        print(f"  Label      : \"{label_text}\"  ({label_family})")
    for p in problems:
        print(f"  character.txt: {p}")
    if label_warning:
        print(f"  WARNING    : {label_warning}")
    print()

    if not narration and not music and not do_stock_audio:
        print("  No narration or music found — building a silent video.")
        print("  (Name them narration.mp3 and music.mp3 to include them.)\n")

    out_path = os.path.join(folder, 'video.mp4')
    work = tempfile.mkdtemp(prefix='character-')
    silent_path = os.path.join(work, 'picture.mp4')
    audio_path = os.path.join(work, 'sound.m4a')

    # -ss before -i only skips the intro on the *first* pass through the file —
    # -stream_loop reopens the input from its true beginning on every loop
    # after that, so a naive '-ss start -stream_loop N' brings the disliked
    # intro back on every repeat past the first. Cutting a copy that starts at
    # `start` first, then looping *that*, means every loop is the trimmed clip
    # — verified by sampling frames of a test render; without this the intro
    # reappeared partway through.
    # Audio is kept in this copy (not '-an') even though the picture pass below
    # never reads it — extracting the stock clip's own sound, if 'stock_audio'
    # asks for it, reuses this same correctly-trimmed file rather than
    # re-deriving the same -ss/-stream_loop logic a second time.
    stock_input = stock
    if start > 0:
        stock_input = os.path.join(work, 'stock_trimmed' + os.path.splitext(stock)[1])
        trim_cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                    '-ss', f'{start:.3f}', '-i', stock, '-c', 'copy', stock_input]
        if subprocess.run(trim_cmd).returncode != 0:
            raise Fail(
                "Couldn't cut the stock clip at the 'start' you gave.\n"
                "A very unusual codec can refuse a copy-trim — try 'start: 0'\n"
                "or trim the clip yourself before naming it in character.txt."
            )

    def run(cmd, graph, what):
        graph_file = os.path.join(work, f'{what}.txt')
        with open(graph_file, 'w', encoding='utf-8') as fh:
            fh.write(graph)
        full = [c if c != 'GRAPH' else graph_file for c in cmd]
        if os.environ.get('SHOW_FFMPEG'):
            print('  ' + ' '.join(full) + '\n' + graph + '\n')
        if subprocess.run(full).returncode != 0:
            raise Fail(
                f"ffmpeg couldn't build the {what}.\n"
                "The message above says why — usually a damaged clip or image, or\n"
                "an audio file in a format ffmpeg can't read."
            )

    # ---- pass 1: picture ---------------------------------------------------
    print("  Rendering the picture…\n")

    vcmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats']

    vcmd += ['-stream_loop', str(repeats), '-i', stock_input]     # 0: stock
    vcmd += ['-loop', '1', '-t', f'{video_len:.3f}', '-i', character]  # 1: character
    idx = 2
    wave_idx = None
    if do_waveform:
        vcmd += ['-i', wave_source]                                # 2: waveform source
        wave_idx = idx
        idx += 1
    label_idx = None
    if label_img is not None:
        label_path = os.path.join(work, 'label.png')
        label_img.save(label_path)
        vcmd += ['-loop', '1', '-t', f'{video_len:.3f}', '-i', label_path]  # 3: label
        label_idx = idx
        idx += 1

    lines = []
    zoom = float(s['zoom'])
    if zoom > 0:
        frames_total = max(1, int(video_len * fps))
        big_w, big_h = W * 2, H * 2
        lines.append(
            f"[0:v]scale={big_w}:{big_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={big_w}:{big_h},"
            f"zoompan=z='min(1+{zoom}*on/{frames_total},1+{zoom})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},"
            f"setsar=1,trim=duration={video_len:.3f},setpts=PTS-STARTPTS[bg]"
        )
    else:
        lines.append(
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H},setsar=1,trim=duration={video_len:.3f},setpts=PTS-STARTPTS[bg]"
        )

    lines.append(f"[1:v]scale={cw}:{ch}:flags=lanczos,format=rgba[char]")
    lines.append(f"[bg][char]overlay=x={x_char}:y={y_char}:format=auto[with_char]")
    last = 'with_char'

    if do_waveform:
        wave_h = int(s['waveform_height'])
        wave_y = int(float(s['waveform_y']) * H - wave_h / 2)
        colour = f"0x{s['waveform_colour'].lstrip('#')}"
        opacity = float(s['waveform_opacity'])
        fade = '' if opacity >= 0.999 else f",colorchannelmixer=aa={opacity}"
        lines.append(
            f"[{wave_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"apad=whole_dur={video_len:.3f},atrim=duration={video_len:.3f},asetpts=N/SR/TB,"
            f"showwaves=s={W}x{wave_h}:mode=line:rate={fps}:colors={colour}[wraw]"
        )
        lines.append(f"[wraw]format=rgba,colorkey=0x000000:0.12:0.08{fade}[wave]")
        lines.append(f"[{last}][wave]overlay=x=0:y={wave_y}:format=auto[with_wave]")
        last = 'with_wave'

    if label_idx is not None:
        lw, lh = label_img.width, label_img.height
        lmargin = int(s['label_margin'])
        label_side = s['label_side'] or ('right' if side != 'right' else 'left')
        x_label = (lmargin if label_side == 'left'
                   else W - lw - lmargin if label_side == 'right'
                   else (W - lw) // 2)
        y_label = H - lh - lmargin
        lines.append(f"[{last}][{label_idx}:v]overlay=x={x_label}:y={y_label}:format=auto[with_label]")
        last = 'with_label'

    lines.append(f"[{last}]format=yuv420p[vout]")
    graph = ';\n'.join(lines)

    vcmd += [
        '-filter_complex_script', 'GRAPH',
        '-map', '[vout]', '-an',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', str(int(s['quality'])),
        '-pix_fmt', 'yuv420p', '-r', str(fps), '-t', f'{video_len:.3f}',
        silent_path
    ]
    run(vcmd, graph, 'picture')

    # ---- pass 2: sound -----------------------------------------------------
    # 'effective_music' plays the role 'music' normally does in the mix built
    # below. When stock_audio is on it's replaced with a bed that's already
    # looped, padded to video_len, and volume-scaled — either the stock clip's
    # own sound alone, or that summed with music.mp3 if there is one — so
    # build_audio_graph must be told not to loop or scale it a second time.
    effective_music, music_settings = music, s
    if do_stock_audio:
        print("\n  Pulling the sound out of the stock clip…\n")
        stock_audio_raw = extract_audio(work, 'stock_audio_raw', stock_input)
        print("\n  Looping it to fill the video…\n")
        ambient_bed = render_bed(work, 'stock_ambient', stock_audio_raw, s['stock_audio_volume'], video_len)
        if music:
            music_bed = render_bed(work, 'music_bed', music, s['music_volume'], video_len)
            combined = os.path.join(work, 'combined_bed.m4a')
            mix_cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats',
                '-i', ambient_bed, '-i', music_bed,
                '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[aout]',
                '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', combined,
            ]
            if subprocess.run(mix_cmd).returncode != 0:
                raise Fail("Couldn't mix the stock clip's sound with music.mp3.")
            effective_music = combined
        else:
            effective_music = ambient_bed
        music_settings = dict(s, music_volume=1.0)

    if narration or effective_music:
        print("\n  Mixing the sound…\n")
        acmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats']
        if narration:
            acmd += ['-i', narration]
        if effective_music:
            if do_stock_audio:
                acmd += ['-i', effective_music]  # already looped/padded to video_len
            else:
                music_len = duration_of(effective_music)
                m_repeats = 0 if music_len <= 0 else max(0, math.ceil(video_len / music_len))
                acmd += ['-stream_loop', str(m_repeats), '-i', effective_music]
        acmd += ['-filter_complex_script', 'GRAPH', '-map', '[aout]',
                 '-c:a', 'aac', '-b:a', '192k', audio_path]
        run(acmd, build_audio_graph(music_settings, narration, effective_music, video_len), 'sound')

    # ---- pass 3: mux ---------------------------------------------------------
    mux = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', silent_path]
    if narration or effective_music:
        mux += ['-i', audio_path, '-map', '0:v', '-map', '1:a']
    mux += ['-c', 'copy', '-movflags', '+faststart', out_path]
    if subprocess.run(mux).returncode != 0:
        raise Fail("Couldn't combine the picture and sound.")

    shutil.rmtree(work, ignore_errors=True)

    size_mb = os.path.getsize(out_path) / 1_000_000
    print(f"\n  Done: {out_path}")
    print(f"  {human(duration_of(out_path))} · {size_mb:.0f} MB")
    print("\n  Add captions, a sticker, or compress it with the other tools if you want.")


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
