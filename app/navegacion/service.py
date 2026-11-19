"""Navegación hacia un destino (HU-30): ruta en auto y búsqueda de lugares por nombre.

Elige el proveedor (`NAVEGACION_PROVEEDOR`: OSRM + Nominatim por defecto, OpenRouteService
con su clave o desactivada), usa la caché y, si no está ahí, le consulta.
"""

import logging
from types import ModuleType

from fastapi import status

from app.core.config import settings
from app.core.errores import ErrorDeNavegacion
from app.navegacion import proveedor_ors, proveedor_osrm
from app.navegacion.cache import CacheTTL
from app.navegacion.schemas import Coordenada, Lugar, LugaresSalida, RutaSalida

logger = logging.getLogger(__name__)

# 4 decimales ≈ 11 m: el mismo pedido con el GPS movido unos metros reutiliza la ruta
DECIMALES_CLAVE = 4
TTL_RUTA_S = 60 * 60
TTL_LUGARES_S = 24 * 60 * 60

NAVEGACION_DESACTIVADA = (
    "La navegación no está disponible: el servidor no tiene configurado el servicio de rutas"
)

cache_rutas: CacheTTL[RutaSalida] = CacheTTL(TTL_RUTA_S)
cache_lugares: CacheTTL[list[Lugar]] = CacheTTL(TTL_LUGARES_S)


def proveedor() -> ModuleType:
    """El proveedor configurado; `navegacion_desactivada` si no hay ninguno (o es ORS sin clave)."""
    nombre = settings.navegacion_proveedor.strip().lower()
    if nombre == "osrm":
        return proveedor_osrm
    if nombre == "ors" and settings.ors_api_key.strip():
        return proveedor_ors
    raise ErrorDeNavegacion(
        status.HTTP_503_SERVICE_UNAVAILABLE, "navegacion_desactivada", NAVEGACION_DESACTIVADA
    )


def clave_ruta(origen: Coordenada, destino: Coordenada) -> tuple[float, ...]:
    return tuple(round(v, DECIMALES_CLAVE) for v in (origen.lat, origen.lon, destino.lat, destino.lon))


def normalizar_texto(texto: str) -> str:
    """Texto de búsqueda sin espacios de más (pura); en minúsculas sirve de clave de la caché."""
    return " ".join(texto.split())


async def obtener_ruta(origen: Coordenada, destino: Coordenada) -> RutaSalida:
    consultar = proveedor()
    clave = clave_ruta(origen, destino)
    ruta = cache_rutas.obtener(clave)
    if ruta is not None:
        logger.info("Navegación: ruta desde la caché")
        return ruta
    # Se pide con las coordenadas redondeadas: lo guardado vale para cualquier pedido con esa clave
    ruta = await consultar.pedir_ruta(
        Coordenada(lat=clave[0], lon=clave[1]), Coordenada(lat=clave[2], lon=clave[3])
    )
    cache_rutas.guardar(clave, ruta)
    return ruta


async def buscar_lugares(texto: str) -> LugaresSalida:
    consultar = proveedor()
    texto = normalizar_texto(texto)
    clave = texto.lower()
    lugares = cache_lugares.obtener(clave)
    if lugares is None:
        lugares = await consultar.buscar_lugares(texto)
        cache_lugares.guardar(clave, lugares)
    else:
        logger.info("Navegación: lugares desde la caché")
    return LugaresSalida(lugares=lugares)
