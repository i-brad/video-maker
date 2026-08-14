# Video Maker

**Double-click _Make Everything.command_.** That's the whole thing — video,
captions, sticker, and a file sized for upload, in one run.

The four steps it runs are also there on their own, for when you only want to
redo one of them. Each reads the previous one's output and leaves it untouched,
so redoing a step never means rebuilding from scratch.

| Double-click                          | Reads                                        | Writes                                    |
| ------------------------------------- | -------------------------------------------- | ----------------------------------------- |
| **Make Everything.command**           | everything below, in order                   | all of it                                 |
| **Make Video.command**                | `images/`, `timing.txt`, audio               | `video.mp4`                               |
| _or_ **Make Scrolling Video.command** | one image, `script.txt`, audio               | `video.mp4`                               |
| _or_ **Make Character Video.command** | a stock clip, a character cutout, `character.txt`, audio | `video.mp4`                  |
| **Add Captions.command**              | `video.mp4` + `captions.srt` or `script.txt` | `video_with_captions.mp4`, `captions.srt` |
| **Add Sticker.command**               | the captioned video if there is one          | `video_with_sticker.mp4`                  |
| **Compress for YouTube.command**      | the most finished video present              | `..._youtube.mp4`                         |

Each step picks the most finished file it can find, so the order matters but you
don't have to tell it which file to use. Skip any step you don't need — compress
straight after **Make Video** if you're not adding captions or a sticker.

**All the files must sit in the same folder as your video.** The `.command`
launchers work on the folder they're in.

Turns a folder of images into a narrated video. Each image gets its own duration,
a slow Ken Burns move, and a dissolve into the next. Narration sits on top of a
music bed that automatically drops out of the way whenever the voice is talking.

---

## One-time setup

It needs **ffmpeg**, which isn't part of macOS. Open Terminal and paste:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install ffmpeg
```

The first line installs Homebrew (skip it if you already have it). If you forget,
the tool tells you exactly this when you run it.

---

## Using it

Put everything in one folder:

```
Episode 09/
    Make Everything.command ← double-click this
    Make Video.command      ← or just one step at a time
    Add Captions.command
    Add Sticker.command
    Make Scrolling Video.command
    Make Character Video.command
    Compress for YouTube.command

    timing.txt              ← your files, all optional
    script.txt
    character.txt
    narration.mp3
    music.mp3
    images/
        09-namak_scene_01.png
        09-namak_scene_02.png
        …
    stock.mp4                ← for Make Character Video
    character.png

    tools/                  ← the code; you never need to open it
```

Your files and the launchers live at the top. Everything inside `tools/` is the
machinery — one folder per tool, plus a `shared/` folder for the parts more than
one of them needs. There's a [map of it](tools/README.md) if you're curious.

Images can sit loose in the folder or in an `images` subfolder. They play in
filename order, counting properly, so `scene_2` comes before `scene_10`.

Audio is found by name: anything containing **narration**, **voice** or **vo** is
the voice track; anything containing **music**, **bgm** or **score** is the bed.
Either can be left out.

Double-click **Make Video.command**. It prints what it found, renders, and opens
the folder with `video.mp4` in it.

---

## Doing it all in one go

Double-click **Make Everything.command**. It runs the four steps in order and
prints a summary at the end:

```
  ✓ Video            4m 12s  →  video.mp4
  ✓ Captions         1m 30s  →  video_with_captions.mp4
  ✓ Sticker            48s   →  video_with_sticker.mp4
  – Compress         skipped — no smaller file to be had (see above)

  Upload: video_with_sticker.mp4  (412.0 MB)
