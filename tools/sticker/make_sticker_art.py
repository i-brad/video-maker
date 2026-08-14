#!/usr/bin/env python3
"""
make_sticker_art.py — draw sticker.png (and a gently pulsing sticker.gif).

Run once to get artwork you can use as-is or replace. Everything is drawn from
shapes, so there are no font or licensing surprises.

Writes into the folder you run it from, so run it from your video folder:

    python3 tools/sticker/make_sticker_art.py
    python3 tools/sticker/make_sticker_art.py --text "पसंद करें"
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

SCALE = 3                      # drawn large, downsampled for clean edges
CARD = (255, 255, 255, 250)    # white card
BORDER = (15, 15, 20, 28)      # hairline ring so the card reads on light video too
SHADOW = (10, 10, 15, 110)     # soft shadow the card floats on
RED = (237, 20, 30, 255)       # button red
WHITE = (255, 255, 255, 255)
BLUE = (24, 119, 242, 255)     # like-icon blue
ORANGE = (255, 149, 5, 255)    # bell


def load_font(size, bold=True):
    candidates = [
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold
        else '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf' if bold
        else '/usr/share/fonts/truetype/google-fonts/Poppins-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def capsule(d, x0, y0, x1, y1, r, colour):
    """A pill shape between two points — used for the thumb, so it can lean
    at a natural angle instead of standing bolt upright."""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy * r, ux * r
    d.polygon([(x0 + px, y0 + py), (x1 + px, y1 + py),
               (x1 - px, y1 - py), (x0 - px, y0 - py)], fill=colour)
    d.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=colour)
    d.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=colour)


def thumbs_up(d, x, y, size, colour, groove=None):
    """A thumbs-up built from primitives — no icon font needed.

    An earlier version stacked plain rectangles (a vertical thumb dead-centred
    on a box) and read as a flashlight or a paint roller rather than a hand.
    This one gives the thumb a lean and a rounded tip, and narrows the fist
    at the knuckles so the shape reads as a fist with folded fingers. Leave
    `groove` as None for a flat, single-colour glyph (the YouTube-style look);
    pass a colour to etch knuckle creases into it instead.
    """
    u = size / 10.0
    # wrist / cuff
    d.rounded_rectangle([x - 0.3 * u, y + 6.4 * u, x + 1.7 * u, y + 9.8 * u], u * 0.65, fill=colour)
    # fist — narrower at the top, where the fingers curl over
    d.rounded_rectangle([x + 1.0 * u, y + 4.5 * u, x + 7.0 * u, y + 9.8 * u], u * 1.3, fill=colour)
    d.rounded_rectangle([x + 1.6 * u, y + 3.9 * u, x + 6.6 * u, y + 6.0 * u], u * 1.1, fill=colour)
    # thumb, leaning up and out of the fist rather than standing straight
    capsule(d, x + 2.5 * u, y + 5.1 * u, x + 4.7 * u, y + 0.6 * u, u * 1.05, colour)
    if groove:
        # knuckle lines — gaps that read as folded-finger creases
        for i in range(3):
            yy = y + 5.35 * u + i * 1.3 * u
            d.line([x + 1.7 * u, yy, x + 6.3 * u, yy], fill=groove, width=max(1, int(u * 0.2)))


def like_icon(d, x, y, size, colour):
    """The thumb plus the little divider bar next to it — the familiar
    YouTube 'like' glyph shape, not just a bare hand."""
    u = size / 10.0
    d.rounded_rectangle([x, y + 3.2 * u, x + 1.35 * u, y + 9.8 * u], u * 0.6, fill=colour)
    thumbs_up(d, x + 2.6 * u, y, size, colour)


def like_icon_width(size):
    return size * 0.96


def bell(d, x, y, size, colour):
    u = size / 10.0
    d.pieslice([x, y + 0.6 * u, x + 8 * u, y + 11 * u], 180, 360, fill=colour)
    d.rectangle([x, y + 5.6 * u, x + 8 * u, y + 7.4 * u], fill=colour)
    d.rounded_rectangle([x - 0.6 * u, y + 7.0 * u, x + 8.6 * u, y + 8.4 * u], u * 0.7, fill=colour)
    d.ellipse([x + 3.0 * u, y + 8.4 * u, x + 5.0 * u, y + 10.4 * u], fill=colour)
    d.ellipse([x + 3.4 * u, y - 0.4 * u, x + 4.6 * u, y + 1.2 * u], fill=colour)


def ease(t):
    """Smooth 0→1→0, so motion starts and stops gently instead of snapping."""
    t = max(0.0, min(1.0, t))
    return (1 - math.cos(t * math.pi * 2)) / 2


def bell_ring(phase):
    """
    Angle for the bell, in degrees.

    A short burst of decaying wobble at the start of each loop, then stillness —
    a bell that shakes constantly reads as a glitch rather than a ring.
    """
    if phase > 0.42:
        return 0.0
    t = phase / 0.42
    return math.sin(t * math.pi * 6) * 15 * (1 - t) ** 1.5


def draw(width, label, pulse=0.0, phase=None, shadow=True):
    """
    Three things side by side, floating directly on the video with nothing
    behind them — the like icon, a red SUBSCRIBED button, and a bell.
    Each keeps its own fill (the button is still a red pill, the bell still
    solid orange); there's just no card or panel behind the group.

    pulse 0..1 nudges the button's size a little, for the animated version.
    Every element's width is measured rather than guessed, and laid out with
    a left-to-right cursor, so changing one piece can't crowd another.
    """
    H = 200 * SCALE
    probe = ImageDraw.Draw(Image.new('RGBA', (10, 10)))

    pad = H * 0.20
    gap = H * 0.20
    icon_h = H * 0.56
    bh = H * 0.46
    bell_h = H * 0.62

    f_btn = load_font(int(bh * 0.42))

    label_w = probe.textlength(label, font=f_btn)
    bw = label_w + bh * 0.95

    like_w = like_icon_width(icon_h)
    u_bell = bell_h / 10.0
    bell_w = 9.2 * u_bell

    cursor = pad
    like_x = cursor
    cursor += like_w + gap
    bx1 = cursor
    cursor += bw + gap
    bell_x = cursor + 0.6 * u_bell
    cursor += bell_w + pad

    W = int(cursor)

    content = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(content)

    # Like icon, with a gentle bob so it isn't perfectly static.
    bob = 0.0
    if phase is not None:
        bob = -ease((phase * 2) % 1.0) * H * 0.03
    like_icon(d, like_x, (H - icon_h) / 2 + bob, icon_h, BLUE)

    # Button, pulsing slightly.
    grow = pulse * H * 0.03
    by1 = (H - bh) / 2
    d.rounded_rectangle([bx1 - grow, by1 - grow, bx1 + bw + grow, by1 + bh + grow],
                        (bh + 2 * grow) / 2, fill=RED)
    lb = d.textbbox((0, 0), label, font=f_btn)
    d.text((bx1 + (bw - (lb[2] - lb[0])) / 2 - lb[0], by1 + (bh - (lb[3] - lb[1])) / 2 - lb[1]),
           label, font=f_btn, fill=WHITE)

    # A gloss sweeping across the button, clipped to its rounded shape.
    if phase is not None:
        sweep = (phase - 0.45) / 0.35
        if 0 <= sweep <= 1:
            shine = Image.new('RGBA', (int(bw), int(bh)), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shine)
            band = bw * 0.22
            cx = -band + (bw + band * 2) * sweep
            for i in range(int(band)):
                a = int(70 * math.sin(math.pi * i / band))
                sd.polygon([(cx + i, bh), (cx + i + bh * 0.4, 0),
                            (cx + i + bh * 0.4 + 2, 0), (cx + i + 2, bh)],
                           fill=(255, 255, 255, a))
            mask = Image.new('L', (int(bw), int(bh)), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw - 1, bh - 1], bh / 2, fill=255)
            shine.putalpha(Image.composite(shine.getchannel('A'),
                                           Image.new('L', mask.size, 0), mask))
            content.alpha_composite(shine, (int(bx1), int(by1)))

    # Bell, standing free rather than boxed into the button — drawn on its
    # own layer so it can be rotated for the ring.
    bell_y = (H - bell_h) / 2 + 0.4 * u_bell
    angle = bell_ring(phase) if phase is not None else 0.0
    if abs(angle) < 0.01:
        bell(d, bell_x, bell_y, bell_h, ORANGE)
    else:
        pad_px = int(bell_h * 0.6)
        layer = Image.new('RGBA', (int(bell_h * 1.2) + pad_px * 2,
                                   int(bell_h * 1.4) + pad_px * 2), (0, 0, 0, 0))
        bell(ImageDraw.Draw(layer), pad_px, pad_px, bell_h, ORANGE)
        # Pivot near the top, the way a bell hangs.
        layer = layer.rotate(angle, resample=Image.BICUBIC,
                             center=(pad_px + bell_h * 0.4, pad_px))
        content.alpha_composite(layer, (int(bell_x - pad_px), int(bell_y - pad_px)))

    if not shadow:
        # Used for the GIF: a blurred shadow adds a lot of intermediate alpha
        # levels, and GIF's shared 256-colour palette across every frame turns
        # that into visible dithering. Without a card behind them, the icons'
        # own saturated colour is what keeps them readable on the video.
        return content.resize((int(width), max(1, round(width * H / W))), Image.LANCZOS)

    # No card to cast one shadow for — instead, each element casts its own,
    # traced straight from its own silhouette (the content layer's alpha)
    # rather than a separate rounded-rect shape. Drawn on a larger, otherwise
    # -empty canvas so the blur has room instead of being clipped at the edge.
    margin = int(H * 0.12)
    drop = int(H * 0.045)
    silhouette = content.getchannel('A').point(lambda v: int(v * 0.5))
    shadow_layer = Image.new('RGBA', (W, H), SHADOW[:3] + (0,))
    shadow_layer.putalpha(silhouette)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(H * 0.025))

    FW, FH = W + margin * 2, H + margin * 2 + drop
    final = Image.new('RGBA', (FW, FH), (0, 0, 0, 0))
    final.alpha_composite(shadow_layer, (margin, margin + drop))
    final.alpha_composite(content, (margin, margin))

    return final.resize((int(width), max(1, round(width * FH / FW))), Image.LANCZOS)


def main():
    label = 'SUBSCRIBED'
    if '--text' in sys.argv:
        label = sys.argv[sys.argv.index('--text') + 1]
    if '--label' in sys.argv:
        label = sys.argv[sys.argv.index('--label') + 1]

    # Next to the video, not next to this script — the artwork is something you
    # keep and edit, so it belongs in the folder you work in.
    folder = next((a for a in sys.argv[1:] if not a.startswith('--')
                   and os.path.isdir(a)), os.getcwd())
    png = os.path.join(folder, 'sticker.png')
    draw(640, label).save(png)
    print(f"  wrote {png}")

    # A slow breath, so it draws the eye without being obnoxious.
    # 2 seconds at 25fps: bell rings, thumb bobs, gloss sweeps, then it rests.
    frames, steps = [], 50
    for i in range(steps):
        phase = i / steps
        pulse = max(0.0, math.sin(phase * math.pi * 2)) ** 2 * 0.6
        frames.append(draw(640, label, pulse, phase, shadow=False).convert('RGBA'))
    gif = os.path.join(folder, 'sticker.gif')
    frames[0].save(gif, save_all=True, append_images=frames[1:],
                   duration=40, loop=0, disposal=2, transparency=0, optimize=True)
    print(f"  wrote {gif}")
    print("\n  Put either next to the video. The .png is used if both exist —")
    print("  delete sticker.png to use the animated one.")


if __name__ == '__main__':
    main()