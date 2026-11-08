"""Draws the installer's wizard images in the project website's colors (navy, mint accent, the app icon).
Run with any Python that has Pillow: python installer/make_images.py  ->  installer/images/*.bmp"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "..", "tools", "windows", "localagentbrowser.ico")
BG, BG2, MINT, GLOW = (11, 17, 27), (14, 22, 36), (95, 211, 166), (30, 58, 90)


def panel(w, h):
    """The welcome page's side panel: navy with the website's corner glow, the icon, and a mint line."""
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    for r in range(int(w * 1.2), 0, -max(1, w // 60)):  # Radial glow from the top-right corner, as on the site
        t = r / (w * 1.2)
        c = tuple(int(BG[i] + (GLOW[i] - BG[i]) * (1 - t) * 0.9) for i in range(3))
        d.ellipse((w - r, -r, w + r, r), fill=c)
    icon = Image.open(ICON).convert("RGBA")
    size = int(w * 0.62)
    icon = icon.resize((size, size), Image.LANCZOS)
    img.paste(icon, ((w - size) // 2, int(h * 0.30)), icon)
    d.rectangle((int(w * 0.2), int(h * 0.30) + size + int(h * 0.05), int(w * 0.8),
                 int(h * 0.30) + size + int(h * 0.05) + max(2, h // 160)), fill=MINT)
    return img


def small(s):
    img = Image.new("RGB", (s, s), BG2)
    icon = Image.open(ICON).convert("RGBA").resize((int(s * 0.86), int(s * 0.86)), Image.LANCZOS)
    img.paste(icon, ((s - icon.width) // 2, (s - icon.height) // 2), icon)
    return img


if __name__ == "__main__":
    out = os.path.join(HERE, "images")
    os.makedirs(out, exist_ok=True)
    # Inno Setup picks the best size for the screen's DPI from these (100%, 150%, 200%)
    for scale, (w, h) in ((100, (164, 314)), (150, (246, 471)), (200, (328, 628))):
        panel(w, h).save(os.path.join(out, f"wizard-{scale}.bmp"))
    for scale, s in ((100, 55), (150, 83), (200, 110)):
        small(s).save(os.path.join(out, f"small-{scale}.bmp"))
    print("written to", out)