```

The last line is the one to upload, and it's worked out from what's actually on
disk rather than from what was meant to happen — so a step that failed or was
skipped can't leave it pointing at a file that was never made.

**A step with nothing to work on is skipped, not failed.** No `sticker.txt`
means no sticker, and the run carries on. **A step that fails doesn't stop the
run either** — captions failing shouldn't also cost you the sticker. Only the
video build is treated as fatal, because there's nothing to caption without it.
Either way the summary says what didn't happen, so a missing piece can't pass
unnoticed.

**It builds the slideshow.** A `scroll.txt` and an `images/` folder don't
conflict, so most folders are set up for both — slideshow is the default and it
says so on the way past. Add `--scroll`, or double-click **Make Scrolling
Video.command**, for the other kind. The only time it decides for itself is when
there aren't enough images to make a slideshow at all.

Anything left over from a previous run that's older than the video just built is
deleted, and it says which files. This matters more than it sounds: if captions
fail and yesterday's `video_with_captions.mp4` is still lying there, the sticker
step would happily pick it up and hand you a finished video built from the wrong
footage.

Everything is non-interactive — it never stops to ask a question, so it's safe
to leave running.

---

## Running it by hand

Double-clicking runs all four steps with no options. To leave a step out or
change what it builds, run it from Terminal instead.

**Getting there:** open Terminal, type `cd ` (with the space), then drag your
episode folder onto the window and press Return. You're now standing in the
folder, which is what every command below assumes.

```
python3 tools/pipeline/make_all.py
```

That's the same thing the double-click does. Add flags to change it:

| Flag            | What it does                                                      |
| --------------- | ----------------------------------------------------------------- |
| `--dry-run`     | Prints which steps would run and stops. Builds nothing            |
| `--resume`      | Keeps the `video.mp4` already there instead of rendering it again |
| `--no-captions` | No captions                                                       |
| `--no-sticker`  | No sticker                                                        |
| `--no-compress` | Stop after the sticker                                            |
| `--slideshow`   | Many images, one per scene. The default                           |
| `--scroll`      | One image, the script scrolling beside it                         |
| `--character`   | Your character standing over a looping stock/backdrop clip        |

Flags combine in any order. To run it without `cd`-ing in first, name the script
and the folder in full — the short `tools/…` path only works from inside:

```
python3 ~/Documents/video-maker/tools/pipeline/make_all.py ~/Documents/video-maker --resume
```

### The ones worth knowing

```
python3 tools/pipeline/make_all.py --dry-run
```

Says what it's about to do and changes nothing. Worth ten seconds before
committing an hour to a render — it's how you catch a missing `sticker.txt`
before rather than after.

```
python3 tools/pipeline/make_all.py --resume
```

**The most useful one.** The video is the slow step by a wide margin; captions,
sticker and compress are minutes. If the video is fine and you only want to
redo what comes after it, `--resume` saves the whole render. Change
`sticker.txt`, run this, and you have a new sticker in a couple of minutes
instead of an hour.

```
python3 tools/pipeline/make_all.py --resume --no-captions --no-compress
```

Just the sticker, on the video already there. Flags stack like this, so any
single step can be run on its own — though for one step the matching
`.command` file is usually less typing.

```
python3 tools/pipeline/make_all.py --scroll
```

The scrolling-text video instead of the slideshow, then captions and sticker as
normal.

### If it stops

An unknown flag is refused before anything is built, and it lists the real ones:

```
  Don't know the option --nocaptions.
  There is: --dry-run, --no-captions, --no-compress, --no-sticker, --resume, --scroll, --slideshow
```

Exit code is `0` if the run finished, `1` if the video failed or a flag was
wrong — so it can go in a script if you ever want it to.

Ctrl-C stops it cleanly between steps. Anything already finished stays on disk,
so `--resume` picks up from there.

---

## timing.txt

```
default: 8
crossfade: 1

