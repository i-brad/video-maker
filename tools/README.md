# tools/

The code. You don't need to come in here to make a video — double-click one of
the `.command` files in the folder above instead.

One folder per tool, so everything a tool needs sits next to it:

| Folder | Does | Launched by |
|---|---|---|
| `pipeline/` | runs the four below, in order | Make Everything.command |
| `video/` | a folder of stills → a narrated video | Make Video.command |
| `scroll/` | one still, script scrolling beside it | Make Scrolling Video.command |
| `character/` | your character over a looping stock clip | Make Character Video.command |
| `captions/` | burns captions in, writes a .srt | Add Captions.command |
| `sticker/` | slides a like/subscribe sticker in | Add Sticker.command |
| `compress/` | shrinks the finished file for upload | Compress for YouTube.command |
| `shared/` | the parts more than one tool needs | — |

## Where things live

A tool's own helpers stay in the tool's folder — easier to follow sitting next
to the only thing that calls them. `shared/` holds what genuinely has two or
more callers, and nothing else:

| `shared/` | Holds | Used by |
|---|---|---|
| `errors.py` | `Fail` — an error to show as a sentence, not a stack trace | all five |
| `ffmpeg.py` | finding ffmpeg, asking a file its length and size | all five |
| `fonts.py` | which installed font can actually draw this text | captions, scroll, character |
| `audio.py` | finding narration/music, and the ducking mix | video, scroll |

No tool imports another tool. If two of them start needing the same thing, it
moves into `shared/` — that's the only rule here.

`pipeline/` doesn't break that rule: it doesn't import the other tools, it
*runs* them, each as its own process, the same way the `.command` files do. So
a tool that crashes can't take the run down with it, and each one keeps the
error messages it already has. Steps hand work to each other through
filenames — `video.mp4` → `video_with_captions.mp4` → `video_with_sticker.mp4`
— which is how they already worked before the pipeline existed. Adding a step
means adding an entry to `plan()`, not touching any of the tools.

## Reading a file's settings

Each tool reads its settings from a plain text file in *your* folder, not this
one, and every setting is optional:

| Tool | Settings file | Sample |
|---|---|---|
| pipeline | none — command-line flags | — |
| video | `timing.txt` | `video/sample-timing.txt` |
| scroll | `scroll.txt` | `scroll/sample-scroll.txt` |
| character | `character.txt` | `character/sample-character.txt` |
| captions | `captions.txt` | — |
| sticker | `sticker.txt` | — |
| compress | command-line flags | — |

The defaults are the `DEFAULTS` dict at the top of each tool's script.

## Running one directly

The launchers pass the folder to work on. From the command line you can point a
tool anywhere:

```bash
python3 tools/video/build_video.py /path/to/folder
```

With no argument they use the folder you're standing in. Set `SHOW_FFMPEG=1` to
print the ffmpeg command and filter graph before each run.
