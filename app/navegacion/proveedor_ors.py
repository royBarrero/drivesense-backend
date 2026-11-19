"""Cliente de OpenRouteService (HU-30): rutas en auto y búsqueda de lugares. Se usa con
`NAVEGACION_PROVEEDOR=ors`; el proveedor por defecto es OSRM + Nominatim (`proveedor_osrm.py`).

Solo el backend llama a ORS: la clave (`ORS_API_KEY`) va en el header `Authorization` y nunca
llega a la app ni aparece en la URL o en el log. A ORS solo van coordenadas o el texto buscado:
nunca datos del conductor.

Las funciones `ruta_desde_ors`, `lugares_desde_ors` y `tipo_de_paso` son puras (con pruebas);
`pedir_ruta` y `buscar_lugares` hacen la llamada y traducen los errores a `ErrorDeNavegacion`.
"""

import logging

import httpx
from fastapi import status

from app.core.config import settings
from app.core.errores import ErrorDeNavegacion
from app.navegacion.comun import (  # noqa: F401  (las pruebas los usan desde aquí)
    DESTINO_LEJANO,
    FOCO_LAT,
    FOCO_LON,
    MAXIMO_LUGARES,
    PAIS,
    PROVEEDOR_CAIDO,
    SIN_CUOTA,
    SIN_RUTA,
    caido,
)
from app.navegacion.schemas import Coordenada, Lugar, PasoRuta, RutaSalida, TipoPaso

logger = logging.getLogger(__name__)

# Perfil de auto: respeta el sentido de las calles (oneway de OSM), los giros prohibidos y
# las vías por las que no pasa un auto
PERFIL = "driving-car"
IDIOMA = "es"

# Códigos de error de ORS (`error.code`)
ORS_PARAMETRO_EXCEDE_MAXIMO = 2004  # p. ej. la distancia de la ruta supera el máximo del perfil
ORS_RUTA_NO_ENCONTRADA = 2009
ORS_PUNTO_NO_ENCONTRADO = 2010  # no hay calle para autos cerca del origen o del destino

# ORS numera las maniobras del 0 al 13
_TIPOS = {
    0: TipoPaso.girar_izquierda,
    1: TipoPaso.girar_derecha,
    2: TipoPaso.girar_fuerte_izquierda,
    3: TipoPaso.girar_fuerte_derecha,
    4: TipoPaso.girar_leve_izquierda,
    5: TipoPaso.girar_leve_derecha,
    6: TipoPaso.seguir_recto,
    7: TipoPaso.entrar_rotonda,
    8: TipoPaso.salir_rotonda,
    9: TipoPaso.media_vuelta,
    10: TipoPaso.llegada,
    11: TipoPaso.salida,
    12: TipoPaso.mantener_izquierda,
    13: TipoPaso.mantener_derecha,
}


def tipo_de_paso(codigo: int) -> TipoPaso:
    """Nombre estable de la maniobra (pura). Un código nuevo de ORS se toma como seguir recto."""
    return _TIPOS.get(codigo, TipoPaso.seguir_recto)


def _calle(nombre: str | None) -> str | None:
    # ORS manda "-" cuando la vía no tiene nombre
    nombre = (nombre or "").strip()
    return nombre if nombre and nombre != "-" else None


def ruta_desde_ors(datos: dict) -> RutaSalida:
    """Traduce la respuesta GeoJSON de `/v2/directions/{perfil}/geojson` (pura)."""
    ruta = datos["features"][0]
    propiedades = ruta.get("properties", {})
    # Con origen = destino el resumen llega vacío
    resumen = propiedades.get("summary", {})
    trazado = [Coordenada(lat=lat, lon=lon) for lon, lat, *_ in ruta["geometry"]["coordinates"]]
    pasos = [
        PasoRuta(
            instruccion=paso.get("instruction", ""),
            tipo=tipo_de_paso(paso.get("type", 6)),
            calle=_calle(paso.get("name")),
            distancia_m=paso.get("distance", 0.0),
            duracion_s=paso.get("duration", 0.0),
            salida_rotonda=paso.get("exit_number"),
            inicio=paso["way_points"][0],
            fin=paso["way_points"][1],
        )
        for segmento in propiedades.get("segments", [])
        for paso in segmento.get("steps", [])
    ]
    return RutaSalida(
        distancia_m=resumen.get("distance", 0.0),
        duracion_s=resumen.get("duration", 0.0),
        trazado=trazado,
        pasos=pasos,
    )


