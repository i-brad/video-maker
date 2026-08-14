#!/usr/bin/env python3
"""
compress_for_youtube.py — shrink a finished video for upload.

YouTube re-encodes whatever you send it, so the goal isn't the smallest possible
file — it's the smallest file that still gives YouTube's encoder clean material
to work from. Compress too hard and you upload artefacts that get baked in.

    Double-click "Compress for YouTube.command", or:

    python3 tools/compress/compress_for_youtube.py
    python3 tools/compress/compress_for_youtube.py my_video.mp4
    python3 tools/compress/compress_for_youtube.py --target 500   # aim for 500 MB
    python3 tools/compress/compress_for_youtube.py --quality 22   # smaller, softer
"""

import json
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.errors import Fail

# Sensible for 1080p talking/storytelling footage. Lower = better and bigger.
DEFAULT_CRF = 20
AUDIO_BITRATE = '320k'          # YouTube caps at 384k stereo AAC; 320 is ample
PREFERRED_ORDER = (
    'video_with_sticker.mp4', 'video_with_captions.mp4', 'video.mp4',
)


def probe(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-of', 'json',
         '-show_format', '-show_streams', path],
        capture_output=True, text=True)
    if out.returncode != 0:
        # Most often this is a half-written file from a step that was stopped
        # partway, not something that was never a video.
        size = os.path.getsize(path) if os.path.exists(path) else 0
        raise Fail(
            f"Can't read {os.path.basename(path)}.\n"
            f"    It's {size / 1_000_000:.1f} MB but ffmpeg can't make sense of it, which\n"
            "    usually means the step that produced it was interrupted. Delete it\n"
            "    and run that step again."
        )
    data = json.loads(out.stdout)
    v = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    a = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
    if not v:
        raise Fail(f"{os.path.basename(path)} has no video in it.")
    num, den = (v.get('r_frame_rate') or '30/1').split('/')
    audio_kbps = 0
    if a is not None:
        try:
            audio_kbps = int(a.get('bit_rate', 0)) / 1000
        except (TypeError, ValueError):
            audio_kbps = 0
    return {
        'width': int(v['width']),
        'height': int(v['height']),
        'fps': float(num) / float(den or 1),
        'duration': float(data['format'].get('duration', 0)),
        'size': int(data['format'].get('size', os.path.getsize(path))),
        'has_audio': a is not None,
        'acodec': (a or {}).get('codec_name', ''),
        'audio_kbps': audio_kbps or (192 if a is not None else 0),
        'vcodec': v.get('codec_name', '?'),
    }


def find_video(folder, explicit):
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(folder, explicit)
        if not os.path.exists(p):
            raise Fail(f"Can't find {explicit}")
        return p
    # The most finished version first, so you don't accidentally upload the one
    # without captions or the sticker.
    for name in PREFERRED_ORDER:
        p = os.path.join(folder, name)
        if os.path.exists(p):
            return p
    videos = [f for f in sorted(os.listdir(folder))
              if f.lower().endswith(('.mp4', '.mov', '.mkv'))
              and not f.startswith('.') and '_youtube' not in f]
    if not videos:
        raise Fail("No video file in this folder.")
    return os.path.join(folder, max(videos, key=lambda f: os.path.getmtime(os.path.join(folder, f))))


def human_size(b):
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.2f} GB"
    if b >= 100_000_000:
        return f"{b / 1_000_000:.0f} MB"
    # Below 100 MB, whole megabytes turn a real change into "1 MB → 1 MB".
    return f"{b / 1_000_000:.1f} MB"


def human_time(sec):
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def bitrate_for_target(target_mb, duration, has_audio):
    """Video bitrate, in kbit/s, that lands near a target file size."""
    audio_kbps = int(AUDIO_BITRATE.rstrip('k')) if has_audio else 0
    total_kbits = target_mb * 8000                     # MB -> kbit
    overhead = 1.02                                    # container
    video_kbps = (total_kbits / max(duration, 1)) / overhead - audio_kbps
    return max(500, int(video_kbps))


