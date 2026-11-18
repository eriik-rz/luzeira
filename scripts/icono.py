"""Genera el icono de Visoria (.ico con todos los tamaños de Windows) a partir del mismo
diseño que la bandeja (``bandeja.make_icon``). Uso:  uv run python scripts/icono.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "src" / "visoria" / "recursos" / "visoria.ico"
FONT = OUT.parent / "fuentes" / "bold.ttf"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    """Cuadrado redondeado naranja con una «V» oscura, dibujado a su tamaño (no escalado,
    para que los pequeños salgan nítidos)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 32)
    d.rounded_rectangle(
        (pad, pad, size - 1 - pad, size - 1 - pad), radius=size * 7 // 32, fill=(255, 140, 0)
    )
    font = ImageFont.truetype(str(FONT), round(size * 0.69))
    d.text((size / 2, size * 0.52), "V", font=font, fill=(12, 16, 28), anchor="mm")
    return img


def main() -> None:
    images = [draw(s) for s in SIZES]
    images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"{OUT.name}: {OUT.stat().st_size} B, tamaños {SIZES}")


if __name__ == "__main__":
    main()
