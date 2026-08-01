"""Generate assets/app.ico and assets/app.png - a 'duplicate videos' icon."""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

SS = 4                                   # supersampling factor
S = 256 * SS
ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]


def lerp(a: tuple[int, int, int], b: tuple[int, int, int],
         t: float) -> tuple[int, ...]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Background tile: vertical blue -> indigo gradient, rounded corners.
    top, bottom = (59, 130, 246), (109, 92, 246)
    gradient = Image.new("RGB", (1, S))
    for y in range(S):
        gradient.putpixel((0, y), lerp(top, bottom, y / S))
    gradient = gradient.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=int(58 * SS), fill=255)
    img.paste(gradient, (0, 0), mask)

    # Soft shadow under the front card.
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [int(58 * SS), int(78 * SS), int(58 * SS) + int(140 * SS),
         int(78 * SS) + int(108 * SS)], radius=int(16 * SS),
        fill=(0, 0, 0, 90))
    img = Image.alpha_composite(
        img, shadow.filter(ImageFilter.GaussianBlur(7 * SS)))

    draw = ImageDraw.Draw(img)

    # Back card, offset to imply a duplicate stack.
    draw.rounded_rectangle(
        [int(92 * SS), int(52 * SS), int(92 * SS) + int(132 * SS),
         int(52 * SS) + int(102 * SS)], radius=int(14 * SS),
        fill=(255, 255, 255, 120))

    # Front card - a video frame (16:9-ish), with a play triangle.
    x0, y0 = int(54 * SS), int(76 * SS)
    x1, y1 = x0 + int(140 * SS), y0 + int(108 * SS)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=int(14 * SS),
                           fill=(23, 26, 32, 255))

    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    r = int(34 * SS)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 235))
    tri = int(18 * SS)
    draw.polygon([
        (cx - tri * 0.45, cy - tri * 0.72),
        (cx - tri * 0.45, cy + tri * 0.72),
        (cx + tri * 0.85, cy),
    ], fill=(59, 130, 246))

    # Film-strip perforations along the top and bottom of the front card.
    hole = int(6 * SS)
    for fx in range(x0 + int(14 * SS), x1 - int(10 * SS), int(20 * SS)):
        draw.rounded_rectangle(
            [fx, y0 + int(6 * SS), fx + hole, y0 + int(6 * SS) + hole],
            radius=int(2 * SS), fill=(255, 255, 255, 200))
        draw.rounded_rectangle(
            [fx, y1 - int(6 * SS) - hole, fx + hole, y1 - int(6 * SS)],
            radius=int(2 * SS), fill=(255, 255, 255, 200))

    return img.resize((256, 256), Image.Resampling.LANCZOS)


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    icon = build()
    ico_path = os.path.join(ASSETS, "app.ico")
    png_path = os.path.join(ASSETS, "app.png")
    icon.save(ico_path, sizes=ICO_SIZES)
    icon.save(png_path)
    print(f"wrote {ico_path} ({os.path.getsize(ico_path)} bytes)")
    print(f"wrote {png_path} ({os.path.getsize(png_path)} bytes)")


if __name__ == "__main__":
    main()
