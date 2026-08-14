#!/usr/bin/env python3
"""
add_sticker.py — slide a like/subscribe sticker into an existing video.

Reads video.mp4, writes video_with_sticker.mp4. The original is left alone.

Uses your own sticker.png / sticker.gif if there is one, otherwise draws a
plain badge. Times come from sticker.txt.
"""

import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import ffmpeg
from shared.errors import Fail

STICKER_NAMES = ('sticker', 'subscribe', 'like')
STICKER_EXTS = ('.png', '.gif', '.webm', '.mov', '.apng')

DEFAULTS = {
    'duration': 6.0,      # seconds on screen
    'slide': 0.6,         # seconds to slide in and back out
    'corner': 'bottom-left',
    'margin': 60,         # pixels from the edge
    'width': 420,         # sticker width; height follows the aspect ratio
    'opacity': 1.0,
    'remove_background': '',        # e.g. white — for artwork with no alpha
    'background_tolerance': 0.12,
}

CORNERS = ('bottom-left', 'bottom-right', 'top-left', 'top-right')


def find_video(folder):
    # Most-processed first. Taking video.mp4 when a captioned version exists
    # silently throws the captions away — the run still succeeds, and the
    # missing captions are easy not to notice.
    for name in ('video_with_captions.mp4', 'video.mp4'):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            return p
    raise Fail("No video.mp4 in this folder. Build the video first.")


def find_sticker(folder):
    for f in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(f.lower())
        if ext in STICKER_EXTS and any(n in stem for n in STICKER_NAMES):
            return os.path.join(folder, f)
    return None


def parse_time(text):
    """Accepts 90, 1:30 or 01:30:00."""
    parts = text.strip().split(':')
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"'{text}' isn't a time")
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def parse_sticker_file(folder, settings, video_len):
    """
    sticker.txt:

        at: 0:45, 6:30, 12:00
        duration: 6
        corner: bottom-left

    Or `every: 5:00` to place them on a repeating interval, or `at: always` to
    leave the sticker up for the whole video.
    """
    path = os.path.join(folder, 'sticker.txt')
    times, problems = [], []
    whole_video, duration_line = False, None
    if not os.path.exists(path):
        # Nothing specified: one appearance a little way in, which is the
        # convention for this kind of video.
        return [min(45.0, max(5.0, video_len * 0.08))], problems

    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.split('#')[0].strip()
        if not line or ':' not in line:
            if line:
                problems.append(f"line {lineno}: expected 'name: value'")
            continue
        key, value = line.split(':', 1)
        key, value = key.strip().lower().replace(' ', '_'), value.strip()

        if key in ('always', 'whole_video') or (key == 'at' and
                                                value.lower() in ('always', 'whole', 'whole video', 'all')):
            # On screen for the whole thing: one appearance covering the video,
            # so it still slides in at the start and out at the end. The length
            # is settled after the whole file is read — setting it here let a
            # later 'duration:' line silently undo it.
            if key == 'at' or value.lower() in ('yes', 'true', '1', 'on', 'whole', 'always'):
                whole_video = True
            continue
        if key == 'at':
            for chunk in value.split(','):
                if not chunk.strip():
                    continue
                try:
                    times.append(parse_time(chunk))
                except ValueError as exc:
                    problems.append(f"line {lineno}: {exc}")
        elif key == 'every':
            try:
                step = parse_time(value)
                if step <= 0:
                    raise ValueError('interval must be more than zero')
                t = step
                while t < video_len - 2:
                    times.append(t)
                    t += step
            except ValueError as exc:
                problems.append(f"line {lineno}: {exc}")
        elif key == 'corner':
            v = value.lower().replace('_', '-')
            if v in CORNERS:
                settings['corner'] = v
            else:
                problems.append(f"line {lineno}: corner should be one of {', '.join(CORNERS)}")
        elif key == 'remove_background':
            settings['remove_background'] = value
        elif key in DEFAULTS:
            try:
                settings[key] = float(value)
                if key == 'duration':
                    duration_line = lineno
            except ValueError:
                problems.append(f"line {lineno}: '{value}' is not a number")
        else:
            problems.append(f"line {lineno}: don't know the setting '{key}'")

    # Applied last so it can't be undone by the order lines happen to be in.
    if whole_video:
        if duration_line:
            problems.append(f"line {duration_line}: ignoring 'duration' — "
                            "'at: always' keeps the sticker up for the whole video")
        settings['duration'] = max(1.0, video_len)
        times = [0.0]

    return sorted(set(times)), problems


