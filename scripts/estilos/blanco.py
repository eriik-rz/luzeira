"""Estilo Blanco: papel y tinta, tipo revista o pantalla de tinta electrónica.

Fondo liso color papel, filetes negros como en una maqueta editorial y un único acento
rojo (la marca de sección de arriba). Sin tarjetas ni sombras: así se distingue de
«Esencial Claro». Las coordenadas de los filetes son las mismas que usan los widgets
del ``theme.json`` de cada carpeta: si se mueve una, hay que mover la otra.

Sin grano ni degradados a propósito: un fondo liso pesa muy poco (la pantalla recibe la
imagen por USB serie) y en un panel TN un gris medio se ve igual desde cualquier ángulo.
"""

from __future__ import annotations

from collections.abc import Callable

from fondos import VH, VW, H, W
from PIL import Image, ImageDraw

PAPER = (243, 241, 236)  # blanco roto cálido: menos deslumbrante que el blanco puro
INK = (24, 24, 24)  # tinta: filetes principales
RULE = (176, 171, 162)  # filetes secundarios, gris medio con contraste de sobra
ACCENT = (200, 55, 29)  # el único color: rojo bermellón de imprenta

# Horizontal: columnas CPU · GPU · RAM (x de inicio, 136 px de ancho cada una).
BLANCO_COLUMNS = [24, 172, 320]
BLANCO_RULE_Y = 160  # filete grueso bajo el reloj
BLANCO_FOOT_Y = 286  # filete fino sobre el pie

# Vertical: filas CPU · GPU · RAM (y de inicio, 80 px de alto cada una).
BLANCO_V_ROWS = [190, 270, 350]
BLANCO_V_RULE_Y = 176
BLANCO_V_FOOT_Y = 434


def blanco() -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((24, 12, 63, 15), fill=ACCENT)  # marca de sección
    d.rectangle((24, BLANCO_RULE_Y, W - 25, BLANCO_RULE_Y + 1), fill=INK)
    for x in BLANCO_COLUMNS[1:]:  # filetes verticales entre columnas, en el hueco de 12 px
        d.rectangle((x - 7, 176, x - 6, 276), fill=RULE)
    d.rectangle((24, BLANCO_FOOT_Y, W - 25, BLANCO_FOOT_Y), fill=RULE)
    return img


def blanco_vertical() -> Image.Image:
    img = Image.new("RGB", (VW, VH), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((24, 16, 63, 19), fill=ACCENT)
    d.rectangle((24, BLANCO_V_RULE_Y, VW - 25, BLANCO_V_RULE_Y + 1), fill=INK)
    for y in BLANCO_V_ROWS[1:]:  # filete fino entre filas
        d.rectangle((24, y - 6, VW - 25, y - 6), fill=RULE)
    d.rectangle((24, BLANCO_V_FOOT_Y, VW - 25, BLANCO_V_FOOT_Y), fill=RULE)
    return img


FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("blanco", "fondo.png", blanco),
    ("blanco-vertical", "fondo.png", blanco_vertical),
]
