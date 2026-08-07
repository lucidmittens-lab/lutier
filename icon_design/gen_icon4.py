import cairosvg

SIZE = 1024

def hald_colors(cube=8):
    colors = []
    for b in range(cube):
        for g in range(cube):
            for r in range(cube):
                colors.append((r / (cube - 1), g / (cube - 1), b / (cube - 1)))
    return colors

def build_ribbon_svg(cols=32, rows=8, block=40, gap=2):
    colors = hald_colors(cube=8)
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


def leg_path(length=380, width_top=54, bow=34):
    """A single tapered divider/caliper leg -- a solid bowed blade
    running straight down the local Y axis from the hinge (0,0) to a
    sharp point at (0,length). Rotated into place per-leg by the caller.
    Solid filled shape (not thin double-strokes) specifically so it stays
    bold and legible even shrunk to icon sizes, unlike the earlier
    thin-stroke f-hole attempt."""
    w = width_top / 2
    return (
        f"M {w},0 "
        f"C {w+bow},{length*0.32} {width_top*0.22},{length*0.78} 0,{length} "
        f"C {-width_top*0.22},{length*0.78} {-(w+bow)},{length*0.32} {-w},0 "
        f"Z"
    )


def caliper_group(cx, cy, scale=1.0, spread_deg=34, length=380):
    s = scale
    left_leg = leg_path(length=length*s, width_top=54*s, bow=30*s)
    right_leg = leg_path(length=length*s, width_top=54*s, bow=30*s)

    hinge_r = 46 * s
    crossbar_y = 150 * s
    crossbar_half_w = 95 * s
    screw_r = 20 * s

    return f"""
    <g transform="translate({cx},{cy})">
      <!-- legs -->
      <g fill="#f7f1e3" stroke="#0c0d10" stroke-width="{10*s}">
        <g transform="rotate({-spread_deg})"><path d="{left_leg}"/></g>
        <g transform="rotate({spread_deg})"><path d="{right_leg}"/></g>
      </g>

      <!-- cross brace linking the two legs, classic divider detail -->
      <g stroke="#0c0d10" stroke-width="{16*s}" stroke-linecap="round">
        <line x1="{-crossbar_half_w}" y1="{crossbar_y}" x2="{crossbar_half_w}" y2="{crossbar_y}"/>
      </g>
      <g stroke="#f7f1e3" stroke-width="{9*s}" stroke-linecap="round">
        <line x1="{-crossbar_half_w}" y1="{crossbar_y}" x2="{crossbar_half_w}" y2="{crossbar_y}"/>
      </g>
      <circle cx="0" cy="{crossbar_y}" r="{screw_r}" fill="#f7f1e3" stroke="#0c0d10" stroke-width="{6*s}"/>

      <!-- hinge -->
      <circle cx="0" cy="0" r="{hinge_r}" fill="#f7f1e3" stroke="#0c0d10" stroke-width="{9*s}"/>
      <circle cx="0" cy="0" r="{hinge_r*0.4}" fill="#0c0d10"/>
    </g>
    """


def build_svg():
    ribbon_svg, ribbon_w, ribbon_h = build_ribbon_svg(cols=32, rows=8, block=40, gap=2)
    bg_radius = 200

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
      <feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#000000" flood-opacity="0.5"/>
    </filter>
  </defs>

  <g clip-path="url(#bgClip)">
    <rect x="0" y="0" width="{SIZE}" height="{SIZE}" fill="url(#bgGrad)"/>
    <g transform="translate({SIZE/2},{SIZE/2}) rotate(-24) translate({-ribbon_w/2},{-ribbon_h/2})" filter="url(#softShadow)">
      {ribbon_svg}
    </g>
  </g>

  <g filter="url(#softShadow)">
    {caliper_group(SIZE/2, SIZE/2 - 30, scale=1.15, spread_deg=32, length=380)}
  </g>

  <rect x="6" y="6" width="{SIZE-12}" height="{SIZE-12}" rx="{bg_radius-6}" ry="{bg_radius-6}"
        fill="none" stroke="#000000" stroke-opacity="0.35" stroke-width="4"/>
</svg>
"""
    return svg


if __name__ == "__main__":
    svg_str = build_svg()
    out_dir = "/sessions/amazing-compassionate-archimedes/mnt/outputs/lut_tool/icon_design"
    with open(f"{out_dir}/icon.svg", "w") as f:
        f.write(svg_str)
    cairosvg.svg2png(bytestring=svg_str.encode(), write_to=f"{out_dir}/icon_1024.png", output_width=1024, output_height=1024)
    print("done")
