"""Monta la web del proyecto (GitHub Pages) en ``_site/``.

Qué hace:
1. Copia ``web/`` (la portada, en HTML y CSS a mano), con ``web/en/`` (la versión en
   inglés, que usa el mismo CSS e imágenes con rutas ``../``).
2. Dibuja el icono de Luzeira (``luzeira.marca``) y copia la captura de los temas.
3. Compila el editor en **modo simulado** dentro de ``_site/demo/``: el mismo editor
   que va en la app, con datos inventados y una vista previa aproximada. No habla con
   ningún servidor ni guarda nada.
4. Copia los temas incluidos a ``_site/demo/temas/`` con su lista (``temas.json``):
   la demo los carga para que se puedan ver todos (``sim/simApi.ts``).
5. Pone a la demo su política de seguridad (``DEMO_CSP``) en un ``<meta>``: GitHub
   Pages no deja poner cabeceras. Solo aquí: en ``editor/index.html`` rompería
   ``npm run dev`` (Vite mete scripts propios), y en la app la pone el servidor local.

Uso: ``uv run python scripts/web.py`` (necesita ``npm ci`` hecho en ``editor/``).
Para verla: ``uv run python -m http.server -d _site 8000 --bind 127.0.0.1``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
sys.path.insert(0, str(ROOT / "src"))

from luzeira.marca import draw_mark  # noqa: E402

# Lo que la demo necesita y nada más: sus propios scripts, estilos y temas; imágenes y
# fuentes de un tema subido se leen como blob:/data:. Es la CSP del servidor local
# (``luzeira.editor.servidor.CSP``) sin ``frame-ancestors``, que en un <meta> no vale
# (la portada la enseña en un iframe). ``script-src`` sale de ``default-src 'self'``.
DEMO_CSP = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' blob: data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)
_CHARSET = '<meta charset="UTF-8" />'


def add_demo_csp(index: Path) -> None:
    """Mete la CSP justo después del ``charset`` del ``index.html`` de la demo: antes de
    cualquier ``<script>``, que si no ya se habría cargado sin ella."""
    html = index.read_bytes().decode("utf-8")
    if html.count(_CHARSET) != 1:
        raise SystemExit(f"{index}: no encuentro {_CHARSET} (¿ha cambiado editor/index.html?)")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{DEMO_CSP}" />'
    index.write_bytes(html.replace(_CHARSET, f"{_CHARSET}\n    {meta}").encode("utf-8"))


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
    add_demo_csp(SITE / "demo" / "index.html")
    themes_src = ROOT / "src" / "luzeira" / "recursos" / "temas"
    themes_dst = SITE / "demo" / "temas"
    shutil.copytree(themes_src, themes_dst)
    names = sorted(p.parent.name for p in themes_src.glob("*/theme.json"))
    (themes_dst / "temas.json").write_text(json.dumps(names), encoding="utf-8")
    print(f"Web montada en {SITE} ({len(names)} temas en la demo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