scene_01: 12
scene_02: 9
```

`default` covers every image you don't list. A name matches any filename
containing it, so `scene_01` is enough for `09-namak_scene_01.png`.

Other settings you can put in the same file:

| Setting        | Default   | What it does                                                |
| -------------- | --------- | ----------------------------------------------------------- |
| `crossfade`    | 1         | Seconds of dissolve between images                          |
| `size`         | 1920x1080 | Use `3840x2160` for 4K (much slower)                        |
| `fps`          | 30        | Frames per second                                           |
| `zoom`         | 0.12      | How far the Ken Burns move travels; `0` for perfectly still |
| `music volume` | 0.18      | Before ducking. `0.10` quieter, `0.30` louder               |
| `quality`      | 18        | x264 CRF — lower is better quality and a bigger file        |

Mistakes in this file are reported by line number and the render continues, so a
typo doesn't cost you an hour.

---

## Missing files are fine

| What you have           | What happens                                                 |
| ----------------------- | ------------------------------------------------------------ |
| Narration **and** music | Music plays under the voice, ducked, and carries any tail    |
| **Narration only**      | Voice plays; the video is fitted to it, so nothing is silent |
| **Music only**          | Music plays the whole way through, no ducking                |
| Neither                 | A silent video, and it says so before starting               |
| No `timing.txt`         | Every image gets the default 8 seconds                       |

The audio track is always padded to the exact length of the video. An audio
stream that stops early makes some players and uploaders treat the file as
damaged.

---

## Your timings are relative, not absolute

**Whenever there's a narration file, the video is fitted to it.** Every image's
time is scaled by the same factor so the video ends exactly when the voice does —
no silent tail, no narration cut off mid-sentence.

Relative pacing is preserved. If you write:

```
scene_01: 12
scene_02: 9
scene_03: 7
default: 3
```

against a 20-second narration, those become 8.5 / 6.4 / 4.9 / 2.1 / 2.1 — the
same proportions, fitted to the voice. It tells you the factor it used.

This means **the numbers only have to be right relative to each other.** Getting
scene 1 roughly twice as long as scene 4 matters; whether you wrote 12 and 6 or
40 and 20 does not.

If a fit squeezes images down close to the crossfade length, it says so and
suggests lowering `crossfade`.

---

## How long and how the audio behaves

The video is the sum of your durations minus the crossfade overlaps. If the
narration runs longer than that, **the last image is held** so the voice is never
cut off mid-sentence — it tells you when this happens.

Music loops if it's shorter than the video, ducks about **16 dB** under the
narration and comes back up in the gaps, fades in over 1 second and out over the
last 2.

An image shorter than the crossfade would produce a black flash, so any such
image is quietly extended and the change is reported.

---

## If something looks wrong

**It renders but the second half is silent** — that was a real bug and is fixed;
if you see it again, the audio is built in its own pass now, so try running again
and check whether `sound` or `picture` is named in the error.

**Motion looks jittery** — set `zoom: 0` for still frames, or raise `fps` to 60.

**Render is slow** — expected. A 15-minute 1080p video takes roughly 10–20
minutes. 4K takes several times longer. It's the Ken Burns move that costs.

**A prompt about an unidentified developer** — right-click _Make Video.command_
→ _Open_ the first time, instead of double-clicking.

---

## Captions

Give it either:

- **`captions.srt`** — already timed. Used exactly as written, most accurate.
- **`script.txt`** — your narration text. Split into caption-sized lines and
  spread across the video, weighted by how much text each line holds. Good
  enough to read along with; not lip-accurate.

You get **both** a burned-in version and a `captions.srt` to upload to YouTube.

### Hindi

The script checks that the chosen font can actually draw **every** Devanagari
character in your text, by reading the font's character map — not by trusting its
name. A font missing conjuncts renders them as empty boxes and reports no error,
so this is worth checking rather than assuming.

If nothing on the machine can draw Devanagari it says so plainly instead of
producing a video full of boxes. macOS ships _Kohinoor Devanagari_ and
_Devanagari Sangam MN_, so this should not come up. Override with
`font: <name>` in `captions.txt`.

Styling lives in `captions.txt`: `size`, `bottom margin`, `max chars`,
`max lines`, `outline`, `shadow`, `colour`, `box`.

---

## The sticker

**`sticker.png` and `sticker.gif` are included** — a dark card with a thumbs-up,
"LIKE", and a red SUBSCRIBE button with a bell. The `.gif` is the same thing with
the button breathing gently. The `.png` wins if both are present; delete it to
use the animated one.

Redraw them with your own wording:

```
python3 tools/sticker/make_sticker_art.py --text "पसंद करें" --sub "अगर कहानी अच्छी लगी"
```

The card sizes itself to whatever you write, so longer text just makes a wider
sticker rather than getting cut off. Or ignore all of this and drop in your own
`sticker.png` / `.gif` / `.webm` with transparency.

`sticker.txt` controls when it shows:

```
at: 0:45, 6:30, 12:00
duration: 6
corner: bottom-left
```

Or `every: 5:00` for a regular interval. It slides in from the nearest edge,
holds, and slides back out.

For a sticker that never leaves:

```
at: always
```

It slides in at the start and stays up for the whole video. `duration` means
nothing alongside it and is ignored — it says so when you run, rather than
quietly cutting the sticker short.

---

## The scrolling-text video

An alternative to the slideshow: **one** image on the left, and the whole script
crawling up a column on the right, fading in at the bottom and out at the top.

Put in the folder: one image, `script.txt`, and your narration. Double-click
**Make Scrolling Video.command**. It writes `video.mp4`, so the sticker and
compress steps work on it exactly as before.

The crawl speed is calculated so the script finishes exactly as the narration
ends — nothing to tune, and it can't run out early or leave text unread. It
prints the speed it chose.

Settings live in `scroll.txt`:

| Setting      | Default | What it does                                                                            |
| ------------ | ------- | --------------------------------------------------------------------------------------- |
| `image fit`  | `whole` | `whole` shows the entire picture; `fill` fills the half and crops ~40% off a 16:9 still |
| `side`       | `left`  | Which side the image sits on                                                            |
| `size`       | 46      | Text size                                                                               |
| `text width` | 0.42    | Fraction of the frame the text column takes                                             |
| `fade`       | 220     | Pixels of fade at the top and bottom                                                    |
| `zoom`       | 0.10    | Slow drift on the still; `0` for perfectly still                                        |
| `length`     | —       | Seconds, only if there's no narration                                                   |

**This one works without libass.** The text is drawn with Pillow and scrolled as
an image, so it doesn't need ffmpeg's subtitle filters — unlike Add Captions,
which does. Hindi shapes correctly as long as Pillow has RAQM, and it says so if
it doesn't.

Why you might prefer it: one image instead of sixty, so character consistency
stops being a problem, it renders far faster, and the words are on screen for
viewers watching without sound.

---

## The character video

A third alternative: your channel character (a cutout PNG) standing in front of
a stock or backdrop clip that loops or trims to fit the narration — the
daily-message look, with an optional audio-reactive line across the frame and
an optional callout bar.

Put in the folder: a stock video, a character cutout, and your narration.
Double-click **Make Character Video.command**. It writes `video.mp4`, so
captions, the sticker, and compress work on it exactly as before.

**Naming what it needs to find**, same idea as everything else here — a name
match first, then a lone unambiguous file:

| What        | Looked for                                                      |
| ----------- | ---------------------------------------------------------------- |
| Stock clip  | a video named with `stock`, `background`, `backdrop`, `footage`, `bg`, or `loop` in it — or the only video in the folder, or the only one in a `stock/` subfolder |
| Character   | a `.png` or `.webp` named with `character`, `host`, `presenter`, `anchor`, `avatar`, or `narrator` in it — or the only image loose in the folder. Needs a transparent background, same as the sticker |

More than one unnamed candidate of either kind stops the run and says so,
rather than guessing. To skip the guessing entirely, name them outright in
`character.txt`:

```
character: my_character.png
stock: my_backdrop.mp4
```

A bare filename is looked up inside the video's folder; a full path
(`/Users/you/Pictures/character.png`) works too, if the file lives elsewhere.
Narration is still found by name as usual — `narration.mp3`, or anything with
`narration`, `voice`, or `vo` in it.

Settings live in `character.txt`:

```
start: 0:08
side: left
character_width: 950
waveform: 1
label: आज का दिन आपके लिए
```

| Setting            | Default   | What it does                                                       |
| ------------------ | --------- | -------------------------------------------------------------------- |
| `character`         | —         | Explicit filename or path for the character cutout — see above       |
| `stock`             | —         | Explicit filename or path for the backdrop clip — see above          |
| `start`             | 0         | Seconds (or `mm:ss`) into the stock clip to begin from — skips an intro. Every loop restarts from here too, not just the first play |
| `side`              | `left`    | Which side the character stands: `left`, `right`, `center`          |
| `character_width`   | 950       | Pixels; height follows the source image's own aspect ratio          |
| `margin`            | 60        | Distance from the side edge                                         |
| `crop_bottom`       | 0         | Pixels of the character hidden below the frame — raise this for the "cropped at the waist" look without shrinking the character |
| `waveform`          | 1         | Draw a line across the frame reacting to the narration; 0 to turn it off |
| `waveform_height`, `waveform_y` | —  | The line's size and vertical position                        |
| `waveform_colour`   | `FFFFFF` (white) | A name (`white`, `teal`, ...) or hex, no leading `#`         |
| `waveform_opacity`  | 1.0       | Below 1, the colour blends with what's behind it — see the note below |
| `label`             | —         | An optional callout bar; leave it out for none                      |
| `label_side`        | opposite the character | `left`, `right`, `center`                              |
| `label_size`, `label_margin` | — | The bar's text size and edge distance                              |
| `label_colour`, `label_bg` | `111111`, `2FD1C5` | Text colour and bar colour — name or hex, no leading `#` |
| `font`              | —         | Otherwise a font that can draw the label's script is chosen, the same way captions picks one |
| `zoom`              | 0         | A slow push on the stock footage, on top of its own motion; usually left at 0 |
| `stock_audio`       | 0         | Mix the stock clip's own sound in, quietly, under the narration — see below |
| `stock_audio_volume` | 0.15     | Before ducking; only matters if `stock_audio` is on                 |
| `length`            | follows narration, then music, then the clip's own length | Seconds, if you want to set it directly |