def transparency_of(path):
    """
    (has an alpha channel, fraction of pixels that are see-through).

    Nothing here removes backgrounds — the sticker is composited using its own
    alpha. A picture flattened onto white looks fine in Preview and lands on the
    video as a white rectangle, so it's worth saying so before rendering.
    """
    try:
        from PIL import Image
        im = Image.open(path)
        if getattr(im, 'is_animated', False):
            im.seek(0)
        if im.mode not in ('RGBA', 'LA', 'PA') and 'transparency' not in im.info:
            return False, 0.0
        # histogram() counts all 256 alpha levels in one C pass, so there's no
        # need to shrink the image first or walk the pixels in Python. It also
        # dodges getdata(), which Pillow 14 removes.
        hist = im.convert('RGBA').getchannel('A').histogram()
        return True, sum(hist[:16]) / (sum(hist) or 1)
    except Exception:
        # Video stickers (.webm/.mov) — ask ffprobe about the pixel format.
        fmt = ffmpeg.stream_field(path, 'v:0', 'pix_fmt')
        return ('a' in fmt or 'pal8' in fmt), 0.0


def make_badge(path, width):
    """
    A plain red badge, drawn only when you haven't supplied artwork.

    Deliberately unbranded — it's a placeholder so the script is useful
    immediately, not an attempt to design your channel's sticker.
    """
    from PIL import Image, ImageDraw, ImageFont

    h = int(width * 0.32)
    img = Image.new('RGBA', (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = h // 4
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius, fill=(204, 0, 0, 235))

    font = None
    for candidate in (
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    ):
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, int(h * 0.34))
                break
            except OSError:
                continue
    if font is None:
        font = ImageFont.load_default()

    text = 'LIKE  ·  SUBSCRIBE'
    box = d.textbbox((0, 0), text, font=font)
    d.text(((width - (box[2] - box[0])) / 2, (h - (box[3] - box[1])) / 2 - box[1]),
           text, font=font, fill=(255, 255, 255, 255))
    img.save(path)
    return path


def overlay_expressions(settings, video_w, video_h, sticker_w, sticker_h, times):
    """
    Slide in from the nearest edge, sit still, slide back out.

    x/y are single expressions covering every appearance, because one overlay
    filter with enable= is far cheaper than chaining one per appearance.
    """
    m = int(settings['margin'])
    corner = settings['corner']
    slide = max(0.05, float(settings['slide']))
    dur = float(settings['duration'])

    rest_x = m if corner.endswith('left') else video_w - sticker_w - m
    rest_y = m if corner.startswith('top') else video_h - sticker_h - m
    off_x = -sticker_w - 10 if corner.endswith('left') else video_w + 10

    # For each window: 0→1 while sliding in, 1 while resting, 1→0 sliding out.
    progress_terms = []
    enables = []
    for start in times:
        end = start + dur
        p = (f"min(1,max(0,(t-{start:.3f})/{slide}))"
             f"*min(1,max(0,({end:.3f}-t)/{slide}))")
        progress_terms.append(f"if(between(t,{start:.3f},{end:.3f}),{p},0)")
        enables.append(f"between(t,{start:.3f},{end:.3f})")

    progress = progress_terms[0]
    for term in progress_terms[1:]:
        progress = f"max({progress},{term})"

    x = f"{off_x}+({rest_x}-({off_x}))*({progress})"
    return x, str(rest_y), '+'.join(enables)


