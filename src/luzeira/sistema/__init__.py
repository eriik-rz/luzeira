"""Integración con Windows sin privilegios de administrador.

- ``autoarranque``: arrancar con la sesión del usuario (clave ``Run`` de HKCU).
- ``energia``: avisos de suspensión y reanudación (para apagar y encender la pantalla).
- ``instancia``: impedir que corran dos Luzeiras a la vez (y se peleen por el puerto).
- ``permisos``: leer quién es el propietario de un objeto de Windows y quién puede
  escribir en él (lo usan ``instancia``, ``autoarranque`` y el lector del módulo de
  sensores).
- ``registro``: log rotado con filtro de privacidad.

No se reexporta nada a propósito: cada submódulo se importa por su cuenta y ninguno
tiene efectos al importarse (tampoco fuera de Windows).
"""
