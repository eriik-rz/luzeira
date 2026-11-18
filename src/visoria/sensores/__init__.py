"""Sensores del PC (sin admin en v1)."""

from visoria.sensores.base import Reading, SensorInfo, SensorSource
from visoria.sensores.hub import SensorHub, default_hub

__all__ = ["Reading", "SensorHub", "SensorInfo", "SensorSource", "default_hub"]