def main(folder):
    settings = dict(DEFAULTS)
    video = find_video(folder)
    video_len = ffmpeg.duration_of(video)
    if video_len <= 0:
        raise Fail("Couldn't read the video's length — is video.mp4 complete?")

    vw = int(ffmpeg.stream_field(video, 'v:0', 'width'))
    vh = int(ffmpeg.stream_field(video, 'v:0', 'height'))

    times, problems = parse_sticker_file(folder, settings, video_len)
    times = [t for t in times if 0 <= t < video_len - 0.5]
    if not times:
        raise Fail(
            "No valid times for the sticker.\n"
            "Put something like this in sticker.txt:\n"
            "    at: 0:45, 6:30\n"
            "    duration: 6"
        )

    supplied = find_sticker(folder)
    work = tempfile.mkdtemp(prefix='sticker-')
    art = supplied or make_badge(os.path.join(work, 'badge.png'), int(settings['width']))
    animated = supplied is not None and supplied.lower().endswith(('.gif', '.webm', '.mov', '.apng'))

    sw = int(settings['width'])
    src_w = int(ffmpeg.stream_field(art, 'v:0', 'width') or sw)
    src_h = int(ffmpeg.stream_field(art, 'v:0', 'height') or int(sw * 0.32))
    sh = max(1, round(sw * src_h / src_w))

    x, y, enable = overlay_expressions(settings, vw, vh, sw, sh, times)

    has_alpha, clear_fraction = transparency_of(art)

    print(f"  Video      : {os.path.basename(video)}  ({vw}x{vh}, {video_len:.0f}s)")
    print(f"  Sticker    : {os.path.basename(art)}"
          + ("  (yours)" if supplied else "  (generated placeholder)"))
    keying_on = str(settings.get('remove_background', '')).strip().lower() not in \
        ('', '0', 'no', 'none', 'off')

    if has_alpha:
        print(f"  Background : transparent ({clear_fraction * 100:.0f}% of it shows the video through)")
    elif keying_on:
        print(f"  Background : solid, keying out '{settings['remove_background']}'")
        print(f"               (this also removes that colour from inside the artwork)")
    else:
        print(f"  Background : SOLID — this image has no transparency, so it will")
        print(f"               appear as a rectangle over the video.")
        print(f"               Re-export it as a PNG with a transparent background, or")
        print(f"               add 'remove background: white' to sticker.txt.")
    print(f"  Appears    : {len(times)}× at " + ', '.join(f"{int(t)//60}:{int(t)%60:02d}" for t in times[:6])
          + (' …' if len(times) > 6 else ''))
    print(f"  Position   : {settings['corner']}, {int(settings['duration'])}s each")
    for p in problems:
        print(f"  sticker.txt: {p}")
    print()

    out = os.path.join(folder, 'video_with_sticker.mp4')
    alpha = float(settings['opacity'])
    fade = '' if alpha >= 0.999 else f",colorchannelmixer=aa={alpha}"

    # Optional: knock out a flat background colour. Off by default — it also
    # eats any matching colour inside the artwork, so white lettering on a white
    # background would disappear along with it.
    key = str(settings.get('remove_background', '')).strip().lower()
    keying = ''
    if key and key not in ('0', 'no', 'none', 'off'):
        colour = {'white': '0xFFFFFF', 'black': '0x000000',
                  'green': '0x00FF00', 'magenta': '0xFF00FF'}.get(key, key)
        if not colour.startswith('0x'):
            colour = '0x' + colour.lstrip('#')
        similarity = float(settings.get('background_tolerance', 0.12))
        keying = f",colorkey={colour}:{similarity}:0.05"

    graph = (
        f"[1:v]scale={sw}:{sh}:flags=lanczos,format=rgba{keying}{fade}[stk];"
        f"[0:v][stk]overlay=x='{x}':y='{y}':enable='{enable}':format=auto[vout]"
    )

    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats', '-i', video]
    if animated:
        cmd += ['-stream_loop', '-1', '-i', art]
    else:
        cmd += ['-i', art]
    cmd += [
        '-filter_complex', graph,
        '-map', '[vout]', '-map', '0:a?',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
        '-c:a', 'copy', '-movflags', '+faststart',
        '-t', f'{video_len:.3f}',
        out
    ]

    if os.environ.get('SHOW_FFMPEG'):
        print('  ' + ' '.join(cmd) + '\n')

    print("  Adding the sticker…\n")
    if subprocess.run(cmd).returncode != 0:
        raise Fail("ffmpeg couldn't add the sticker. The message above says why.")

    import shutil
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n  Done: {out}")


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