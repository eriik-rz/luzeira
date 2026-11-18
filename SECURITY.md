# Política de seguridad

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el reporte privado de GitHub: pestaña **Security → Report a vulnerability** del repositorio.

Incluye qué versión usas, cómo reproducirlo y qué impacto crees que tiene. Intentaremos responder en un plazo de 7 días.

## Versiones con soporte

Solo la última versión publicada recibe arreglos de seguridad.

## Cómo verificar que tu descarga es oficial

La única fuente oficial de Visoria son las **Releases de este repositorio** (y, cuando existan, los canales que se anuncien aquí). Cada versión publica la suma `SHA256SUMS` y una atestación de procedencia de GitHub:

```bash
gh attestation verify <fichero-descargado> --repo <propietario>/visoria
```

Si alguien distribuye Visoria desde otro sitio, no podemos garantizar que no esté modificada.

## Principios de diseño

- La app **no lleva claves ni tokens** dentro.
- **Sin telemetría** y sin anuncios.
- La app normal corre **sin permisos de administrador**. El módulo opcional de sensores de CPU (futuro) será un componente separado y mínimo.
- Los temas son **datos** (JSON e imágenes), nunca código ejecutable.
- Las actualizaciones se verifican con firma antes de instalarse.
