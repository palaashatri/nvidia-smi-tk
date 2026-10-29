#!/usr/bin/env python3
"""Export platform raster assets from the checked-in SVG sources."""
from pathlib import Path
import io
try:
    import cairosvg
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Install dependencies: python -m pip install cairosvg Pillow") from exc
HERE = Path(__file__).resolve().parent
OUT = HERE / "generated"
OUT.mkdir(exist_ok=True)
SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
def png_from_svg(file, size):
    return cairosvg.svg2png(url=str(HERE / file), output_width=size, output_height=size)
for source, stem in (("app-icon.svg", "app-icon"), ("app-icon-light.svg", "app-icon-light"), ("app-icon-dark.svg", "app-icon-dark"), ("app-icon-mono.svg", "app-icon-mono")):
    for size in SIZES:
        (OUT / f"{stem}-{size}.png").write_bytes(png_from_svg(source, size))
images = [Image.open(io.BytesIO(png_from_svg("app-icon.svg", s))).convert("RGBA") for s in (16, 24, 32, 48, 64, 128, 256)]
images[-1].save(OUT / "app-icon.ico", format="ICO", append_images=images[:-1], sizes=[(i.width, i.height) for i in images])
(OUT / "social-preview.png").write_bytes(cairosvg.svg2png(url=str(HERE / "social-preview.svg"), output_width=1200, output_height=630))
print("Wrote", len(list(OUT.glob("*"))), "assets to", OUT)
