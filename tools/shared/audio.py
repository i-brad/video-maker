"""
Finding the narration and music, and mixing them.

Shared because both video builders — the slideshow and the scrolling one — end
up with the same problem once the picture is rendered: one voice track, one
music bed, and a fixed length to fill.
"""

import os

AUDIO_EXTS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}
NARRATION_HINTS = ('narration', 'narrate', 'voice', 'vo', 'speech', 'dialogue')
MUSIC_HINTS = ('music', 'bgm', 'score', 'background', 'bed')


def find_audio(folder, hints):
    for f in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(f)
        if ext.lower() in AUDIO_EXTS and any(h in stem.lower() for h in hints):
            return os.path.join(folder, f)
    return None


def build_audio_graph(s, narration, music, video_len):
    """
    Built and rendered separately from the video, deliberately.

    Sharing one ffmpeg command produced a file where the audio simply stopped
    when the narration ended — the crossfade chain and the audio mix interfere
    while scheduling, and the result looks completely normal: right length,
    right video, silence for the back half. Two passes and a copy-mux is a
    little slower and entirely predictable.
    """
    lines = []
    narr_idx, music_idx = (0, 1) if narration else (None, 0)
    if narration and not music:
        music_idx = None

    if narration and music:
        lines.append(f"[{narr_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[n1][n2]")
        lines.append(
            f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={s['music_volume']}[mq]"
        )
        # Ducking: the music is compressed using the voice as the trigger, so it
        # drops under narration and comes back up in the gaps.
        #
        # The trigger copy is padded with silence to the full length first.
        # sidechaincompress stops when its key input runs out, so an unpadded
        # narration silences the music for the rest of the video.
        lines.append(f"[n1]apad=whole_dur={video_len:.3f}[key]")
        # threshold 0.005 / ratio 20 measures ~16 dB of ducking, which puts the
        # voice clearly on top. 0.02 / 12 only managed 5.7 dB — audible, but the
        # music still competed with the narration.
        lines.append(
            "[mq][key]sidechaincompress=threshold=0.005:ratio=20:attack=15:release=350[mduck]"
        )
        # duration=longest, not first: the narration usually ends before the
        # video does, and `first` would cut the music dead at that moment,
        # leaving the rest of the video silent. atrim below sets the real end.
        lines.append("[n2][mduck]amix=inputs=2:normalize=0:duration=longest[amixed]")
        src = 'amixed'
    elif narration:
        lines.append(f"[{narr_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[amixed]")
        src = 'amixed'
    else:
        lines.append(
            f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={s['music_volume']}[amixed]"
        )
        src = 'amixed'

    # asetpts=N/SR/TB rebuilds timestamps from the sample count.
    #
    # PTS-STARTPTS leaves the mix with timestamps that jump once the narration
    # ends, so every time-based filter after it sees a bogus `t`: afade silenced
    # the whole back half, and a fade-out applied nothing at all. Rebuilding
    # from sample position makes `t` mean what it says.
    #
    # The fade is a plain gain expression rather than afade for the same reason:
    # its behaviour here is explicit and verifiable in seconds.
    fade_in, fade_out = 1.0, 2.0
    gain = (f"min(1,t/{fade_in})"
            f"*max(0,min(1,({video_len:.3f}-t)/{fade_out}))")
    # apad first, so the audio stream always runs the full length of the video.
    # Without it a short narration leaves an audio track that stops early, which
    # some players and uploads treat as a damaged file.
    lines.append(
        f"[{src}]apad=whole_dur={video_len:.3f},atrim=duration={video_len:.3f},"
        f"asetpts=N/SR/TB,volume='{gain}':eval=frame[aout]"
    )
    return ';\n'.join(lines)
