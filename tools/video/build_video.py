#!/usr/bin/env python3
"""
build_video.py — turn a folder of stills into a narrated video.

Each image gets its own duration, a slow Ken Burns move, and a crossfade into
the next. Narration sits on top of a music bed that ducks automatically
underneath the voice.

Normally run by double-clicking "Make Video.command"; the command line is here
for when you want to point it somewhere else:

    python3 tools/video/build_video.py /path/to/folder
"""

import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.audio import MUSIC_HINTS, NARRATION_HINTS, build_audio_graph, find_audio
from shared.errors import Fail
from shared.ffmpeg import duration_of

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}

DEFAULTS = {
    'default': 8.0,        # seconds per image when the timing file doesn't say
    'crossfade': 1.0,      # seconds of dissolve between images
    'fps': 30,
    'width': 1920,
    'height': 1080,
    'music_volume': 0.18,  # before ducking
    'zoom': 0.12,          # how far the Ken Burns move travels (0 = still)
    'quality': 18,         # x264 CRF; lower is better and bigger
}


# --------------------------------------------------------------------- input

def natural_key(name):
    """Sort scene_2 before scene_10, which plain alphabetical order gets wrong."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]


def find_images(folder):
    candidates = []
    for root in (os.path.join(folder, 'images'), folder):
        if not os.path.isdir(root):
            continue
        found = [
            os.path.join(root, f) for f in os.listdir(root)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS and not f.startswith('.')
        ]
        if found:
            candidates = found
            break
    if not candidates:
        raise Fail(
            "No images found.\n"
            f"Put your .png or .jpg files in:\n  {folder}\n"
            "or in an 'images' folder inside it."
        )
    return sorted(candidates, key=lambda p: natural_key(os.path.basename(p)))


def expand_scene_range(token):
    """
    'scene_07-08' -> ['scene_07', 'scene_08']; 'scene_01' -> ['scene_01'].

    The second number's width follows the first, so 'scene_7-8' stays
    single-digit and 'scene_07-08' stays zero-padded — whichever the
    filenames actually use.
    """
    m = re.match(r'^(.*?)(\d+)-(\d+)$', token)
    if not m:
        return [token]
    prefix, start, end = m.groups()
    lo, hi = int(start), int(end)
    if hi < lo or hi - lo > 20:  # not a scene range — leave it alone
        return [token]
    width = len(start)
    return [f"{prefix}{str(n).zfill(width)}" for n in range(lo, hi + 1)]


def parse_timing(folder, images, settings):
    """
    timing.txt looks like:

        default: 8
        crossfade: 1

        scene_01: 12
        scene_02: 9.5

    A name matches any image whose filename contains it, so `scene_01` matches
    `09-namak_scene_01.png` without you writing the whole thing out.

    The scene label can carry a descriptive tag after it, purely for your own
    reading — `scene_01 img_town_dawn: 14` matches on `scene_01` and ignores
    the rest — and it can span a range when two scenes share one image —
    `scene_07-08 img_breakfast: 53` matches anything with `scene_07` or
    `scene_08` in the name and gives each match the full 53s.
    """
    path = os.path.join(folder, 'timing.txt')
    per_image = {}
    if not os.path.exists(path):
        return per_image, []

    problems = []
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.split('#')[0].strip()
        if not line:
            continue
        if ':' not in line:
            problems.append(f"line {lineno}: expected 'name: seconds', got '{line}'")
            continue
        key, value = (p.strip() for p in line.split(':', 1))
        norm = key.lower().replace(' ', '_')

        if norm in ('size', 'resolution'):
            m = re.match(r'(\d+)\s*[x×]\s*(\d+)$', value)
            if m:
                settings['width'], settings['height'] = int(m.group(1)), int(m.group(2))
            else:
                problems.append(f"line {lineno}: size should look like 1920x1080")
            continue

        if norm in DEFAULTS:
            try:
                settings[norm] = int(value) if norm in ('fps', 'quality') else float(value)
            except ValueError:
                problems.append(f"line {lineno}: '{value}' is not a number")
            continue

        try:
            seconds = float(value)
        except ValueError:
            problems.append(f"line {lineno}: '{value}' is not a number of seconds")
            continue

        # The first word is the scene id to match on; anything after it is a
        # human-readable tag, not part of the match, so `key` (which may
        # contain spaces) can't be used as the substring directly.
        parts = key.split()
        scene_token, label = (parts[0], ' '.join(parts[1:])) if parts else (key, '')

        matches, seen = [], set()

        def add_matches(needle):
            needle = needle.lower()
            for p in images:
                if p not in seen and needle in os.path.basename(p).lower():
                    matches.append(p)
                    seen.add(p)

        add_matches(scene_token)                       # 'scene_07-08' verbatim,
        if not matches:                                 # in case that's how it's named
            for sid in expand_scene_range(scene_token):  # else 'scene_07' + 'scene_08'
                add_matches(sid)
        if not matches and label:                        # last resort: the tag itself
            add_matches(label.replace(' ', '_'))

        if not matches:
            problems.append(f"line {lineno}: no image matches '{key}'")
        for p in matches:
            per_image[p] = seconds

    return per_image, problems


# ------------------------------------------------------------- filter graph

def ken_burns(index, dur, s):
    """
    A slow push or pull, alternating direction so 60 images don't feel identical.

    zoompan works per output frame, so the image is upscaled first — zooming a
    1080p still directly produces visible stepping as it interpolates.
    """
    frames = max(1, int(round(dur * s['fps'])))
    w, h, z = s['width'], s['height'], s['zoom']
    big_w, big_h = w * 2, h * 2

    # Four gentle variations, cycled.
    kind = index % 4
    if kind == 0:      # push in, centred
        zexpr = f"min(1+{z}*on/{frames},1+{z})"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif kind == 1:    # pull out, centred
        zexpr = f"max(1+{z}-{z}*on/{frames},1)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif kind == 2:    # push in, drifting right
        zexpr = f"min(1+{z}*on/{frames},1+{z})"
        x, y = f"(iw-iw/zoom)*(on/{frames})", "ih/2-(ih/zoom/2)"
    else:              # push in, drifting down
        zexpr = f"min(1+{z}*on/{frames},1+{z})"
        x, y = "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(on/{frames})"

    return (
        f"[{index}:v]"
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{zexpr}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={s['fps']},"
        f"setsar=1,format=yuv420p[v{index}]"
    )


def build_video_graph(images, durations, s, video_len):
    lines = [ken_burns(i, durations[i], s) for i in range(len(images))]

    fade = s['crossfade']
    last = 'v0'
    elapsed = durations[0]
    for i in range(1, len(images)):
        out = f'x{i}'
        offset = max(0.0, elapsed - fade)
        lines.append(
            f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset:.3f}[{out}]"
        )
        last = out
        elapsed = offset + durations[i]

    lines.append(f"[{last}]trim=duration={video_len:.3f},setpts=PTS-STARTPTS[vout]")
    return ';\n'.join(lines)


# -------------------------------------------------------------------- build

def human(seconds):
    m, sec = divmod(int(round(seconds)), 60)
    return f"{m}:{sec:02d}"


def main(folder):
    settings = dict(DEFAULTS)
    images = find_images(folder)
    per_image, problems = parse_timing(folder, images, settings)

    narration = find_audio(folder, NARRATION_HINTS)
    music = find_audio(folder, MUSIC_HINTS)

    durations = [per_image.get(p, settings['default']) for p in images]
    fade = settings['crossfade']

    # Every image must outlast the dissolve it takes part in, or xfade produces
    # a black flash where the overlap runs past the end of the clip.
    min_dur = fade + 0.5
    too_short = [(os.path.basename(p), d) for p, d in zip(images, durations) if d < min_dur]
    if too_short:
        for i, d in enumerate(durations):
            durations[i] = max(d, min_dur)

    video_len = sum(durations) - fade * (len(images) - 1)

    narr_len = duration_of(narration) if narration else 0.0
    fitted = None

    if narration and narr_len > 0 and abs(narr_len - video_len) > 0.5:
        # Make the video end with the narration, by scaling every image's time
        # in proportion. Relative pacing is preserved — an image you gave twice
        # as long still gets twice as long — and nothing is left silent or cut
        # off mid-sentence.
        #
        # The crossfades don't scale: total = sum(durations) - fade*(n-1), so the
        # overlap has to be added back before working out the scale factor.
        overlap = fade * (len(images) - 1)
        target_sum = narr_len + overlap
        scale = target_sum / sum(durations)

        scaled = [d * scale for d in durations]

        # Scaling down can push an image under the crossfade length, which
        # produces a black flash. Pin those to the floor and rescale the rest to
        # absorb the difference.
        floor = fade + 0.5
        if any(d < floor for d in scaled):
            pinned = [max(d, floor) for d in scaled]
            free = [i for i, d in enumerate(scaled) if d >= floor]
            excess = sum(pinned) - target_sum
            free_total = sum(pinned[i] for i in free)
            if free and free_total - excess > len(free) * floor:
                adjust = (free_total - excess) / free_total
                for i in free:
                    pinned[i] *= adjust
            scaled = pinned

        durations = scaled
        video_len = sum(durations) - overlap
        fitted = (scale, narr_len)

    print(f"  Images     : {len(images)}")
    print(f"  Narration  : {os.path.basename(narration) if narration else 'none'}"
          + (f"  ({human(narr_len)})" if narration else ""))
    print(f"  Music      : {os.path.basename(music) if music else 'none'}")
    print(f"  Resolution : {settings['width']}x{settings['height']} @ {settings['fps']}fps")
    print(f"  Length     : {human(video_len)}")
    if too_short and not fitted:
        names = ', '.join(n for n, _ in too_short[:4])
        print(f"  Note       : {len(too_short)} image(s) were shorter than the "
              f"{fade}s crossfade and were extended to {min_dur}s ({names})")
    if fitted:
        scale, _ = fitted
        direction = 'stretched' if scale > 1 else 'shortened'
        print(f"  Note       : image times {direction} ×{scale:.2f} so the video "
              f"ends with the narration")
        # Once an image is only a little longer than the dissolve, the video is
        # mostly dissolve and reads as mush.
        shortest = min(durations)
        if shortest < fade * 2.5:
            print(f"  Note       : the shortest image is now {shortest:.1f}s against a "
                  f"{fade}s crossfade —")
            print(f"               lower 'crossfade' in timing.txt if the result looks smeared")
    for p in problems:
        print(f"  Timing file: {p}")
    print()

    if not narration and not music:
        print("  No narration or music found — building a silent video.")
        print("  (Name them narration.mp3 and music.mp3 to include them.)\n")

    out_path = os.path.join(folder, 'video.mp4')
    work = tempfile.mkdtemp(prefix='video-maker-')
    silent_path = os.path.join(work, 'picture.mp4')
    audio_path = os.path.join(work, 'sound.m4a')

    def run(cmd, graph, what):
        graph_file = os.path.join(work, f'{what}.txt')
        with open(graph_file, 'w', encoding='utf-8') as fh:
            fh.write(graph)
        full = cmd[:1] + ['-y', '-hide_banner', '-loglevel', 'error', '-stats'] + cmd[1:]
        idx = full.index('-filter_complex_script') + 1
        full[idx] = graph_file
        if os.environ.get('SHOW_FFMPEG'):
            print('  ' + shlex.join(full) + '\n' + graph + '\n')
        if subprocess.run(full).returncode != 0:
            raise Fail(
                f"ffmpeg couldn't build the {what}.\n"
                "The message above says why — usually a damaged image, or an\n"
                "audio file in a format ffmpeg can't read."
            )

    # ---- pass 1: picture -------------------------------------------------
    print("  Rendering the picture… this is the slow part.\n")
    vcmd = ['ffmpeg']
    for path, dur in zip(images, durations):
        vcmd += ['-loop', '1', '-t', f'{dur:.3f}', '-i', path]
    vcmd += [
        # A filter graph with one input per image (dozens to hundreds) makes
        # ffmpeg's default per-filter threading spin up far more threads than
        # the OS allows in one process, which fails as swscale/encoder errors
        # that look nothing like a threading problem. Capping the graph to a
        # single thread avoids that; the encoder below still runs multi-threaded.
        '-filter_complex_threads', '1',
        '-filter_complex_script', 'PLACEHOLDER',
        '-map', '[vout]', '-an',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', str(settings['quality']),
        '-pix_fmt', 'yuv420p', '-r', str(settings['fps']),
        silent_path
    ]
    run(vcmd, build_video_graph(images, durations, settings, video_len), 'picture')

    # ---- pass 2: sound ---------------------------------------------------
    if narration or music:
        print("\n  Mixing the sound…\n")
        acmd = ['ffmpeg']
        if narration:
            acmd += ['-i', narration]
        if music:
            # A finite repeat count, not -1: an endless input leaves ffmpeg
            # deciding when to stop reading it, which is not something to leave
            # to chance when the result is silence you might not notice.
            music_len = duration_of(music)
            repeats = 0 if music_len <= 0 else max(0, math.ceil(video_len / music_len))
            acmd += ['-stream_loop', str(repeats), '-i', music]
        acmd += [
            '-filter_complex_script', 'PLACEHOLDER',
            '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k',
            audio_path
        ]
        run(acmd, build_audio_graph(settings, narration, music, video_len), 'sound')

    # ---- pass 3: put them together --------------------------------------
    mux = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', silent_path]
    if narration or music:
        mux += ['-i', audio_path, '-map', '0:v', '-map', '1:a']
    mux += ['-c', 'copy', '-movflags', '+faststart', out_path]
    if subprocess.run(mux).returncode != 0:
        raise Fail("Couldn't combine the picture and sound.")

    shutil.rmtree(work, ignore_errors=True)

    size_mb = os.path.getsize(out_path) / 1_000_000
    print(f"\n  Done: {out_path}")
    print(f"  {human(duration_of(out_path))} · {size_mb:.0f} MB")


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
