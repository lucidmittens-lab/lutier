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


def f_hole_group(cx, cy, flip=1, scale=1.0):
    s = scale
    path = f"M 0,{-170*s} C {55*s},{-170*s} {55*s},{-55*s} 0,0 C {-55*s},{55*s} {-55*s},{170*s} 0,{170*s}"
    wing1 = f'<line x1="{34*s}" y1="{-95*s}" x2="{78*s}" y2="{-118*s}"/>'
    wing2 = f'<line x1="{-34*s}" y1="{95*s}" x2="{-78*s}" y2="{118*s}"/>'
    eyes = f'<circle cx="0" cy="{-178*s}" r="{19*s}"/><circle cx="0" cy="{178*s}" r="{19*s}"/>'
    # Soft dark halo behind the f-hole so it stays legible against the
    # busy mosaic instead of competing with it for attention.
    halo = f'<ellipse cx="0" cy="0" rx="{110*s}" ry="{230*s}" fill="#0a0a0c" opacity="0.55"/>'
    return f"""
    <g transform="translate({cx},{cy}) scale({flip},1)">
      {halo}
      <path d="{path}" fill="none" stroke="#0c0d10" stroke-width="{30*s}" stroke-linecap="round"/>
      <path d="{path}" fill="none" stroke="#f7f1e3" stroke-width="{20*s}" stroke-linecap="round"/>
      <g stroke="#0c0d10" stroke-width="{14*s}" stroke-linecap="round">{wing1}{wing2}</g>
      <g stroke="#f7f1e3" stroke-width="{8*s}" stroke-linecap="round">{wing1}{wing2}</g>
      <g fill="#f7f1e3" stroke="#0c0d10" stroke-width="{6*s}">{eyes}</g>
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
      <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#000000" flood-opacity="0.45"/>
    </filter>
  </defs>

  <g clip-path="url(#bgClip)">
    <rect x="0" y="0" width="{SIZE}" height="{SIZE}" fill="url(#bgGrad)"/>
    <g transform="translate({SIZE/2},{SIZE/2}) rotate(-24) translate({-ribbon_w/2},{-ribbon_h/2})" filter="url(#softShadow)">
      {ribbon_svg}
    </g>
  </g>

  <g filter="url(#softShadow)">
    {f_hole_group(SIZE/2 - 165, SIZE/2, flip=1, scale=1.55)}
    {f_hole_group(SIZE/2 + 165, SIZE/2, flip=-1, scale=1.55)}
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
