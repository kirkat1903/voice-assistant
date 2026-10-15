"""Generates the Aiko logo: icon.png (256 px) and a multi-size icon.ico.

Run:  venv\\Scripts\\python.exe make_icon.py
Everything is drawn procedurally at high resolution and downsampled, so the logo
stays crisp at tray sizes (16-24 px) and on high-DPI screens.
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

OUT = Path(__file__).parent
ICO_SIZES = [16, 20, 24, 28, 32, 40, 48, 64, 96, 128, 256]


def circle_mask(size, radius, ss=4):
    big = Image.new("L", (size * ss, size * ss), 0)
    c = size * ss / 2
    ImageDraw.Draw(big).ellipse([c - radius * ss, c - radius * ss, c + radius * ss, c + radius * ss], fill=255)
    return big.resize((size, size), Image.LANCZOS)


def blob(size, center, radius, color, blur):
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx, cy = center
    ImageDraw.Draw(layer).ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color)
    return layer.filter(ImageFilter.GaussianBlur(blur))


def sparkle_mask(size, cx, cy, r, k=3.0, ss=2):
    """Four-point star (astroid-like); smaller k gives fatter arms."""
    big = Image.new("L", (size * ss, size * ss), 0)
    pts = []
    for i in range(360):
        t = 2 * math.pi * i / 360
        c, s = math.cos(t), math.sin(t)
        pts.append(((cx + r * math.copysign(abs(c) ** k, c)) * ss, (cy + r * math.copysign(abs(s) ** k, s)) * ss))
    ImageDraw.Draw(big).polygon(pts, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def vertical_gradient(size, top, bottom):
    grad = Image.linear_gradient("L").resize((size, size))
    return Image.composite(Image.new("RGBA", (size, size), bottom), Image.new("RGBA", (size, size), top), grad)


def render(size=1024, detailed=True):
    R = size * 0.47
    mid = size / 2

    # Mesh-gradient body: indigo base with violet / cyan / magenta / peach light pools.
    body = Image.new("RGBA", (size, size), (46, 34, 140, 255))
    pools = [
        ((0.28, 0.24), 0.42, (139, 92, 246, 255), 0.14),
        ((0.22, 0.82), 0.36, (45, 212, 245, 255), 0.14),
        ((0.82, 0.74), 0.38, (244, 100, 190, 255), 0.14),
        ((0.80, 0.22), 0.20, (255, 150, 190, 235), 0.11),
    ]
    for (px, py), pr, color, blur in pools:
        body = Image.alpha_composite(body, blob(size, (px * size, py * size), pr * size, color, blur * size))

    # Edge vignette for depth.
    vig = Image.radial_gradient("L").resize((size, size)).point(lambda v: int((v / 255) ** 3 * 80))
    body = Image.alpha_composite(body, Image.merge("RGBA", (*(Image.new("L", (size, size), 0),) * 3, vig)))

    if detailed:
        # Glass highlight on the upper half + a thin rim light that fades downwards.
        gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(gloss).ellipse([size * 0.16, size * 0.03, size * 0.84, size * 0.52], fill=(255, 255, 255, 62))
        body = Image.alpha_composite(body, gloss.filter(ImageFilter.GaussianBlur(size * 0.05)))

        rim = Image.new("L", (size, size), 0)
        ImageDraw.Draw(rim).ellipse([mid - R + 6, mid - R + 6, mid + R - 6, mid + R - 6], outline=255, width=max(2, size // 100))
        rim = rim.filter(ImageFilter.GaussianBlur(size * 0.002))
        fade = ImageOps.flip(Image.linear_gradient("L").resize((size, size)))
        rim = ImageChops.multiply(rim, fade).point(lambda v: int(v * 0.55))
        body = Image.alpha_composite(body, Image.merge("RGBA", (*(Image.new("L", (size, size), 255),) * 3, rim)))

    # Sparkle (the "AI spark") with soft shadow and glow.
    if detailed:
        stars = [(0.47, 0.54, 0.285, 3.0), (0.745, 0.285, 0.105, 3.0)]
    else:
        stars = [(0.50, 0.50, 0.34, 2.6)]
    spark = Image.new("L", (size, size), 0)
    for sx, sy, sr, k in stars:
        spark = ImageChops.lighter(spark, sparkle_mask(size, sx * size, sy * size, sr * size, k))

    shadow = spark.filter(ImageFilter.GaussianBlur(size * 0.025)).point(lambda v: int(v * 0.45))
    shadow = ImageChops.offset(shadow, 0, int(size * 0.02))
    body = Image.alpha_composite(body, Image.merge("RGBA", (*(Image.new("L", (size, size), 0),) * 3, shadow)))

    glow = spark.filter(ImageFilter.GaussianBlur(size * 0.04)).point(lambda v: int(v * 0.85))
    body = Image.alpha_composite(body, Image.merge("RGBA", (*(Image.new("L", (size, size), 255),) * 3, glow)))

    fill = vertical_gradient(size, (255, 255, 255, 255), (226, 230, 255, 255))
    fill.putalpha(spark)
    body = Image.alpha_composite(body, fill)

    body.putalpha(ImageChops.multiply(body.getchannel("A"), circle_mask(size, R)))
    return body


def main():
    master = render(1024, detailed=True)
    simple = render(512, detailed=False)

    frames = {}
    for s in ICO_SIZES:
        src = master if s > 32 else simple
        frames[s] = src.resize((s, s), Image.LANCZOS)

    master.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    master.resize((512, 512), Image.LANCZOS).save(OUT / "icon_512.png")
    frames[256].save(
        OUT / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[frames[s] for s in ICO_SIZES if s != 256],
    )
    print("saved icon.png, icon_512.png, icon.ico")


if __name__ == "__main__":
    main()