The stock clip loops (from `start`, not from 0) if it's shorter than the
video, and is trimmed if it's longer — same idea as the music bed in the other
two builders. The character stays up for the whole video, the way the sticker
does with `at: always`.

The waveform is drawn from the narration if there is one, otherwise the music;
with neither, it's skipped and the run says so rather than failing.

**The stock clip's own soundtrack is silent by default** — only
`narration.mp3` and `music.mp3` are ever used for audio, the same as the other
two builders. Set `stock_audio: 1` to mix the clip's own sound in too, quietly,
ducked under the narration exactly like the music bed. If the clip has no
audio track at all, this is skipped with a note rather than failing. With
music.mp3 also present, the two are summed together into one bed before
ducking, so both duck together under the voice.

**A `#` starts a comment on any line here**, the same as every other settings
file in this folder — so `waveform_colour: #FFFFFF` is read as `waveform_colour:`
with nothing after it, not as white. Write hex codes bare: `FFFFFF`, not
`#FFFFFF`. Colour names work too and don't have this problem: `white`, `teal`.

**`waveform_opacity` below 1 mixes the colour with the footage behind it.**
At the default of 1.0, `waveform_colour: white` renders as true white no
matter what's playing behind it. Lower it for a softer, more translucent line,
but expect the colour you asked for to shift depending on the background —
white at 0.85 over a strongly coloured clip will visibly pick up that colour,
which is usually not what "I set it to white" was going for.