def recommended_kbps(info):
    """
    What YouTube asks for at this resolution and frame rate, in kbit/s.

    Used two ways: as a ceiling so the output can't balloon, and to spot a
    source that is already at or below target — where re-encoding makes the
    file *bigger*, because generation loss adds noise and noise costs bits.
    """
    height = info['height']
    fast = info['fps'] > 40
    table = [
        (2000, 53_000 if fast else 40_000),   # 2160p
        (1300, 24_000 if fast else 16_000),   # 1440p
        (1000, 12_000 if fast else 8_000),    # 1080p
        (700,   7_500 if fast else 5_000),    # 720p
        (0,     4_000 if fast else 2_500),    # smaller
    ]
    for min_height, kbps in table:
        if height >= min_height:
            return kbps
    return 8_000


def video_args(info):
    """
    Encoder settings, identical for both passes.

    x264 refuses a second pass whose settings differ from the first — 'different
    bframes setting than first pass (2 vs 3)' — and leaves a zero-byte file
    behind. Building the list once is what keeps them in step.
    """
    # A keyframe every 2 seconds is what YouTube's guidance asks for, and it
    # gives their encoder clean cut points.
    gop = max(2, int(round(info['fps'] * 2)))
    return [
        '-c:v', 'libx264', '-profile:v', 'high', '-level', '4.2',
        '-pix_fmt', 'yuv420p',
        '-g', str(gop), '-keyint_min', str(gop), '-sc_threshold', '0',
        '-bf', '2', '-preset', 'slow',
    ]


def audio_args(info):
    """
    Copy the audio when it's already AAC at a sane rate.

    Re-encoding 192k AAC up to 320k adds ~30 MB to a 30-minute video and makes
    it sound very slightly worse — a lossy pass over lossy material. That alone
    can turn a "compression" into a file that grew.
    """
    if not info['has_audio']:
        return ['-an']
    if info['acodec'] == 'aac' and info['audio_kbps'] <= 340:
        return ['-c:a', 'copy']
    return ['-c:a', 'aac', '-b:a', AUDIO_BITRATE, '-ar', '48000', '-ac', '2']


def run(cmd):
    return subprocess.run(cmd).returncode == 0


