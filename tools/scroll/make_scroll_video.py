#!/usr/bin/env python3
"""
make_scroll_video.py — one still image, with the script crawling up beside it.

An alternative to Make Video: instead of sixty images, one image sits on the
left and the whole script scrolls up a column on the right, fading in at the
bottom and out at the top. Much faster to render, and character consistency
stops being a problem because there is only one picture.

The text is drawn with Pillow rather than ffmpeg's subtitle filters, so this
works on an ffmpeg built without libass — which is the common Homebrew build.

    Double-click "Make Scrolling Video.command", or:
    python3 tools/scroll/make_scroll_video.py /path/to/folder
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.audio import MUSIC_HINTS, NARRATION_HINTS, build_audio_graph, find_audio
from shared.errors import Fail
from shared.ffmpeg import duration_of
from shared.fonts import load_font

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}

DEFAULTS = {
    'width': 1920,
    'height': 1080,
    'fps': 30,
    'image_side': 'left',
    # A 16:9 still squeezed into a half-width panel loses ~40% of its width to
    # cropping. 'whole' keeps the entire composition and pads around it.
    'image_fit': 'whole',      # whole | fill
    'text_width': 0.42,        # fraction of the frame the text column occupies
    'font': '',
    'size': 46,
    'line_spacing': 1.55,
    'colour': 'EDEDED',
    'panel': '111114',         # the column behind the text
    'panel_opacity': 0.86,
    'fade': 220,               # pixels of fade at the top and bottom
    'zoom': 0.10,              # slow drift on the still
    'music_volume': 0.18,
    'quality': 18,
    'length': 0,               # seconds; 0 = follow the narration
}


# --------------------------------------------------------------- ingredients

def find_image(folder, named):
    if named:
        p = os.path.join(folder, named)
        if os.path.exists(p):
            return p
        raise Fail(f"Can't find the image '{named}'.")
    for root in (folder, os.path.join(folder, 'images')):
        if not os.path.isdir(root):
            continue
        files = [f for f in sorted(os.listdir(root))
                 if os.path.splitext(f)[1].lower() in IMAGE_EXTS
                 and not f.startswith('.') and 'sticker' not in f.lower()]
        if files:
            return os.path.join(root, files[0])
    raise Fail(
        "No image found.\n"
        f"Put one .png or .jpg in:\n  {folder}\n"
        "or name it in scroll.txt with 'image: my_picture.png'."
    )


def read_script(folder):
    for name in ('script.txt', 'narration.txt', 'story.txt'):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            return open(p, encoding='utf-8-sig').read(), name
    raise Fail(
        "No script.txt in this folder.\n"
        "Put the narration text in a file called script.txt — that's what scrolls."
    )


def parse_settings(folder, s):
    path = os.path.join(folder, 'scroll.txt')
    problems, image_name = [], None
    if not os.path.exists(path):
        return problems, image_name
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.split('#')[0].strip()
        if not line or ':' not in line:
            continue
        key, value = line.split(':', 1)
        key, value = key.strip().lower().replace(' ', '_'), value.strip()
        if key == 'image':
            image_name = value
        elif key in ('font', 'colour', 'color', 'panel'):
            s['colour' if key == 'color' else key] = value.lstrip('#')
        elif key in ('image_fit', 'fit'):
            s['image_fit'] = value.lower()
        elif key in ('image_side', 'side'):
            if value.lower() in ('left', 'right'):
                s['image_side'] = value.lower()
            else:
                problems.append(f"line {lineno}: side should be left or right")
        elif key in ('size', 'resolution'):
            import re
            m = re.match(r'(\d+)\s*[x×]\s*(\d+)$', value)
            if m:
                s['width'], s['height'] = int(m.group(1)), int(m.group(2))
            else:
                try:
                    s['size'] = int(value)
                except ValueError:
                    problems.append(f"line {lineno}: '{value}' is not a number")
        elif key in s:
            try:
                s[key] = float(value) if '.' in value else int(value)
            except ValueError:
                problems.append(f"line {lineno}: '{value}' is not a number")
        else:
            problems.append(f"line {lineno}: don't know the setting '{key}'")
    return problems, image_name


# ---------------------------------------------------------------- rendering

def render_strip(text, font, column_w, s):
    """The whole script drawn onto one tall transparent image."""
    from PIL import Image, ImageDraw

    probe = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    line_h = int(s['size'] * s['line_spacing'])
    para_gap = int(line_h * 0.55)

    lines = []
    for para in [p.strip() for p in text.split('\n')]:
        if not para:
            lines.append(None)              # a gap between paragraphs
            continue
        words, cur = para.split(), ''
        for w in words:
            trial = f"{cur} {w}".strip()
            if cur and probe.textlength(trial, font=font) > column_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)

    while lines and lines[0] is None:
        lines.pop(0)

    height = sum(para_gap if l is None else line_h for l in lines) + line_h
    strip = Image.new('RGBA', (int(column_w), max(height, 1)), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    colour = tuple(int(s['colour'][i:i + 2], 16) for i in (0, 2, 4)) + (255,)

    y = 0
    for line in lines:
        if line is None:
            y += para_gap
            continue
        d.text((0, y), line, font=font, fill=colour)
        y += line_h
    return strip, len([l for l in lines if l])


def render_fade_mask(path, w, h, s):
    """
    A panel-coloured gradient, opaque at the very top and bottom.

    Laid over the finished frame, it makes lines dissolve as they enter and
    leave. Doing the fade this way is a single overlay of a still image —
    per-pixel alpha maths on every frame of a 30-minute video is not worth it.
    """
    from PIL import Image
    fade = max(1, int(s['fade']))
    panel = tuple(int(s['panel'][i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new('RGBA', (w, h), panel + (0,))
    px = img.load()
    for y in range(h):
        if y < fade:
            a = int(255 * (1 - y / fade))
        elif y > h - fade:
            a = int(255 * (1 - (h - y) / fade))
        else:
            continue
        for x in range(w):
            px[x, y] = panel + (a,)
    img.save(path)


# --------------------------------------------------------------------- main

def main(folder):
    s = dict(DEFAULTS)
    problems, image_name = parse_settings(folder, s)

    text, script_name = read_script(folder)
    image = find_image(folder, image_name)
    narration = find_audio(folder, NARRATION_HINTS)
    music = find_audio(folder, MUSIC_HINTS)

    duration = float(s['length']) or (duration_of(narration) if narration else 0)
    if duration <= 0:
        raise Fail(
            "I don't know how long the video should be.\n"
            "Add a narration file, or put 'length: 600' (seconds) in scroll.txt."
        )

    W, H = int(s['width']), int(s['height'])
    col_w = int(W * float(s['text_width']))
    pad = int(col_w * 0.10)
    text_w = col_w - pad * 2

    font, family, have_raqm, font_warning = load_font(text, s['font'], int(s['size']))
    strip, line_count = render_strip(text, font, text_w, s)

    # Fit the crawl to the video: the text starts just below the frame and
    # finishes just above it, exactly as the narration ends.
    travel = H + strip.height
    speed = travel / duration

    print(f"  Image      : {os.path.basename(image)}")
    print(f"  Script     : {script_name}  ({line_count} lines)")
    print(f"  Narration  : {os.path.basename(narration) if narration else 'none'}")
    print(f"  Music      : {os.path.basename(music) if music else 'none'}")
    print(f"  Font       : {family}" + ("" if have_raqm else "   (no complex shaping — Hindi may look wrong)"))
    fit = 'whole image shown' if str(s['image_fit']).lower().startswith('whole') else 'image fills its half (cropped)'
    print(f"  Layout     : image {s['image_side']}, {fit}, text column {col_w}px")
    print(f"  Length     : {int(duration // 60)}:{int(duration % 60):02d}")
    print(f"  Crawl      : {speed:.0f} px/sec, fitted to the narration")
    for p in problems:
        print(f"  scroll.txt : {p}")
    if font_warning:
        print(f"  WARNING    : {font_warning}")
    print()

    work = tempfile.mkdtemp(prefix='scroll-')
    strip_path = os.path.join(work, 'text.png')
    strip.save(strip_path)
    mask_path = os.path.join(work, 'fade.png')
    render_fade_mask(mask_path, col_w, H, s)

    img_w = W - col_w
    col_x = img_w if s['image_side'] == 'left' else 0
    img_x = 0 if s['image_side'] == 'left' else col_w

    panel = s['panel']
    panel_rgb = f"0x{panel}"
    zoom, fps = float(s['zoom']), int(s['fps'])
    frames = max(1, int(duration * fps))

    big_w, big_h = img_w * 2, H * 2
    if str(s['image_fit']).lower().startswith('whole'):
        fit_chain = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                     f"pad={big_w}:{big_h}:(ow-iw)/2:(oh-ih)/2:color={panel_rgb}")
    else:
        fit_chain = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                     f"crop={big_w}:{big_h}")

    graph = ';\n'.join([
        # background panel
        f"color=c={panel_rgb}:s={W}x{H}:r={fps}:d={duration:.3f}[bg]",
        # the still, slowly drifting so it isn't frozen.
        # 'whole' scales down and pads; 'fill' scales up and crops. pad cannot
        # shrink a frame, so the two need different chains, not one flag.
        f"[0:v]{fit_chain},"
        f"zoompan=z='min(1+{zoom}*on/{frames},1+{zoom})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={img_w}x{H}:fps={fps},"
        f"setsar=1[pic]",
        f"[bg][pic]overlay=x={img_x}:y=0[withpic]",
        # the text, moving up: starts below the frame, ends above it
        f"[1:v]format=rgba[txt]",
        f"[withpic][txt]overlay=x={col_x + pad}:y='{H}-{speed:.4f}*t':format=auto[scrolled]",
        # panel-coloured gradient over the column, so lines dissolve at the edges
        f"[2:v]format=rgba[mask]",
        f"[scrolled][mask]overlay=x={col_x}:y=0:format=auto[vout]",
    ])

    out_path = os.path.join(folder, 'video.mp4')
    silent = os.path.join(work, 'picture.mp4')
    audio = os.path.join(work, 'sound.m4a')

    def run(cmd, graph_text, what):
        gf = os.path.join(work, f'{what}.txt')
        open(gf, 'w', encoding='utf-8').write(graph_text)
        full = [c if c != 'GRAPH' else gf for c in cmd]
        if os.environ.get('SHOW_FFMPEG'):
            print('  ' + ' '.join(full) + '\n' + graph_text + '\n')
        if subprocess.run(full).returncode != 0:
            raise Fail(f"ffmpeg couldn't build the {what}. The message above says why.")

    print("  Rendering the picture…\n")
    run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats',
         '-loop', '1', '-t', f'{duration:.3f}', '-i', image,
         '-loop', '1', '-t', f'{duration:.3f}', '-i', strip_path,
         '-loop', '1', '-t', f'{duration:.3f}', '-i', mask_path,
         '-filter_complex_script', 'GRAPH',
         '-map', '[vout]', '-an',
         '-c:v', 'libx264', '-preset', 'medium', '-crf', str(int(s['quality'])),
         '-pix_fmt', 'yuv420p', '-r', str(fps), '-t', f'{duration:.3f}', silent],
        graph, 'picture')

    if narration or music:
        print("\n  Mixing the sound…\n")
        acmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats']
        if narration:
            acmd += ['-i', narration]
        if music:
            mlen = duration_of(music)
            repeats = 0 if mlen <= 0 else max(0, math.ceil(duration / mlen))
            acmd += ['-stream_loop', str(repeats), '-i', music]
        acmd += ['-filter_complex_script', 'GRAPH', '-map', '[aout]',
                 '-c:a', 'aac', '-b:a', '192k', audio]
        run(acmd, build_audio_graph(s, narration, music, duration), 'sound')

    mux = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', silent]
    if narration or music:
        mux += ['-i', audio, '-map', '0:v', '-map', '1:a']
    mux += ['-c', 'copy', '-movflags', '+faststart', out_path]
    if subprocess.run(mux).returncode != 0:
        raise Fail("Couldn't combine the picture and sound.")

    shutil.rmtree(work, ignore_errors=True)
    size = os.path.getsize(out_path) / 1_000_000
    print(f"\n  Done: {out_path}")
    print(f"  {int(duration // 60)}:{int(duration % 60):02d} · {size:.0f} MB")
    print("\n  Add a sticker or compress it with the other tools if you want.")


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