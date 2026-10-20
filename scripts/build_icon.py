"""Render SeekWords icon without a GUI; requires Pillow only for asset creation."""
from pathlib import Path
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1] / "assets"
S = 1024
f = lambda value: round(value * S / 512)
image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gradient = Image.new("RGBA", (S, S))
pixels = gradient.load()
for y in range(S):
    for x in range(S):
        t = min(1, (x + y) / (2 * S))
        pixels[x, y] = (round(23 + 48*t), round(41 + 75*t), round(81 + 151*t), 255)
mask = Image.new("L", (S, S))
md = ImageDraw.Draw(mask)
md.rounded_rectangle((f(12), f(12), f(500), f(500)), radius=f(112), fill=255)
image.paste(gradient, (0, 0), mask)
d = ImageDraw.Draw(image)
# Document with a folded upper-right corner.
d.rounded_rectangle((f(67), f(92), f(406), f(424)), radius=f(40), fill="#f8fbff")
d.polygon([(f(315), f(92)), (f(315), f(160)), (f(338), f(183)), (f(406), f(183))], fill="#dce9ff")
for yy, length in ((188, 151), (231, 139), (274, 92), (317, 91)):
    d.line((f(119), f(yy), f(119+length), f(yy)), fill="#a9bddb", width=f(19), joint="curve")
    for xx in (119, 119+length):
        d.ellipse((f(xx-9), f(yy-9), f(xx+9), f(yy+9)), fill="#a9bddb")
d.line((f(371), f(363), f(435), f(427)), fill="#45d3c9", width=f(37))
d.ellipse((f(232), f(224), f(398), f(390)), fill="#e7fffc", outline="#45d3c9", width=f(25))
d.line((f(282), f(308), f(304), f(330), f(347), f(282)), fill="#1e6e89", width=f(16), joint="curve")
image.save(ROOT / "seekwords.png", optimize=True)
image.save(ROOT / "seekwords.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
image.save(ROOT / "seekwords.icns", format="ICNS")
print("Created seekwords.png, seekwords.ico and seekwords.icns")
