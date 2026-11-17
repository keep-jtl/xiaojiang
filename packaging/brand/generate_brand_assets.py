# -*- coding: utf-8 -*-
"""小财通品牌资源生成（仅构建期使用，不进入运行时依赖）。

严格依据网页测试版 .logo-dot 的规格重新生成，不另做设计：
- 22×22 圆角方块，圆角 7px（7/22 ≈ 31.8%）
- 135° 线性渐变：#2f6fed → #5b8df8
- Android 图标/启动图使用奶油底 #F6F1E8（与界面 --bg 一致）

运行（Pillow/numpy 只装在构建环境，不写入 requirements.txt）：
    D:\\Projects\\XiaoLiCaiTong\\.venv\\Scripts\\python.exe packaging\\brand\\generate_brand_assets.py

输出：
- assets/brand/brand-1024.png
- assets/brand/xltc.ico（16/24/32/48/64/128/256）
- android/app/src/main/res/mipmap-*/ic_launcher.png、ic_launcher_round.png、ic_launcher_foreground.png
- android/app/src/main/res/drawable*/splash.png
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "android" / "app" / "src" / "main" / "res"
BRAND = ROOT / "assets" / "brand"

START = np.array([0x2F, 0x6F, 0xED], dtype=np.float64)   # #2f6fed
END = np.array([0x5B, 0x8D, 0xF8], dtype=np.float64)     # #5b8df8
CREAM = (0xF6, 0xF1, 0xE8, 0xFF)
RADIUS_RATIO = 7.0 / 22.0
MARK_RATIO = 66.0 / 108.0
SPLASH_MARK_RATIO = 0.26

MIPMAPS = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
FOREGROUNDS = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
SPLASHES = {
    "drawable/splash.png": (480, 320),
    "drawable-port-mdpi/splash.png": (320, 480),
    "drawable-port-hdpi/splash.png": (480, 800),
    "drawable-port-xhdpi/splash.png": (720, 1280),
    "drawable-port-xxhdpi/splash.png": (960, 1600),
    "drawable-port-xxxhdpi/splash.png": (1280, 1920),
    "drawable-land-mdpi/splash.png": (480, 320),
    "drawable-land-hdpi/splash.png": (800, 480),
    "drawable-land-xhdpi/splash.png": (1280, 720),
    "drawable-land-xxhdpi/splash.png": (1600, 960),
    "drawable-land-xxxhdpi/splash.png": (1920, 1280),
}


def make_master(size=1024):
    """渐变 + 透明圆角方块，颜色/角度/圆角比例与 CSS 完全一致。"""
    y, x = np.mgrid[0:size, 0:size]
    t = (x + y) / (2.0 * (size - 1))          # 135°：左上 → 右下
    rgb = START[None, None, :] + (END - START)[None, None, :] * t[:, :, None]
    rgba = np.dstack([rgb, np.full((size, size), 255.0)]).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")

    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = max(1, round(size * RADIUS_RATIO))
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def resize(img, size):
    return img.resize((size, size), Image.LANCZOS)


def centered_mark(canvas, mark, bg):
    w, h = canvas.size
    side = max(1, round(min(w, h) * MARK_RATIO))
    m = resize(mark, side)
    canvas.paste(m, ((w - side) // 2, (h - side) // 2), m)
    return canvas


def make_legacy(mark, size):
    """传统 launcher：奶油底 + 居中方块（避免在旧启动器上像一整块纯蓝）。"""
    canvas = Image.new("RGBA", (size, size), CREAM)
    return centered_mark(canvas, mark, CREAM)


def make_round(mark, size):
    """圆形 launcher：奶油圆底 + 居中方块，圆形外透明。"""
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    circle = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, size - 1, size - 1), fill=255)
    cream = Image.new("RGBA", (size, size), CREAM)
    base.paste(cream, (0, 0), circle)
    return centered_mark(base, mark, CREAM)


def make_foreground(mark, size):
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return centered_mark(base, mark, None)


def make_splash(mark, w, h):
    canvas = Image.new("RGBA", (w, h), CREAM)
    side = max(1, round(min(w, h) * SPLASH_MARK_RATIO))
    m = resize(mark, side)
    canvas.paste(m, ((w - side) // 2, (h - side) // 2), m)
    return canvas


def main():
    BRAND.mkdir(parents=True, exist_ok=True)
    mark = make_master(1024)
    mark.save(BRAND / "brand-1024.png", optimize=True)

    mark.save(BRAND / "xltc.ico",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                     (64, 64), (128, 128), (256, 256)])

    for density, size in MIPMAPS.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        make_legacy(mark, size).save(folder / "ic_launcher.png", optimize=True)
        make_round(mark, size).save(folder / "ic_launcher_round.png", optimize=True)
    for density, size in FOREGROUNDS.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        make_foreground(mark, size).save(folder / "ic_launcher_foreground.png", optimize=True)

    for rel, (w, h) in SPLASHES.items():
        path = RES / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        make_splash(mark, w, h).save(path, optimize=True)

    print("brand assets generated")
    print("  assets/brand/brand-1024.png")
    print("  assets/brand/xltc.ico")
    print("  Android mipmap-* / drawable*")


if __name__ == "__main__":
    main()
