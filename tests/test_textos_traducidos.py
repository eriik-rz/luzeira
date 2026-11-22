"""Que no queden textos de usuario escritos a mano en español fuera de los catálogos (C-62).

Busca literales con tildes, «ñ», «¿», «¡» o comillas angulares en el código de la app
(Python) y del editor (TS/TSX). No cuenta docstrings, comentarios ni lo que va a los logs
(los logs siguen en español a propósito). Un literal que de verdad tiene que quedarse
(p. ej. «Español» en la lista de idiomas) se marca en su línea con ``i18n: no``.

Es un trinquete: ``textos_pendientes.json`` dice cuántos quedan en cada fichero. No se
puede subir, y al bajar hay que bajar también el número (así el progreso queda escrito).
Es una heurística: un texto sin tildes («Guardar») no lo ve. Lo demás lo mira el revisor.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PENDING = Path(__file__).with_name("textos_pendientes.json")
SPANISH = re.compile(r"[áéíóúÁÉÍÓÚñÑ¿¡«»]")
LOG_NAMES = {"log", "logger", "logging"}
SKIP_PY = {"src/luzeira/idioma.py"}


def _python_hits(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    skip: set[int] = set()  # id() de los nodos que no cuentan
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            skip.add(id(body[0].value))  # docstring
        if isinstance(node, ast.Call):
            func = node.func
            owner = func.value if isinstance(func, ast.Attribute) else None
            if isinstance(owner, ast.Name) and owner.id in LOG_NAMES:
                for sub in ast.walk(node):
                    skip.add(id(sub))
    hits = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and SPANISH.search(node.value)
            and "i18n: no" not in lines[node.lineno - 1]
        ):
            hits += 1
    return hits


_TS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:'\"`])//[^\n]*", re.S)
_TS_STRING = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`|>[^<>{}]+<")


def _ts_hits(path: Path) -> int:
    hits = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if "i18n: no" in line:
            continue
        code = _TS_COMMENT.sub("", line)
        hits += sum(1 for m in _TS_STRING.finditer(code) if SPANISH.search(m.group()))
    return hits


def count_all() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted((ROOT / "src" / "luzeira").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in SKIP_PY and (n := _python_hits(path)):
            counts[rel] = n
    for path in sorted((ROOT / "editor" / "src").rglob("*.ts*")):
        rel = path.relative_to(ROOT).as_posix()
        if "/i18n/" not in rel and not rel.endswith(".d.ts") and (n := _ts_hits(path)):
            counts[rel] = n
    return counts


def test_no_new_untranslated_texts_and_the_list_goes_down():
    pending = json.loads(PENDING.read_text(encoding="utf-8"))
    now = count_all()
    grew = {f: (pending.get(f, 0), n) for f, n in now.items() if n > pending.get(f, 0)}
    assert grew == {}, f"textos en español nuevos fuera de los catálogos (antes, ahora): {grew}"
    shrank = {f: (n, now.get(f, 0)) for f, n in pending.items() if now.get(f, 0) < n}
    assert shrank == {}, f"bien: han bajado; baja también textos_pendientes.json: {shrank}"


if __name__ == "__main__":  # para regenerar la lista: uv run python tests/test_textos_traducidos.py
    PENDING.write_text(
        json.dumps(count_all(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
