#!/usr/bin/env python3
"""
make_all.py — build the video, caption it, sticker it, and size it for upload,
in one run.

This is the only tool that doesn't do any work itself. It runs the other four
in order, each as its own process, and each one picks up the file the last one
left behind:

    video.mp4  →  video_with_captions.mp4  →  video_with_sticker.mp4
                                          →  ..._youtube.mp4

A step with nothing to work on is skipped and said so. A step that fails
doesn't stop the run — captions failing shouldn't cost you the sticker — but
the summary at the end says what didn't happen, and the video build failing
stops everything, because there's nothing to caption.

Normally run by double-clicking "Make Everything.command". From the command
line:

    python3 tools/pipeline/make_all.py [folder] [options]

    --slideshow      many images, one per scene (timing.txt) — the default
    --scroll         one image, the script scrolling beside it (scroll.txt)
    --character      a stock/backdrop clip with your character over it (character.txt)
    --no-captions    leave the captions off
    --no-sticker     leave the sticker off
    --no-compress    stop after the sticker
    --resume         keep the video.mp4 that's already there
    --dry-run        say what it would do, change nothing
"""

import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.errors import Fail

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}

# What each step leaves behind, most-processed last.
BUILT = ('video.mp4', 'video_with_captions.mp4', 'video_with_sticker.mp4')


# ---------------------------------------------------------------- looking around

def count_images(folder):
    d = os.path.join(folder, 'images')
    if not os.path.isdir(d):
        return 0
    return sum(1 for f in os.listdir(d)
               if os.path.splitext(f)[1].lower() in IMAGE_EXTS
               and not f.startswith('.') and 'sticker' not in f.lower())


def has_any(folder, *names):
    return any(os.path.exists(os.path.join(folder, n)) for n in names)


def has_script(folder):
    return has_any(folder, 'script.txt', 'narration.txt', 'story.txt')


def has_srt(folder):
    return any(f.lower().endswith('.srt') and 'caption' in f.lower()
               for f in os.listdir(folder))


def newest_output(folder):
    """The file to upload: the most-processed video, as long as it's current."""
    mtime = lambda f: os.path.getmtime(os.path.join(folder, f))

    best = next((n for n in reversed(BUILT)
                 if os.path.exists(os.path.join(folder, n))), None)
    if best is None:
        return None

    # A compressed file only supersedes the video it was made from if it's
    # newer. An old one left from a previous run is not the file to upload.
    made = [f for f in os.listdir(folder)
            if f.endswith('_youtube.mp4') and mtime(f) >= mtime(best)]
    return max(made, key=mtime) if made else best


def drop_stale(folder, after, made):
    """Remove leftovers from an earlier run that are older than what we just built.

    Without this, a captions step that fails leaves yesterday's
    video_with_captions.mp4 in place — and the sticker step picks it up, so you
    get a finished file made from the wrong video and no sign anything is off.
    """
    ref = os.path.join(folder, after)
    if not os.path.exists(ref):
        return
    cutoff = os.path.getmtime(ref)
    for name in made:
        p = os.path.join(folder, name)
        if os.path.exists(p) and os.path.getmtime(p) < cutoff:
            try:
                os.remove(p)
                print(f"  Removed {name} — left over from an earlier run, older than {after}.")
            except OSError as exc:
                # A read-only folder or a file someone else has open. Worth
                # saying out loud, not worth ending the run over.
                print(f"  Couldn't remove the old {name} ({exc.strerror}).")
                print(f"  Delete it yourself before uploading — it's from an earlier run.")


# ---------------------------------------------------------------- which video style

def choose_mode(folder, argv):
    """Slideshow, unless you ask for scrolling, character, or it's the only one possible."""
    if '--character' in argv:
        return 'character'
    if '--scroll' in argv:
        return 'scroll'
    if '--slideshow' in argv:
        return 'slideshow'

    slideshow = count_images(folder) >= 2
    # The scroll tool falls back to any image it can find, so what really marks
    # a folder as set up for scrolling is scroll.txt plus text to scroll.
    scroll = os.path.exists(os.path.join(folder, 'scroll.txt')) and has_script(folder)

    # A scroll.txt and an images/ folder don't conflict, so both can be set up
    # at once and usually are. Slideshow is the default; it only gives way when
    # there aren't the images to make one.
    if scroll and not slideshow:
        return 'scroll'
    if scroll:
        print("  Both kinds are set up here — making the slideshow.")
        print("  Add --scroll for the scrolling-text one instead.")
        print()
    return 'slideshow'


