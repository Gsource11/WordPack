from __future__ import annotations

import base64
from pathlib import Path

APP_NAME_ZH = "词小包"
APP_NAME_EN = "WordPack"
APP_TITLE = f"{APP_NAME_ZH} {APP_NAME_EN}"

_ICON_ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def icon_path(filename: str) -> Path:
    return _base_dir() / "icon" / filename


def icon_data_url() -> str:
    for filename, mime in (("app-icon.png", "image/png"), ("app-icon.svg", "image/svg+xml")):
        path = icon_path(filename)
        if not path.exists():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    return ""


def ensure_icon_ico() -> Path | None:
    ico_path = icon_path("app-icon.ico")
    png_path = icon_path("app-icon.png")

    if not png_path.exists():
        return ico_path if ico_path.exists() else None

    try:
        from PIL import Image
    except Exception:
        return ico_path if ico_path.exists() else None

    try:
        with Image.open(png_path) as image:
            image.convert("RGBA").save(ico_path, format="ICO", sizes=_ICON_ICO_SIZES)
    except Exception:
        return ico_path if ico_path.exists() else None

    return ico_path if ico_path.exists() else None
