"""
Generates preview swatches for candidate empty-space background textures.

Each candidate is rendered twice side-by-side (light bg / dark bg) in one
image so it's obvious the pattern reads in both themes without needing
separate light/dark assets -- same "neutral gray, alpha-blended" trick
used throughout: pure mid-gray at low opacity darkens against a light
background and lightens against a dark one, automatically, no palette
detection needed at generation time.

Also emits a small SEAMLESS tile (the thing that would actually ship in
style.py via background-image/background-repeat) for each candidate,
separately from the big preview swatch.
"""
import numpy as np
from PIL import Image, ImageDraw

LIGHT_BG = (229, 229, 229, 255)   # ~palette(window) in a light theme
DARK_BG = (32, 32, 32, 255)       # ~palette(window) in a dark theme
GRAIN_GRAY = (128, 128, 128)      # neutral -- blends toward "darker" on light bg, "lighter" on dark bg

TILE = 32          # seamless tile size in px
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


# 1. Film grain -- fits a color-grading tool thematically. Sparse random
# specks, low alpha, no two specks the same alpha (avoids an obvious
# repeat-grid look even though the tile itself does repeat).
def make_grain():
    rng = np.random.default_rng(7)
    tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    px = tile.load()
    for y in range(TILE):
        for x in range(TILE):
            if rng.random() < 0.22:
                a = int(rng.uniform(8, 26))
                px[x, y] = (*GRAIN_GRAY, a)
    make_preview("grain", tile)


# 2. Fine dot grid -- small dots on a regular grid, halftone-ish.
def make_dots():
    tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    spacing = 8
    for y in range(0, TILE, spacing):
        for x in range(0, TILE, spacing):
            draw.ellipse([x, y, x + 1, y + 1], fill=(*GRAIN_GRAY, 40))
    make_preview("dots", tile)


# 3. Diagonal hairlines -- 45-degree 1px lines, wide spacing so it reads
# as texture, not as a busy pattern.
def make_diagonal():
    tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    for offset in range(-TILE, TILE * 2, 10):
        draw.line([(offset, 0), (offset + TILE, TILE)], fill=(*GRAIN_GRAY, 22), width=1)
    make_preview("diagonal", tile)


# 4. Fine crosshatch / graph-paper grid.
def make_grid():
    tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    spacing = 16
    for x in range(0, TILE, spacing):
        draw.line([(x, 0), (x, TILE)], fill=(*GRAIN_GRAY, 18), width=1)
    for y in range(0, TILE, spacing):
        draw.line([(0, y), (TILE, y)], fill=(*GRAIN_GRAY, 18), width=1)
    make_preview("grid", tile)


make_grain()
make_dots()
make_diagonal()
make_grid()
print("done")
