#!/usr/bin/env python3
"""Write public/mascots.html: three original 16x16 pixel-art mascot concepts for the owner to pick from.
Nothing on the site uses them yet; the favicon stays as it is until one is chosen.

    python3 tools/mascots.py
"""

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "mascots.html"

K = "#1b1f29"  # outline
COMMON = {"K": K, "W": "#ffffff", "P": "#ff8fa3", "G": "#29c45a"}


def mirror(halves):
    return [h + h[::-1] for h in halves]


def chipper():
    rows = mirror([
        "........",
        "........",
        "...KKKKK",
        ".SSKcccK",
        "...KCCCC",
        ".SSKCWWC",
        "...KCWKC",
        ".SSKCCCC",
        "...KPCKC",
        ".SSKCCCK",
        "...KCCCC",
        ".SSKCCCC",
        "...KKKKK",
        "....SS..",
        "....KK..",
        "........",
    ])
    rows[4] = rows[4][:4] + "G" + rows[4][5:]  # power LED: lights up green for every matched function
    return rows, {"C": "#665a96", "c": "#8f84c2", "S": "#c9ced8"}


def pip():
    rows = mirror([
        "......KK",
        ".....KgG",
        ".....KGG",
        "..KKKKGG",
        "..KgGGGG",
        "..KGGGGG",
        ".KKGWWGG",
        "KgGGWKGG",
        "KGGGGGGG",
        "KGGPGGKG",
        ".KKGGGGK",
        "..KGGGGG",
        "..KGGGKK",
        "..KGGK..",
        "..KKKK..",
        "........",
    ])
    return rows, {"G": "#29c45a", "g": "#8be6a8"}


def nibble():
    rows = [
        "................",
        ".......GG.......",
        "........K.......",
        ".....KKKKKK.....",
        "...KKbbBBBBKK...",
        "..KbbBBBBBBBBK..",
        "..KbBKKKBKKBBK..",
        ".KBBBKWKBBKBBBK.",
        ".KBBBKKKBBKBBBK.",
        ".KBPBBBKKBBBPBK.",
        ".KBBBBBBBBBBBBK.",
        ".KBDBDBDBDBDBDK.",
        ".KDBDBDBDBDBDBK.",
        ".KDDDDDDDDDDDDK.",
        "..KKKKKKKKKKKK..",
        "................",
    ]
    return rows, {"B": "#3d8bfd", "b": "#9cc4ff", "D": "#24508f"}


MASCOTS = [
    ("Chipper", chipper,
     "A little ROM chip being turned into readable source. Its power LED glows green for every function that matches."),
    ("Pip", pip,
     "A puzzle piece that only clicks into place when it fits exactly: byte for byte, like a matching decompilation."),
    ("Nibble", nibble,
     "Half a byte of curiosity, with a 0 and a 1 for eyes. Its bottom is still raw binary (the dither) and fills in solid as code is decompiled."),
]


def svg(rows, colors, size, label):
    pal = {**COMMON, **colors}
    assert len(rows) == 16 and all(len(r) == 16 for r in rows), label
    rects = []
    for y, row in enumerate(rows):
        x = 0
        while x < 16:
            c = row[x]
            if c == ".":
                x += 1
                continue
            run = 1
            while x + run < 16 and row[x + run] == c:
                run += 1
            rects.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{pal[c]}"/>')
            x += run
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="{size}" height="{size}" '
            f'shape-rendering="crispEdges" role="img" aria-label="{escape(label)}">{"".join(rects)}</svg>')


def main():
    cards = []
    for name, fn, concept in MASCOTS:
        rows, colors = fn()
        panels = "".join(
            f'<div class="panel {theme}">'
            + "".join(f'<figure>{svg(rows, colors, s, f"{name} at {s} pixels")}<figcaption>{s} px</figcaption></figure>' for s in (16, 32, 64, 128))
            + f'<div class="tab"><span class="fav">{svg(rows, colors, 16, f"{name} as a favicon")}</span>decomp.fun</div></div>'
            for theme in ("light", "dark")
        )
        cards.append(f'<section><h2>{escape(name)}</h2><p>{escape(concept)}</p><div class="panels">{panels}</div></section>')
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Mascot concepts · decomp.fun</title><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
:root {{ color-scheme: light dark; --bg: #ffffff; --fg: #1b1f29; --muted: #5d6575; --line: #e3e6ec; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #181c25; --fg: #e3e6ec; --muted: #a3abb9; --line: #2a3140; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 2rem 1rem 3rem; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 960px; margin: 0 auto; }}
h1 {{ margin: 0 0 .25rem; font-size: 1.75rem; }}
h2 {{ margin: 2.5rem 0 .25rem; font-size: 1.3rem; }}
p {{ margin: 0 0 1rem; color: var(--muted); max-width: 46rem; }}
.panels {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); gap: 1rem; }}
.panel {{ display: flex; flex-wrap: wrap; align-items: flex-end; gap: 1.25rem; padding: 1.25rem; border-radius: .75rem; border: 1px solid var(--line); }}
.panel.light {{ background: #f4f5f8; color: #5d6575; }}
.panel.dark {{ background: #181c25; color: #a3abb9; border-color: #2a3140; }}
figure {{ margin: 0; text-align: center; font-size: .75rem; }}
figure svg {{ display: block; margin: 0 auto .35rem; image-rendering: pixelated; }}
.tab {{ display: inline-flex; align-items: center; gap: .5rem; padding: .35rem .75rem; border-radius: .5rem .5rem 0 0;
  font-size: .8rem; background: rgba(127, 127, 127, .15); }}
.fav svg {{ display: block; }}
a {{ color: inherit; }}
</style></head><body><main>
<h1>Mascot concepts</h1>
<p>Three original 16&times;16 pixel-art ideas for decomp.fun, shown at favicon, 32, 64 and 128 px on light and dark backgrounds.
None of them is used on the site yet; the current favicon stays until one is picked. <a href="/">Back to decomp.fun</a></p>
{"".join(cards)}
</main></body></html>
"""
    OUT.write_text(html)
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
