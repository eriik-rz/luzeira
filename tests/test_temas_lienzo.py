"""El lienzo de un tema (``canvas``): pantallas de otros tamaños sin romper los temas de
siempre."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from luzeira.temas.modelo import (
    CANVAS_SIZES,
    CANVAS_SIZES_PORTRAIT,
    MAX_CANVAS_SIDE,
    MAX_DYNAMIC_SCREENS,
    MAX_WIDGET_SCREENS,
    ORIENTATION_SIZES,
    CanvasNotAllowed,
    Theme,
    TooMuchArea,
)


def theme(orientation="portrait", canvas=None, widgets=()):
    data = {
        "format": "luzeira-theme/1",
        "name": "Prueba",
        "orientation": orientation,
        "widgets": list(widgets),
    }
    if canvas is not None:
        data["canvas"] = {"width": canvas[0], "height": canvas[1]}
    return Theme.model_validate(data)


def text_at(x, y, w=10, h=10):
    return {"type": "text", "text": "a", "box": {"x": x, "y": y, "width": w, "height": h}}


def canvas_error(exc: pytest.ExceptionInfo) -> CanvasNotAllowed | None:
    for item in exc.value.errors():
        error = item.get("ctx", {}).get("error")
        if isinstance(error, CanvasNotAllowed):
            return error
    return None


@pytest.mark.parametrize("orientation", list(ORIENTATION_SIZES))
def test_without_canvas_the_size_is_the_same_as_always(orientation):
    t = theme(orientation)
    assert t.canvas is None
    assert t.size == ORIENTATION_SIZES[orientation]


def test_without_canvas_it_is_not_written_back():
    # Un tema sin lienzo se guarda igual que antes: lo siguen leyendo las versiones viejas.
    dumped = theme().model_dump(mode="json")
    assert "canvas" not in dumped
    assert "canvas" not in theme().model_dump_json()
    with_canvas = theme(canvas=(480, 800)).model_dump(mode="json")
    assert with_canvas["canvas"] == {"width": 480, "height": 800}


def test_allowed_sizes_are_the_known_displays_both_ways():
    portrait = {(320, 480), (480, 480), (480, 800), (600, 1024), (480, 1920)}
    assert set(CANVAS_SIZES_PORTRAIT) == portrait
    assert set(CANVAS_SIZES) == portrait | {(h, w) for w, h in portrait}
    assert len(CANVAS_SIZES) == len(set(CANVAS_SIZES)) == 9  # el cuadrado una vez
    assert max(max(s) for s in CANVAS_SIZES) == MAX_CANVAS_SIDE == 1920


@pytest.mark.parametrize("size", CANVAS_SIZES)
def test_every_allowed_canvas_works_with_its_orientation(size):
    orientations = []
    if size[0] <= size[1]:
        orientations += ["portrait", "reverse-portrait"]
    if size[0] >= size[1]:
        orientations += ["landscape", "reverse-landscape"]
    for orientation in orientations:
        assert theme(orientation, size).size == size


@pytest.mark.parametrize(
    ("orientation", "size"),
    [
        ("portrait", (500, 500)),  # no es de ninguna pantalla
        ("portrait", (800, 480)),  # horizontal en un tema vertical
        ("reverse-landscape", (480, 800)),
        ("landscape", (1920, 1920)),
    ],
)
def test_other_canvases_are_rejected(orientation, size):
    with pytest.raises(ValidationError) as exc:
        theme(orientation, size)
    error = canvas_error(exc)
    assert error is not None and (error.width, error.height) == size
    assert "480x800" in str(error) or "800x480" in str(error)  # dice cuáles valen


@pytest.mark.parametrize("side", [0, 1921, 4096])
def test_canvas_sides_are_bounded(side):
    with pytest.raises(ValidationError):
        theme("portrait", (side, side))


def test_widgets_must_fit_the_canvas_not_the_old_size():
    far = [text_at(400, 700)]  # fuera de 320x480, dentro de 480x800
    with pytest.raises(ValidationError):
        theme("portrait", None, far)
    assert theme("portrait", (480, 800), far).size == (480, 800)
    with pytest.raises(ValidationError):
        theme("portrait", (480, 800), [text_at(475, 0)])


def test_canvas_rejects_unknown_fields_and_floats():
    base = {"format": "luzeira-theme/1", "name": "x", "orientation": "portrait"}
    for canvas in (
        {"width": 480, "height": 800, "depth": 1},
        {"width": 480.5, "height": 800},
        [480, 800],
    ):
        with pytest.raises(ValidationError):
            Theme.model_validate_json(json.dumps({**base, "canvas": canvas}))


@pytest.mark.parametrize("orientation", list(ORIENTATION_SIZES))
def test_usual_canvas_written_by_hand_is_dropped(orientation):
    # Igual que no ponerlo: no se reescribe al guardar (las versiones viejas lo leen).
    t = theme(orientation, ORIENTATION_SIZES[orientation])
    assert t.canvas is None
    assert "canvas" not in t.model_dump(mode="json")
    assert theme(orientation, (480, 480)).canvas is not None


def _sensors(n, w, h):
    return [
        {"type": "sensor", "sensor": "a.b", "box": {"x": 0, "y": 0, "width": w, "height": h}}
    ] * n


def _texts(n, w, h):
    return [{"type": "text", "text": "a", "box": {"x": 0, "y": 0, "width": w, "height": h}}] * n


def area_error(exc: pytest.ExceptionInfo) -> TooMuchArea | None:
    for item in exc.value.errors():
        error = item.get("ctx", {}).get("error")
        if isinstance(error, TooMuchArea):
            return error
    return None


def test_dynamic_widgets_area_is_bounded_in_screens_of_the_canvas():
    assert MAX_DYNAMIC_SCREENS == 2 and MAX_WIDGET_SCREENS == 8
    # Justo en el tope, vale; un píxel más, no. En cualquier lienzo.
    for canvas in ((320, 480), (480, 1920)):
        w, h = canvas
        theme("portrait", canvas, _sensors(2, w, h))
        with pytest.raises(ValidationError) as exc:
            theme("portrait", canvas, _sensors(2, w, h) + _sensors(1, 1, 1))
        error = area_error(exc)
        assert error is not None and error.limit == 2
        assert "2" in str(error)


def test_all_widgets_area_is_bounded_too():
    theme("portrait", (480, 1920), _texts(8, 480, 1920))
    with pytest.raises(ValidationError) as exc:
        theme("portrait", (480, 1920), _texts(8, 480, 1920) + _texts(1, 1, 1))
    assert area_error(exc).limit == 8
