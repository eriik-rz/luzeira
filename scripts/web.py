"""Monta la web del proyecto (GitHub Pages) en ``_site/``.

Qué hace:
1. Copia ``web/`` (la portada, en HTML y CSS a mano).
2. Dibuja el icono de Luzeira (``luzeira.marca``) y copia la captura de los temas.
3. Compila el editor en **modo simulado** dentro de ``_site/demo/``: el mismo editor
   que va en la app, con datos inventados y una vista previa aproximada. No habla con
   ningún servidor ni guarda nada.

Uso: ``uv run python scripts/web.py`` (necesita ``npm ci`` hecho en ``editor/``).
Para verla: ``uv run python -m http.server -d _site 8000 --bind 127.0.0.1``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
sys.path.insert(0, str(ROOT / "src"))

from luzeira.marca import draw_mark  # noqa: E402


def main() -> int:
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "web", SITE)
    draw_mark(256).save(SITE / "icono.png", optimize=True)
    draw_mark(64).save(SITE / "favicon.png", optimize=True)
    shutil.copy(ROOT / "guia" / "temas.png", SITE / "temas.png")

    npx = shutil.which("npx")
    if npx is None:
        print("Falta Node.js (npx).", file=sys.stderr)
        return 1
    env = {**os.environ, "VITE_SIMULADO": "1"}
    subprocess.run(  # noqa: S603 — npx del PATH, argumentos fijos
        [npx, "vite", "build", "--outDir", str(SITE / "demo"), "--emptyOutDir"],
        cwd=ROOT / "editor",
        env=env,
        check=True,
    )
    print(f"Web montada en {SITE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
