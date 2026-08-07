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


def right_leg_path():
    """One leg of a spring-joint (outside) caliper -- same silhouette shape
    as v5 (bows outward, hooks back inward at the tip) but every x-coordinate
    scaled ~0.68x toward the hinge, so the leg reads as a leaner, less bulky
    shape while keeping the same overall height/reach."""
    return (
        "M 10,-170 "
        "C 61,-150 102,-40 101,60 "
        "C 99,150 68,215 37,248 "
        "C 27,258 15,252 16,236 "
        "C 30,220 53,180 56,120 "
        "C 58,50 41,-10 14,-40 "
        "C 4,-50 3,-70 5,-90 "
        "Z"
    )


def caliper_group(cx, cy, scale=1.0):
    s = scale
    right_leg = right_leg_path()

    # Smaller collar and lighter strokes than v5 -- less chonky, and no
    # inner highlight rect / knurl ticks / double-line thumbscrew thread,
    # since the ask was silhouette-first, details-later.
    collar_w, collar_h = 78 * s, 60 * s
    collar_y = -330 * s
    sw = 7 * s  # stroke width, down from 9

    return f"""
    <g transform="translate({cx},{cy}) scale({s},{s})">
      <!-- top collar (sleeve the two legs pivot inside) -->
      <g fill="#f7f1e3" stroke="#0c0d10" stroke-width="{sw}">
        <rect x="{-collar_w/2}" y="{collar_y}" width="{collar_w}" height="{collar_h}" rx="12"/>
      </g>

      <!-- leaf-spring bow linking collar to the two legs -->
      <path d="M -12,{collar_y+collar_h-4} C -60,{collar_y+collar_h+36} -74,-190 -52,-155
               C -26,-182 26,-182 52,-155
               C 74,-190 60,{collar_y+collar_h+36} 12,{collar_y+collar_h-4} Z"
            fill="#f7f1e3" stroke="#0c0d10" stroke-width="{sw}"/>

      <!-- legs -->
      <g fill="#f7f1e3" stroke="#0c0d10" stroke-width="{sw}">
        <path d="{right_leg}"/>
        <g transform="scale(-1,1)"><path d="{right_leg}"/></g>
      </g>

      <!-- thumbscrew: a single rod crossing between the legs, exiting
           right into a plain adjustment ball; silhouette only, no
           knurling or thread detail. -->
      <g stroke="#0c0d10" stroke-width="10" stroke-linecap="round">
        <line x1="-40" y1="-25" x2="100" y2="-25"/>
      </g>
      <circle cx="110" cy="-25" r="17" fill="#f7f1e3" stroke="#0c0d10" stroke-width="{sw}"/>
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
    {caliper_group(SIZE/2, SIZE/2, scale=1.0)}
  </g>

  <rect x="6" y="6" width="{SIZE-12}" height="{SIZE-12}" rx="{bg_radius-6}" ry="{bg_radius-6}"
        fill="none" stroke="#000000" stroke-opacity="0.35" stroke-width="4"/>
</svg>
"""
    return svg


if __name__ == "__main__":
    svg_str = build_svg()
    import os
    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(f"{out_dir}/icon.svg", "w") as f:
        f.write(svg_str)
    cairosvg.svg2png(bytestring=svg_str.encode(), write_to=f"{out_dir}/icon_1024.png", output_width=1024, output_height=1024)
    print("done")
