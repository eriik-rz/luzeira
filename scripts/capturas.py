"""Genera ``guia/temas.png``: muestra de temas incluidos para el README. Uso:
uv run python scripts/capturas.py

Reproducible: hora y lecturas fijas, inventadas (ni el hardware ni los datos de quien lo
ejecuta). Si cambia un tema, se vuelve a ejecutar y se commitea la imagen.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from visoria.config import BUILTIN_THEMES
from visoria.render import Renderer
from visoria.sensores.base import Reading
from visoria.temas import load_theme, resolve_font
from visoria.temas.modelo import Style

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "guia" / "temas.png"
THEMES = [
    "esencial", "glitch", "synthwave", "ambar",
    "aurora", "blanco", "rgb", "carbono",
    "hielo", "terminal", "instrumentos", "tarjetas",
]  # fmt: skip
COLUMNS = 4
SCALE = 0.5
GAP = 12
LABEL = 22
BACKGROUND = "#101216"
MOMENT = datetime(2026, 3, 13, 21, 47, 5)  # viernes, 13 de marzo

READINGS = {
    "cpu.usage": Reading(37.0, "%"),
    "cpu.name": Reading(None, "", text="AMD Ryzen 7 7800X3D 8-Core Processor"),
    "gpu.name": Reading(None, "", text="NVIDIA GeForce RTX 4070"),
    "gpu.usage": Reading(62.0, "%"),
    "gpu.temperature": Reading(58.0, "°C"),
    "gpu.power": Reading(143.0, "W"),
    "gpu.clock": Reading(2475.0, "MHz"),
    "gpu.fan": Reading(41.0, "%"),
    "gpu.memory_usage": Reading(34.0, "%"),
    "gpu.memory_used": Reading(4.1, "GB"),
    "gpu.memory_total": Reading(12.0, "GB"),
    "memory.usage": Reading(41.0, "%"),
    "memory.used": Reading(13.1, "GB"),
    "memory.total": Reading(32.0, "GB"),
    "disk.usage.c": Reading(58.0, "%"),
    "disk.read": Reading(12.4, "MB/s"),
    "disk.write": Reading(3.8, "MB/s"),
    "network.download": Reading(24.6, "MB/s"),
    "network.upload": Reading(1.2, "MB/s"),
    "system.uptime": Reading(6.5, "h"),
}


def render(name: str) -> tuple[Image.Image, str]:
    loaded = load_theme(BUILTIN_THEMES / name)
    renderer = Renderer(
        loaded.theme,
        font_for=lambda style: resolve_font(style, loaded.files),
        read_sensor=lambda sid: READINGS.get(sid, Reading(None, "")),
        files=loaded.files,
        now=lambda: MOMENT,
    )
    frame = renderer.full_frame().convert("RGB")
    size = (round(frame.width * SCALE), round(frame.height * SCALE))
    return frame.resize(size, Image.Resampling.LANCZOS), loaded.theme.name


def main() -> None:
    shots = [render(n) for n in THEMES]
    cell_w = max(img.width for img, _ in shots)
    cell_h = max(img.height for img, _ in shots) + LABEL
    rows = -(-len(shots) // COLUMNS)
    width = COLUMNS * cell_w + (COLUMNS + 1) * GAP
    height = rows * cell_h + (rows + 1) * GAP
    sheet = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = resolve_font(Style(size=14))  # la Inter incluida
    for i, (img, title) in enumerate(shots):
        x = GAP + (i % COLUMNS) * (cell_w + GAP)
        y = GAP + (i // COLUMNS) * (cell_h + GAP)
        sheet.paste(img, (x, y))
        draw.text((x, y + img.height + 4), title, font=font, fill="#c9ced8")
    OUT.parent.mkdir(exist_ok=True)
    sheet.save(OUT, optimize=True)
    print(f"{OUT.relative_to(ROOT)}  {OUT.stat().st_size} B")


if __name__ == "__main__":
    main()