def main(folder, argv):
    explicit = next((a for a in argv if not a.startswith('--')
                     and a.lower().endswith(('.mp4', '.mov', '.mkv'))), None)

    def opt(name, cast, default):
        if name in argv:
            try:
                return cast(argv[argv.index(name) + 1])
            except (IndexError, ValueError):
                raise Fail(f"{name} needs a number after it.")
        return default

    target_mb = opt('--target', float, None)
    crf = opt('--quality', int, DEFAULT_CRF)

    src = find_video(folder, explicit)
    info = probe(src)
    if info['duration'] <= 0:
        raise Fail("That video's length reads as zero — it may be incomplete.")

    stem = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(folder, f"{stem}_youtube.mp4")

    total_kbps = info['size'] * 8 / max(info['duration'], 1) / 1000
    # Compare like with like: the recommendation tables are video-only, and a
    # long narration track is a big slice of the total.
    source_kbps = max(1.0, total_kbps - info['audio_kbps'])
    target_kbps = recommended_kbps(info)

    print(f"  File       : {os.path.basename(src)}")
    print(f"  Now        : {info['width']}x{info['height']} @ {info['fps']:.0f}fps, "
          f"{human_time(info['duration'])}, {human_size(info['size'])}")
    print(f"  Bitrate    : {source_kbps / 1000:.1f} Mbps of video "
          f"(YouTube suggests about {target_kbps / 1000:.0f} Mbps here)")
    keep_audio = info['has_audio'] and info['acodec'] == 'aac' and info['audio_kbps'] <= 340
    print(f"  Audio      : " + ('none' if not info['has_audio'] else
          f"{info['acodec']} {info['audio_kbps']:.0f}k"
          + ("  — copied as-is" if keep_audio else "  — re-encoded to 320k")))

    # Re-encoding a file that is already at or under target makes it larger,
    # not smaller — and costs a generation of quality for the privilege.
    if not target_mb and '--force' not in argv and source_kbps <= target_kbps * 1.1:
        # Not an error — the right answer is "nothing to do", so this returns
        # normally. Anything running this as one step of a longer sequence
        # would otherwise read a refusal here as the compression having failed.
        print()
        print("  This video is already at or below YouTube's suggested bitrate, so")
        print("  compressing it again would make the file BIGGER, not smaller, and")
        print("  lose a generation of quality doing it.")
        print()
        print(f"  Upload {os.path.basename(src)} as it is.")
        print()
        print("  If you need it smaller anyway — to fit a size limit or a slow")
        print("  connection — say by how much:")
        print(f"      python3 tools/compress/compress_for_youtube.py --target "
              f"{max(1, int(info['size'] / 1_500_000))}")
        print("  or force it with --force --quality 24.")
        print()
        return

    vargs, aargs = video_args(info), audio_args(info)
    base = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-stats', '-i', src]

    if target_mb:
        kbps = bitrate_for_target(target_mb, info['duration'], info['has_audio'])
        # Treated as a ceiling: x264 tends to come in under the requested
        # bitrate on easy footage, which is the harmless direction to miss.
        print(f"  Aiming for : {target_mb:.0f} MB or under  →  {kbps} kbit/s video")
        log = os.path.join(folder, '.ytpass')

        print(f"\n  Pass 1 of 2…\n")
        ok = run(base + vargs + ['-b:v', f'{kbps}k', '-pass', '1',
                                 '-passlogfile', log, '-an', '-f', 'mp4', os.devnull])
        if ok:
            print(f"\n  Pass 2 of 2…\n")
            ok = run(base + vargs + aargs + ['-b:v', f'{kbps}k', '-pass', '2',
                                             '-passlogfile', log,
                                             '-movflags', '+faststart', out])
        for junk in (log + '-0.log', log + '-0.log.mbtree', log + '.log'):
            if os.path.exists(junk):
                os.remove(junk)
        # A failed second pass leaves an empty file that looks like a result.
        if not ok and os.path.exists(out) and os.path.getsize(out) < 1000:
            os.remove(out)
    else:
        # CRF alone has no upper bound on bitrate, which is how a re-encode
        # ends up bigger than its source. Capping at the recommended rate keeps
        # the quality-driven behaviour while making growth impossible.
        cap = min(target_kbps, int(source_kbps * 0.95)) if source_kbps > 0 else target_kbps
        print(f"  Quality    : CRF {crf}  (lower is better; 18 near-lossless, 23 small)")
        print(f"  Ceiling    : {cap / 1000:.1f} Mbps, so it can't come out larger")
        print(f"\n  Compressing…\n")
        ok = run(base + vargs + aargs + [
            '-crf', str(crf),
            '-maxrate', f'{int(cap)}k', '-bufsize', f'{int(cap * 2)}k',
            '-movflags', '+faststart', out])

    if not ok or not os.path.exists(out):
        raise Fail("ffmpeg couldn't finish. The message above says why.")

    after = probe(out)
    saved = info['size'] - after['size']
    pct = saved / info['size'] * 100 if info['size'] else 0

    # "+47%" for a file that got smaller reads as though it grew.
    verdict = (f"{pct:.0f}% smaller, {human_size(saved)} saved" if saved > 0
               else f"{-pct:.0f}% larger")
    print(f"\n  Done: {out}")
    print(f"  {human_size(info['size'])} → {human_size(after['size'])}  ({verdict})")

    # A silent length change means something went wrong that still "succeeded".
    drift = abs(after['duration'] - info['duration'])
    if drift > 0.5:
        print(f"  WARNING    : the length changed by {drift:.1f}s — check the file before uploading")
    if info['has_audio'] and not after['has_audio']:
        print(f"  WARNING    : the audio track is missing from the result")
    if saved < 0:
        print(f"  Note       : the result is larger than the original, which was already\n"
              f"               well compressed. Upload the original instead, or pass\n"
              f"               --quality 23 to trade a little detail for size.")

    mbit = after['size'] * 8 / 1_000_000
    for label, speed in (('10 Mbps', 10), ('50 Mbps', 50)):
        print(f"  Upload at {label:>8}: about {human_time(mbit / speed)}")


if __name__ == '__main__':
    # The folder you're standing in, not the one the script lives in — the
    # launcher cd's here first, so this is right for double-clicking too, and
    # it stops the command line searching the wrong directory.
    folder = os.getcwd()
    args = sys.argv[1:]
    for a in args:
        if os.path.isdir(a):
            folder = a
            args = [x for x in args if x != a]
            break
    try:
        main(folder, args)
    except Fail as exc:
        print(f"\n  {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
        sys.exit(130)