# ---------------------------------------------------------------- the steps

def plan(folder, argv):
    mode = choose_mode(folder, argv)
    resume = '--resume' in argv
    have_video = os.path.exists(os.path.join(folder, 'video.mp4'))

    steps = []

    if mode == 'scroll':
        steps.append({
            'name': 'Scrolling video',
            'script': 'scroll/make_scroll_video.py',
            'makes': 'video.mp4',
            'required': True,
            'skip': ('using the video.mp4 already here' if resume and have_video else None),
        })
    elif mode == 'character':
        steps.append({
            'name': 'Character video',
            'script': 'character/make_character_video.py',
            'makes': 'video.mp4',
            'required': True,
            'skip': ('using the video.mp4 already here' if resume and have_video else None),
        })
    else:
        steps.append({
            'name': 'Video',
            'script': 'video/build_video.py',
            'makes': 'video.mp4',
            'required': True,
            # Nothing to build from isn't checked here on purpose — the video
            # tool's own message about what's missing is better than anything
            # this could say second-hand.
            'skip': ('using the video.mp4 already here' if resume and have_video else None),
        })

    steps.append({
        'name': 'Captions',
        'script': 'captions/add_captions.py',
        'makes': 'video_with_captions.mp4',
        'required': False,
        'skip': ('turned off with --no-captions' if '--no-captions' in argv else
                 None if (has_srt(folder) or has_script(folder)) else
                 'no captions.srt or script.txt to caption from'),
    })

    steps.append({
        'name': 'Sticker',
        'script': 'sticker/add_sticker.py',
        'makes': 'video_with_sticker.mp4',
        'required': False,
        # sticker.txt is where the times live, and there's no sensible default
        # for when a like-and-subscribe badge should appear.
        'skip': ('turned off with --no-sticker' if '--no-sticker' in argv else
                 None if os.path.exists(os.path.join(folder, 'sticker.txt')) else
                 'no sticker.txt saying when it should appear'),
    })

    steps.append({
        'name': 'Compress',
        'script': 'compress/compress_for_youtube.py',
        'makes': None,
        # It declines when the file is already small enough, which is a success,
        # not a failure — but reporting it as "done" would suggest a compressed
        # file exists. Whether one appeared is the honest thing to check.
        'watch': '_youtube.mp4',
        'required': False,
        'skip': ('turned off with --no-compress' if '--no-compress' in argv else None),
    })

    return mode, steps


def run_step(step, folder):
    script = os.path.join(TOOLS, step['script'])
    if not os.path.exists(script):
        raise Fail(f"{step['script']} is missing from tools/.")
    # Its own process, so a crash in one tool can't take the run down with it,
    # and each keeps the error handling it already has. Output isn't captured —
    # ffmpeg's progress should appear as it happens, not in a lump at the end.
    #
    # start_new_session puts the tool outside the terminal's process group, so
    # Ctrl-C arrives here and nowhere else. Left in the same group, everything
    # gets the signal at once and the tool usually reports it as its ffmpeg
    # having died — indistinguishable from a real failure, which means an
    # interrupted step reads as a failed one and the run carries on to the next.
    proc = subprocess.Popen([sys.executable, script, folder], start_new_session=True)
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        print("\n  Stopping…")
        # Ours to pass on now, and ours to insist on: ask, then tell, then
        # stop asking. A half-written file left by a killed ffmpeg lives in a
        # temp folder, so nothing here can be left in a broken state.
        for sig, grace in ((signal.SIGINT, 10), (signal.SIGTERM, 5)):
            try:
                proc.send_signal(sig)
                proc.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            proc.kill()
        raise
    if code in (-signal.SIGINT, 130):
        # The tool has already said "Stopped." on its way out; the tag tells
        # the handler below not to say it a second time.
        raise KeyboardInterrupt('already reported')
    return code == 0


def banner(n, total, name):
    print()
    print(f"  ── {n}/{total}  {name} " + "─" * max(0, 44 - len(name)))
    print()


