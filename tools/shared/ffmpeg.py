"""
Finding ffmpeg, and asking it about a file.

Every tool here shells out to ffmpeg and ffprobe. Which binary that turns out
to be is worth deciding in one place: see `select()` for why.
"""

import os
import re
import subprocess

# Places a working ffmpeg usually lives. PATH alone isn't enough: run from a
# code editor, PATH is often not the one you get in Terminal, so a stripped-down
# ffmpeg can shadow the full one and reinstalling appears to change nothing.
FFMPEG_DIRS = ('/opt/homebrew/bin', '/usr/local/bin', '/opt/local/bin',
               '/usr/bin', '/snap/bin')

FFMPEG = 'ffmpeg'      # replaced by select()
FFPROBE = 'ffprobe'


def candidates():
    found = []
    which = subprocess.run(['which', '-a', 'ffmpeg'], capture_output=True, text=True)
    if which.returncode == 0:
        found += [l.strip() for l in which.stdout.splitlines() if l.strip()]
    found += [os.path.join(d, 'ffmpeg') for d in FFMPEG_DIRS]
    out, seen = [], set()
    for p in found:
        real = os.path.realpath(p)
        if real not in seen and os.path.isfile(p) and os.access(p, os.X_OK):
            seen.add(real)
            out.append(p)
    return out


def has_subtitles_filter(binary):
    try:
        listing = subprocess.run([binary, '-hide_banner', '-filters'],
                                 capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return re.search(r'^\s*\S+\s+subtitles\s', listing.stdout or '', re.M) is not None


def choose():
    """The first ffmpeg on this machine that can actually burn in subtitles."""
    found = candidates()
    capable = [b for b in found if has_subtitles_filter(b)]
    chosen = capable[0] if capable else (found[0] if found else 'ffmpeg')
    return chosen, found, capable


def select():
    """
    Point this module at the best ffmpeg available, and the ffprobe beside it.

    Only the captions tool needs a *particular* ffmpeg — burning in subtitles
    needs a build with libass, and Homebrew's often isn't. The others are happy
    with whatever is on PATH, so they never call this and the defaults stand.

    Returns (chosen, all candidates, capable ones) so the caller can explain
    itself when nothing on the machine can do the job.
    """
    global FFMPEG, FFPROBE
    FFMPEG, found, capable = choose()
    beside = os.path.join(os.path.dirname(FFMPEG), 'ffprobe')
    FFPROBE = beside if os.path.isfile(beside) else 'ffprobe'
    return FFMPEG, found, capable


def duration_of(path):
    """Length in seconds, or 0.0 if ffprobe can't tell."""
    out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'default=nw=1:nk=1', path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def stream_field(path, stream, field):
    """One field off one stream — 'v:0' and 'width', say. '' when absent."""
    out = subprocess.run(
        [FFPROBE, '-v', 'error', '-select_streams', stream,
         '-show_entries', f'stream={field}' if stream != 'fmt' else 'format=duration',
         '-of', 'default=nw=1:nk=1', path],
        capture_output=True, text=True).stdout.strip().split('\n')[0]
    return out
