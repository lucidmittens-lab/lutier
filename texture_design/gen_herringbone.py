"""
Herringbone candidate, added on request alongside the original four
(grain/dots/diagonal/grid) -- same rendering approach: neutral gray,
low alpha, previewed against both a light and a dark swatch in one
image so it's obvious no separate light/dark asset is needed.

Herringbone construction: rows of short diagonal strokes, slope
alternating row-to-row (down-right, then down-left, ...), with
alternating rows offset by half a period so one row's stroke ends
roughly where the next row's begins -- that continuous hand-off is what
reads as a "V/zigzag" herringbone weave rather than an unrelated stack
of diagonal hatching.
"""
from PIL import Image, ImageDraw

LIGHT_BG = (229, 229, 229, 255)
DARK_BG = (32, 32, 32, 255)
GRAIN_GRAY = (128, 128, 128)

TILE = 32
PREVIEW_W, PREVIEW_H = 600, 320
SWATCH_W = PREVIEW_W // 2


def tile_onto(canvas, tile_img, offset_x=0):
    tw, th = tile_img.size
    for y in range(0, canvas.height, th):
        for x in range(offset_x, offset_x + SWATCH_W, tw):
            canvas.alpha_composite(tile_img, (x, y))


def make_preview(name, tile_img):
    canvas = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), (0, 0, 0, 0))
    left = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), LIGHT_BG)
    right = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), DARK_BG)
    canvas.alpha_composite(left, (0, 0))
    canvas.alpha_composite(right.crop((SWATCH_W, 0, PREVIEW_W, PREVIEW_H)), (SWATCH_W, 0))
    tile_onto(canvas, tile_img, 0)
    draw = ImageDraw.Draw(canvas)
    draw.line([(SWATCH_W, 0), (SWATCH_W, PREVIEW_H)], fill=(0, 0, 0, 60), width=1)
    canvas.save(f"/sessions/compassionate-optimistic-brown/mnt/outputs/lut_tool/texture_design/preview_{name}.png")
    tile_img.save(f"/sessions/compassionate-optimistic-brown/mnt/outputs/lut_tool/texture_design/tile_{name}.png")


def make_herringbone():
    row_h = 8
    period = 8
    tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    rows = TILE // row_h
    for r in range(rows + 1):
        y0 = r * row_h
        y1 = y0 + row_h
        down_right = (r % 2 == 0)
        shift = 0 if down_right else period // 2
        x = -period + shift
        while x <= TILE + period:
            if down_right:
                draw.line([(x, y0), (x + row_h, y1)], fill=(*GRAIN_GRAY, 34), width=1)
            else:
                draw.line([(x + row_h, y0), (x, y1)], fill=(*GRAIN_GRAY, 34), width=1)
            x += period
    make_preview("herringbone", tile)


make_herringbone()
print("done")
