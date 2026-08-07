"""Final asset for the grain texture on QMainWindow::separator (the
draggable dividers between docked panels), matched to the original
"grain" candidate Tom saw in preview_grain.png -- same density (22%)
and alpha range (8-26/255), same neutral gray. Kept at the original
32px tile size (unlike bg_herringbone's bumped-up 48px) since a
separator strip is only a few px thick -- there's no benefit to a
denser tile when the visible band is this narrow either way."""
import numpy as np
from PIL import Image

GRAIN_GRAY = (128, 128, 128)
TILE = 32

rng = np.random.default_rng(7)  # same seed as the original preview, for consistency
tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
px = tile.load()
for y in range(TILE):
    for x in range(TILE):
        if rng.random() < 0.22:
            a = int(rng.uniform(8, 26))
            px[x, y] = (*GRAIN_GRAY, a)

tile.save("/sessions/compassionate-optimistic-brown/mnt/outputs/lut_tool/assets/border_grain.png")
print("saved")
