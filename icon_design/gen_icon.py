"""
Generate the LUTier app icon: a HALD-CLUT-inspired diagonal mosaic ribbon
(the colorful mosaic-swatch look colorists recognize from real Hald CLUT
files) crossing a dark rounded-square background, with a pair of violin
f-holes overlaid -- LUTier = LUT + luthier (violin maker) pun.
"""
import colorsys
import math
import cairosvg

SIZE = 1024

def hald_colors(cube=8):
    """Flatten an identity cube's RGB values in raster order (blue slowest,
    green next, red fastest) -- the same raster order a real Hald CLUT
    image is built from, so the resulting color sequence has that
    authentic repeating-band character instead of being an arbitrary
    rainbow gradient."""
    colors = []
    for b in range(cube):
        for g in range(cube):
            for r in range(cube):
                colors.append((r / (cube - 1), g / (cube - 1), b / (cube - 1)))
    return colors


def build_ribbon_svg(cols=32, rows=8, block=40, gap=2):
    colors = hald_colors(cube=8)  # 512 entries = 32*16, we'll use first cols*rows
    assert len(colors) >= cols * rows
    rects = []
    for i in range(cols * rows):
        col = i % cols
        row = i // cols
        r, g, b = colors[i]
        hexcolor = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        x = col * (block + gap)
        y = row * (block + gap)
        rects.append(f'<rect x="{x}" y="{y}" width="{block}" height="{block}" fill="{hexcolor}"/>')
    ribbon_w = cols * (block + gap) - gap
    ribbon_h = rows * (block + gap) - gap
    return "\n".join(rects), ribbon_w, ribbon_h


def f_hole_path(scale=1.0):
    """A stylized violin f-hole as a cubic-bezier path, drawn tall and
    narrow along the Y axis, centered at local origin. Built from
    scratch as a simplified/iconic interpretation (elongated S-curve
    body + two circular eyes + small side notches), not a literal
    instrument-maker's template -- optimized to read clearly at icon
    sizes down to 16px, which a fully authentic f-hole silhouette would
    not."""
    s = scale
    return f"""
    M {0*s},{-190*s}
    C {34*s},{-188*s} {40*s},{-150*s} {22*s},{-120*s}
    C {6*s},{-95*s} {-14*s},{-80*s} {-8*s},{-50*s}
    C {-2*s},{-20*s} {18*s},{-8*s} {16*s},{18*s}
    C {14*s},{45*s} {-10*s},{55*s} {-22*s},{85*s}
    C {-34*s},{115*s} {-30*s},{150*s} {-2*s},{190*s}
    C {-18*s},{188*s} {-34*s},{170*s} {-30*s},{140*s}
    C {-26*s},{112*s} {-2*s},{100*s} {8*s},{75*s}
    C {18*s},{50*s} {0*s},{35*s} {-4*s},{10*s}
    C {-8*s},{-15*s} {14*s},{-30*s} {24*s},{-58*s}
    C {34*s},{-88*s} {24*s},{-120*s} {-4*s},{-140*s}
    C {-16*s},{-148*s} {-18*s},{-166*s} {0*s},{-190*s}
    Z
    """


def eye_circles(scale=1.0, offset_y=150, r=13):
    s = scale
    return (
        f'<circle cx="{18*s}" cy="{-offset_y*s}" r="{r*s}"/>'
        f'<circle cx="{-18*s}" cy="{offset_y*s}" r="{r*s}"/>'
    )


def build_svg():
    ribbon_svg, ribbon_w, ribbon_h = build_ribbon_svg(cols=32, rows=8, block=40, gap=2)

    bg_radius = 200
    f_scale = 1.05

    svg = f"""<svg width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <clipPath id="bgClip">
      <rect x="0" y="0" width="{SIZE}" height="{SIZE}" rx="{bg_radius}" ry="{bg_radius}"/>
    </clipPath>
    <linearGradient id="bgGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#20222a"/>
      <stop offset="100%" stop-color="#121318"/>
    </linearGradient>
    <filter id="softShadow" x="-50%" y="-50%" width="200%" height="200%">
      <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#000000" flood-opacity="0.45"/>
    </filter>
  </defs>

  <g clip-path="url(#bgClip)">
    <rect x="0" y="0" width="{SIZE}" height="{SIZE}" fill="url(#bgGrad)"/>

    <!-- HALD mosaic ribbon, rotated diagonally, bleeding off the edges -->
    <g transform="translate({SIZE/2},{SIZE/2}) rotate(-24) translate({-ribbon_w/2},{-ribbon_h/2})" filter="url(#softShadow)">
      {ribbon_svg}
    </g>

    <!-- subtle vignette to seat the ribbon against the dark bg -->
    <rect x="0" y="0" width="{SIZE}" height="{SIZE}" fill="url(#vignette)" opacity="0.0"/>
  </g>

  <!-- f-holes (LUTier = LUT + luthier), centered, flanking the middle -->
  <g fill="#f2e9d8" stroke="#0c0d10" stroke-width="6" filter="url(#softShadow)">
    <g transform="translate({SIZE/2 - 150},{SIZE/2})">
      <path d="{f_hole_path(f_scale)}"/>
      {eye_circles(f_scale)}
    </g>
    <g transform="translate({SIZE/2 + 150},{SIZE/2}) scale(-1,1)">
      <path d="{f_hole_path(f_scale)}"/>
      {eye_circles(f_scale)}
    </g>
  </g>

  <!-- outer rounded-square border for definition -->
  <rect x="6" y="6" width="{SIZE-12}" height="{SIZE-12}" rx="{bg_radius-6}" ry="{bg_radius-6}"
        fill="none" stroke="#000000" stroke-opacity="0.35" stroke-width="4"/>
</svg>
"""
    return svg


if __name__ == "__main__":
    svg_str = build_svg()
    with open("/sessions/amazing-compassionate-archimedes/mnt/outputs/lut_tool/icon_design/icon.svg", "w") as f:
        f.write(svg_str)
    cairosvg.svg2png(bytestring=svg_str.encode(), write_to="/sessions/amazing-compassionate-archimedes/mnt/outputs/lut_tool/icon_design/icon_1024.png", output_width=1024, output_height=1024)
    print("done")