---

## Compressing for upload

Double-click **Compress for YouTube.command**. It picks the most finished video
in the folder — sticker, then captions, then plain — and writes
`..._youtube.mp4` beside it. The original is untouched, and it never picks a
file it compressed earlier.

From Terminal if you want control:

```
python3 tools/compress/compress_for_youtube.py                  # newest video here
python3 tools/compress/compress_for_youtube.py my_video.mp4     # a particular file
python3 tools/compress/compress_for_youtube.py --quality 23     # smaller, slightly softer
python3 tools/compress/compress_for_youtube.py --target 500     # aim for 500 MB or under
```

`--quality` is a CRF number; **lower is better quality and a bigger file.**

| Value  | Use for                                             |
| ------ | --------------------------------------------------- |
| 18     | Near-lossless, big files. Overkill for upload       |
| **20** | Default. No visible loss on this kind of footage    |
| 23     | Noticeably smaller, slight softening in fine detail |
| 26     | Small. Visible on gradients and grain               |

On a 1080p test file CRF 20 gave **47% smaller**; CRF 26 gave 62%.

It reports before/after sizes and rough upload times, and warns if the length
changed or the audio went missing — a file that looks fine and isn't is the
failure worth catching.

**Don't over-compress.** YouTube re-encodes whatever you send. Compress too hard
and the artefacts you upload get baked into their version permanently. The point
of this step is a faster upload, not the smallest possible file.

Output: H.264 High, `yuv420p`, keyframe every 2 seconds, AAC 320 kbit/s at
48 kHz, `faststart`, same resolution and frame rate as the source.

---

## Notes for the curious

The picture and the sound are rendered as two separate ffmpeg passes and then
combined. Doing both in one command produced a file with correct video and
silence after the narration ended — the crossfade chain and the audio mix
interfere while scheduling. Two passes are marginally slower and entirely
predictable.

Timestamps out of the audio mixer are rebuilt from sample position
(`asetpts=N/SR/TB`) before any fade is applied. Without it, every time-based
filter reads a bogus clock: fades silenced the back half of the audio, or applied
nothing at all, with no error either way.

Run with `SHOW_FFMPEG=1` to print the exact commands and filter graphs.

The code is under `tools/`, one folder per tool, with `shared/` for the handful
of things more than one tool needs — finding ffmpeg, picking a font that can
actually draw the text, and the narration/music mix. No tool imports another
tool; when two of them need the same thing, it moves into `shared/`.
See [tools/README.md](tools/README.md).
