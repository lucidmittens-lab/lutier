"""Final asset generation for the chosen herringbone background texture.
Slightly higher-resolution tile than the preview (48px vs 32px, same
row_h/period ratio) as a reasonable middle ground for sharper edges on
high-DPI displays without building a full @2x asset pipeline -- flagged
as a known limitation in RUNNING.md rather than treated as solved."""
from PIL import Image, ImageDraw

GRAIN_GRAY = (128, 128, 128)
TILE = 48
row_h = 12
period = 12

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

tile.save("/sessions/compassionate-optimistic-brown/mnt/outputs/lut_tool/assets/bg_herringbone.png")
print("saved")
