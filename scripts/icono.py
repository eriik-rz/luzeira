"""Genera el icono de Luzeira (.ico con todos los tamaños de Windows) a partir del lucero de
``luzeira/marca.py`` (el mismo que la bandeja). Uso:  uv run python scripts/icono.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from luzeira.marca import draw_mark

OUT = Path(__file__).resolve().parent.parent / "src" / "luzeira" / "recursos" / "luzeira.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    return draw_mark(size)


def main() -> None:
    images = [draw(s) for s in SIZES]
    images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"{OUT.name}: {OUT.stat().st_size} B, tamaños {SIZES}")


if __name__ == "__main__":
    main()
