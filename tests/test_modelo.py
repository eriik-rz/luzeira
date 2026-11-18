"""Reglas del formato de temas que viven en el modelo (no en el cargador)."""

from visoria.temas.modelo import Theme


def theme(*widgets):
    return {
        "format": "visoria-theme/1",
        "name": "x",
        "orientation": "landscape",
        "widgets": list(widgets),
    }


def bar(x, y, w=100, h=10):
    return {"type": "bar", "sensor": "a.b", "box": {"x": x, "y": y, "width": w, "height": h}}


def test_nan_and_infinity_are_rejected():
    import pytest
    from pydantic import ValidationError

    for bad in ("NaN", "Infinity", "-Infinity"):
        raw = (
            '{"format": "visoria-theme/1", "name": "x", "orientation": "landscape", "widgets": '
            '[{"type": "bar", "sensor": "a.b", "max": ' + bad + ", "
            '"box": {"x": 0, "y": 0, "width": 10, "height": 10}}]}'
        )
        with pytest.raises(ValidationError):
            Theme.model_validate_json(raw)


def test_overlapping_widgets_are_allowed():
    # D-11 (2026-11-17): el motor compone capas, así que los solapes se pintan bien y el
    # editor puede superponer widgets. Antes estaban prohibidos.
    Theme.model_validate(theme(bar(0, 0), bar(50, 5)))