def human(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def main(folder, argv):
    # The tools this runs write straight to the same terminal. Without this,
    # our own headings sit in a buffer until the end and the log reads as if
    # every step ran after all of them had finished.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    if not os.path.isdir(folder):
        raise Fail(f"There's no folder at {folder}.")

    known = {'--slideshow', '--scroll', '--character', '--no-captions', '--no-sticker',
             '--no-compress', '--resume', '--dry-run'}
    for a in argv:
        if a.startswith('--') and a not in known:
            raise Fail(f"Don't know the option {a}.\n"
                       f"    There is: {', '.join(sorted(known))}")

    mode, steps = plan(folder, argv)

    if '--dry-run' in argv:
        print(f"  In {folder}:")
        print()
        for i, step in enumerate(steps, 1):
            if step['skip']:
                print(f"  {i}. {step['name']:<16} skip — {step['skip']}")
            else:
                print(f"  {i}. {step['name']:<16} run"
                      + (f"  →  {step['makes']}" if step['makes'] else ""))
        print()
        print("  Nothing was changed. Run it again without --dry-run to build.")
        print()
        return

    started = time.time()
    results = []

    for i, step in enumerate(steps, 1):
        if step['skip']:
            # Skipping a step the rest depends on is only safe when its output
            # is already sitting there from a previous run.
            if step['required'] and not os.path.exists(os.path.join(folder, step['makes'])):
                raise Fail(f"Can't skip the {step['name'].lower()} step — {step['skip']},\n"
                           f"  and there's no {step['makes']} here to carry on from.")
            results.append((step['name'], 'skipped', step['skip'], 0))
            continue

        banner(i, len(steps), step['name'])
        watched = step.get('watch')
        before = ({f for f in os.listdir(folder) if f.endswith(watched)}
                  if watched else set())
        t0 = time.time()
        ok = run_step(step, folder)
        took = time.time() - t0

        if ok and watched:
            after = {f for f in os.listdir(folder) if f.endswith(watched)}
            new = after - before
            if not new:
                results.append((step['name'], 'skipped',
                                'no smaller file to be had (see above)', took))
                continue
            step = dict(step, makes=sorted(new)[0])

        if ok:
            results.append((step['name'], 'done', step['makes'], took))
            # Anything downstream that predates what we just made is from an
            # older run and must not be picked up as if it were current.
            if step['makes'] in BUILT:
                drop_stale(folder, step['makes'],
                           BUILT[BUILT.index(step['makes']) + 1:])
        else:
            results.append((step['name'], 'failed', None, took))
            if step['required']:
                print()
                raise Fail("The video didn't build, so there's nothing to caption or\n"
                           "  sticker. The message above says what went wrong.")

    # ---- summary
    print()
    print("  ────────────────────────────────────────────")
    print(f"   Finished in {human(time.time() - started)}")
    print("  ────────────────────────────────────────────")
    print()
    for name, state, detail, took in results:
        mark = {'done': '✓', 'skipped': '–', 'failed': '✗'}[state]
        line = f"  {mark} {name:<16}"
        if state == 'done':
            line += f" {human(took)}" + (f"  →  {detail}" if detail else "")
        elif state == 'skipped':
            line += f" skipped — {detail}"
        else:
            line += " failed — see the message above"
        print(line)

    final = newest_output(folder)
    if final:
        size = os.path.getsize(os.path.join(folder, final)) / 1e6
        print()
        print(f"  Upload: {final}  ({size:.1f} MB)" if size < 100 else
              f"  Upload: {final}  ({size:.0f} MB)")
    if any(s == 'failed' for _, s, _, _ in results):
        print()
        print("  Some steps didn't run. Fix what the message says and either run")
        print("  this again, or double-click just that step's own .command file.")
    print()


if __name__ == '__main__':
    # The folder you're standing in, not the one this script lives in — the
    # launcher cd's to your video folder first, so this is right for
    # double-clicking too.
    args = sys.argv[1:]
    target = next((a for a in args if not a.startswith('--') and os.path.isdir(a)),
                  os.getcwd())
    try:
        main(target, [a for a in args if a != target])
    except Fail as exc:
        print(f"\n  {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt as exc:
        if 'already reported' not in exc.args:
            print("\n  Stopped.\n")
        sys.exit(130)