def lugares_desde_ors(datos: dict) -> list[Lugar]:
    """Traduce la respuesta de `/geocode/search` (pura)."""
    lugares = []
    for lugar in datos.get("features", []):
        propiedades = lugar.get("properties", {})
        lon, lat, *_ = lugar["geometry"]["coordinates"]
        nombre = propiedades.get("name") or propiedades.get("label") or ""
        lugares.append(
            Lugar(nombre=nombre, direccion=propiedades.get("label") or nombre, lat=lat, lon=lon)
        )
    return lugares[:MAXIMO_LUGARES]


def _codigo_ors(respuesta: httpx.Response) -> int | None:
    try:
        error = respuesta.json().get("error")
    except ValueError:
        return None
    return error.get("code") if isinstance(error, dict) else None


def _error_de(respuesta: httpx.Response, que: str) -> ErrorDeNavegacion:
    """Traduce una respuesta con error de ORS al error que ve la app."""
    codigo = _codigo_ors(respuesta)
    logger.warning("ORS (%s): error %s, código %s", que, respuesta.status_code, codigo)
    if respuesta.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return ErrorDeNavegacion(status.HTTP_503_SERVICE_UNAVAILABLE, "sin_cuota", SIN_CUOTA)
    if codigo in (ORS_RUTA_NO_ENCONTRADA, ORS_PUNTO_NO_ENCONTRADO):
        return ErrorDeNavegacion(status.HTTP_404_NOT_FOUND, "sin_ruta", SIN_RUTA)
    if codigo == ORS_PARAMETRO_EXCEDE_MAXIMO:
        return ErrorDeNavegacion(status.HTTP_404_NOT_FOUND, "sin_ruta", DESTINO_LEJANO)
    if respuesta.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        logger.error("ORS rechazó la clave: revisar ORS_API_KEY")
    # Clave inválida, 5xx u otro error inesperado: para el conductor, el servicio no responde
    return caido()


_cliente: httpx.AsyncClient | None = None


def _obtener_cliente() -> httpx.AsyncClient:
    global _cliente
    if _cliente is None:
        _cliente = httpx.AsyncClient(
            base_url=settings.ors_url,
            timeout=settings.navegacion_timeout_s,
            headers={"Authorization": settings.ors_api_key},
        )
    return _cliente


async def _llamar(que: str, metodo: str, ruta: str, **kwargs) -> dict:
    try:
        respuesta = await _obtener_cliente().request(metodo, ruta, **kwargs)
    except httpx.TimeoutException:
        logger.warning("ORS (%s): sin respuesta en %s s", que, settings.navegacion_timeout_s)
        raise caido()
    except httpx.HTTPError as e:
        logger.warning("ORS (%s): sin conexión (%s)", que, type(e).__name__)
        raise caido()
    if respuesta.status_code != status.HTTP_200_OK:
        raise _error_de(respuesta, que)
    try:
        return respuesta.json()
    except ValueError:
        logger.warning("ORS (%s): respuesta que no es JSON", que)
        raise caido()


async def pedir_ruta(origen: Coordenada, destino: Coordenada) -> RutaSalida:
    datos = await _llamar(
        "ruta",
        "POST",
        f"/v2/directions/{PERFIL}/geojson",
        json={
            "coordinates": [[origen.lon, origen.lat], [destino.lon, destino.lat]],
            "language": IDIOMA,
            "instructions": True,
            "units": "m",
        },
    )
    try:
        return ruta_desde_ors(datos)
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("ORS (ruta): respuesta con formato inesperado")
        raise caido()


async def buscar_lugares(texto: str) -> list[Lugar]:
    datos = await _llamar(
        "lugares",
        "GET",
        "/geocode/search",
        params={
            "text": texto,
            "boundary.country": PAIS,
            "focus.point.lat": FOCO_LAT,
            "focus.point.lon": FOCO_LON,
            "size": MAXIMO_LUGARES,
            "lang": IDIOMA,
        },
    )
    try:
        return lugares_desde_ors(datos)
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("ORS (lugares): respuesta con formato inesperado")
        raise caido()
