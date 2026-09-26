"""
Generates the system tray icon for the Windows Taskbar.
Renders the colored 'Twin Rails' badge with Pillow -- directly embodying the
widget's signature 'two rails, not one' visual identity (upper quota track in
Claude Coral, lower time rail with red lockout tail, and the synchronized
now needle slicing through both).
"""

import os
from typing import Optional
from PIL import Image, ImageDraw
from .theme import (
    CLAUDE_CORAL, DANGER_RED, TEXT_SECONDARY,
    BG_CARD, BORDER_CARD
)

# Dark Fluent card background for the badge container
ICON_BG_COLOR = "#23211F"
OUTLINE_COLOR = (255, 255, 255, 45)


def render_tray_icon(size: int = 64) -> Image.Image:
    """
    Renders the fixed colored 'Twin Rails' icon for the Windows system tray.
    
    Portrays the widget's defining architecture:
    - Base: Rounded squircle badge in dark Fluent slate.
    - Top Rail: Spent quota track filled with vibrant Claude Coral (#D97756).
    - Bottom Rail: Elapsed sprint time in gray (#A8A29E) with lockout red tail (#EF4444).
    - Needle: Crisp high-contrast white (#FFFFFF) needle spanning both tracks at the center.

    docs/logo.svg is a hand-kept vector copy of this same 64-unit geometry,
    used by the README because this Pillow render has no anti-aliasing and
    looked jagged when the browser scaled it. Change both together.
    """
    base_size = 256
    scale = base_size / 64.0
    img = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Base Squircle Badge
    p = round(2 * scale)
    box = [p, p, base_size - p, base_size - p]
    draw.rounded_rectangle(
        box, radius=round(16 * scale), fill=ICON_BG_COLOR,
        outline=OUTLINE_COLOR, width=round(2 * scale)
    )

    # 2. Top Quota Rail (Claude Coral fill; the empty half is hollow)
    # The empty half is left unpainted, so the tile shows through, and only
    # the outline marks it -- how the bars show "empty" too. It used to be a
    # #1E1E1E fill in a 1-unit #57514C outline: the fill matched the tile to
    # within 1.08:1 and the outline was half a pixel wide at the flyout
    # header's 40px, so on dark backgrounds the empty half vanished. The
    # outline is now 1.5 units in #736E6B (the bars' own recipe: secondary
    # text 40% of the way to the tile, 2.7:1 against it). Compared on
    # 2026-09-26 at 20/32/40px on dark and light backgrounds and rejected:
    # a 2-unit outline (heavy at 40px); a 12% white tint, which on a fixed
    # tile is just a grey slab that reads as filled; and a hole through the
    # tile, which shows a light taskbar as a bright, "full" bar.
    top_box = [round(10 * scale), round(17 * scale), round(54 * scale), round(31 * scale)]
    top_mask = Image.new("L", (base_size, base_size), 0)
    tm_draw = ImageDraw.Draw(top_mask)
    tm_draw.rounded_rectangle(top_box, radius=round(5 * scale), fill=255)

    top_layer = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))
    t_draw = ImageDraw.Draw(top_layer)
    t_draw.rectangle([top_box[0], top_box[1], round(32 * scale), top_box[3]], fill=CLAUDE_CORAL)
    img.paste(Image.alpha_composite(img, top_layer), (0, 0), top_mask)
    draw.rounded_rectangle(top_box, radius=round(5 * scale), outline="#736E6B", width=round(1.5 * scale))

    # 3. Bottom Time Rail (Track + Elapsed Gray + Danger Red Tail)
    bot_box = [round(10 * scale), round(37 * scale), round(54 * scale), round(44 * scale)]
    bot_mask = Image.new("L", (base_size, base_size), 0)
    bm_draw = ImageDraw.Draw(bot_mask)
    bm_draw.rounded_rectangle(bot_box, radius=round(3 * scale), fill=255)

    bot_layer = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))
    b_draw = ImageDraw.Draw(bot_layer)
    b_draw.rectangle(bot_box, fill="#383533")
    b_draw.rectangle([bot_box[0], bot_box[1], round(32 * scale), bot_box[3]], fill=TEXT_SECONDARY)
    b_draw.rectangle([round(45 * scale), bot_box[1], bot_box[2], bot_box[3]], fill=DANGER_RED)
    img.paste(bot_layer, (0, 0), bot_mask)

    # 4. Synchronized White Needle (Now)
    draw.line(
        [(round(32 * scale), round(11 * scale)), (round(32 * scale), round(50 * scale))],
        fill="#FFFFFF", width=round(4 * scale)
    )

    if size != base_size:
        return img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def save_multi_size_ico(filepath: str) -> None:
    """
    Exports a multi-resolution Windows .ico file with all standard system icon sizes.
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    base = render_tray_icon(256)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(filepath, format="ICO", sizes=sizes)

