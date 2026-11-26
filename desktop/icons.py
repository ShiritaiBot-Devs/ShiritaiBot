import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

source, output = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)
icon = Image.open(source).convert("RGBA")
icon.save(output / "icon.png")
icon.save(output / "windows.png")

size = 1024
inset = 88
art = ImageOps.fit(icon, (size - inset * 2, size - inset * 2), Image.Resampling.LANCZOS)
mask = Image.new("L", art.size)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, art.width - 1, art.height - 1), 188, fill=255)
art.putalpha(ImageChops.multiply(art.getchannel("A"), mask))
canvas = Image.new("RGBA", (size, size))
canvas.paste((30, 44, 70, 95), (inset, inset + 14), mask)
canvas = canvas.filter(ImageFilter.GaussianBlur(18))
canvas.alpha_composite(art, (inset, inset))
canvas.save(output / "mac.png")
