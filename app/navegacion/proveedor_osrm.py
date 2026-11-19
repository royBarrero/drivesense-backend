"""Rutas con OSRM y búsqueda de lugares con Nominatim (HU-30), sin clave. Es el proveedor por
defecto (`NAVEGACION_PROVEEDOR=osrm`).

Por defecto usa los servidores públicos de OpenStreetMap: uso razonable y sin garantía. Sus
políticas piden identificar la app (`USER_AGENT`, sin datos personales) y, en Nominatim, como
mucho una consulta por segundo y nada de autocompletar. A ellos solo van coordenadas o el texto
buscado: nunca datos del conductor.

OSRM no da las indicaciones en español: se arman aquí con el tipo de maniobra y la calle
(`comun.instruccion`). Las funciones `ruta_desde_osrm`, `tipo_de_maniobra` y
`lugares_desde_nominatim` son puras (con pruebas).
"""

import asyncio
import logging
import time

import httpx
from fastapi import status

from app.core.config import settings
from app.navegacion.comun import (
    MAXIMO_LUGARES,
    PAIS,
    USER_AGENT,
    caido,
    instruccion,
    sin_cuota,
    sin_ruta,
)
from app.navegacion.schemas import Coordenada, Lugar, PasoRuta, RutaSalida, TipoPaso

logger = logging.getLogger(__name__)

# Perfil de auto: respeta el sentido de las calles (oneway de OSM) y los giros prohibidos
PERFIL = "driving"

# Nominatim: como mucho una consulta por segundo (por proceso)
ESPERA_NOMINATIM_S = 1.0
# Prioriza Santa Cruz de la Sierra (alrededor de comun.FOCO_*) sin excluir el resto de Bolivia
# (oeste, norte, este, sur)
CAJA_SANTA_CRUZ = "-63.30,-17.65,-63.05,-17.92"

# Códigos de OSRM sin ruta: sin camino o sin calle para autos cerca del punto
CODIGOS_SIN_RUTA = {"NoRoute", "NoSegment"}

# OSRM lleva cada punto a la calle más cercana sin importar la distancia: más lejos que esto
# (como el radio de ORS) se considera que no hay calle para autos cerca del punto
MAXIMO_AJUSTE_M = 350.0

_GIROS = {
    "left": TipoPaso.girar_izquierda,
    "right": TipoPaso.girar_derecha,
    "sharp left": TipoPaso.girar_fuerte_izquierda,
    "sharp right": TipoPaso.girar_fuerte_derecha,
    "slight left": TipoPaso.girar_leve_izquierda,
    "slight right": TipoPaso.girar_leve_derecha,
    "straight": TipoPaso.seguir_recto,
    "uturn": TipoPaso.media_vuelta,
}


def tipo_de_maniobra(tipo: str, modificador: str | None) -> TipoPaso:
    """`maneuver.type` + `maneuver.modifier` de OSRM → maniobra (pura)."""
    if tipo == "depart":
        return TipoPaso.salida
    if tipo == "arrive":
        return TipoPaso.llegada
    if tipo in ("roundabout", "rotary"):
        return TipoPaso.entrar_rotonda
    if tipo in ("exit roundabout", "exit rotary"):
        return TipoPaso.salir_rotonda
    if modificador == "uturn":
        return TipoPaso.media_vuelta
    # Bifurcaciones, accesos e incorporaciones: mantenerse a un lado
    if tipo in ("fork", "on ramp", "off ramp", "merge"):
        if modificador in ("left", "slight left", "sharp left"):
            return TipoPaso.mantener_izquierda
        if modificador in ("right", "slight right", "sharp right"):
            return TipoPaso.mantener_derecha
        return TipoPaso.seguir_recto
    # turn, end of road, continue, new name, roundabout turn, notification...
    return _GIROS.get(modificador or "straight", TipoPaso.seguir_recto)


def _calle(nombre: str | None) -> str | None:
    nombre = (nombre or "").strip()
    return nombre or None


def ruta_desde_osrm(datos: dict) -> RutaSalida:
    """Traduce la respuesta de `/route/v1/driving` con `geometries=geojson&steps=true` (pura).

    OSRM no da índices de cada paso en el trazado: como el trazado completo es la unión de las
    geometrías de los pasos (que comparten su punto de unión), se calculan sumando sus puntos.
    """
    ruta = datos["routes"][0]
    trazado = [Coordenada(lat=lat, lon=lon) for lon, lat, *_ in ruta["geometry"]["coordinates"]]
    pasos: list[PasoRuta] = []
    indice = 0
    ultimo = max(0, len(trazado) - 1)
    for tramo in ruta.get("legs", []):
        for paso in tramo.get("steps", []):
            maniobra = paso.get("maneuver", {})
            tipo = tipo_de_maniobra(maniobra.get("type", ""), maniobra.get("modifier"))
            calle = _calle(paso.get("name"))
            salida = maniobra.get("exit") if tipo == TipoPaso.entrar_rotonda else None
            puntos = len(paso.get("geometry", {}).get("coordinates", []))
            fin = min(ultimo, indice + max(0, puntos - 1))
            pasos.append(
                PasoRuta(
                    instruccion=instruccion(tipo, calle, salida),
                    tipo=tipo,
                    calle=calle,
                    distancia_m=paso.get("distance", 0.0),
                    duracion_s=paso.get("duration", 0.0),
                    salida_rotonda=salida,
                    inicio=min(indice, ultimo),
                    fin=fin,
                )
            )
            indice = fin
    return RutaSalida(
        distancia_m=ruta.get("distance", 0.0),
        duracion_s=ruta.get("duration", 0.0),
        trazado=trazado,
        pasos=pasos,
    )


def lugares_desde_nominatim(datos: list) -> list[Lugar]:
    """Traduce la respuesta de `/search?format=jsonv2` (pura)."""
    lugares = []
    for lugar in datos:
        direccion = lugar.get("display_name") or ""
        nombre = lugar.get("name") or direccion.split(",")[0].strip()
        if not nombre:
            continue
        lugares.append(
            Lugar(
                nombre=nombre,
                direccion=direccion or nombre,
                lat=float(lugar["lat"]),
                lon=float(lugar["lon"]),
            )
        )
    return lugares[:MAXIMO_LUGARES]


_cliente: httpx.AsyncClient | None = None


def _obtener_cliente() -> httpx.AsyncClient:
    global _cliente
    if _cliente is None:
        _cliente = httpx.AsyncClient(
            timeout=settings.navegacion_timeout_s,
            headers={"User-Agent": USER_AGENT},
        )
    return _cliente


async def _llamar(que: str, url: str, params: dict | None = None) -> httpx.Response:
    try:
        return await _obtener_cliente().get(url, params=params)
    except httpx.TimeoutException:
        logger.warning("%s: sin respuesta en %s s", que, settings.navegacion_timeout_s)
        raise caido()
    except httpx.HTTPError as e:
        logger.warning("%s: sin conexión (%s)", que, type(e).__name__)
        raise caido()


def _json(respuesta: httpx.Response, que: str):
    try:
        return respuesta.json()
    except ValueError:
        logger.warning("%s: respuesta que no es JSON (%s)", que, respuesta.status_code)
        raise caido()


async def pedir_ruta(origen: Coordenada, destino: Coordenada) -> RutaSalida:
    coordenadas = f"{origen.lon:.6f},{origen.lat:.6f};{destino.lon:.6f},{destino.lat:.6f}"
    respuesta = await _llamar(
        "OSRM",
        f"{settings.osrm_url}/route/v1/{PERFIL}/{coordenadas}",
        {"overview": "full", "geometries": "geojson", "steps": "true"},
    )
    if respuesta.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        logger.warning("OSRM: límite de consultas")
        raise sin_cuota()
    if respuesta.status_code >= 500:
        logger.warning("OSRM: error %s", respuesta.status_code)
        raise caido()
    datos = _json(respuesta, "OSRM")
    codigo = datos.get("code") if isinstance(datos, dict) else None
    if codigo in CODIGOS_SIN_RUTA:
        raise sin_ruta()
    if codigo == "Ok" and any(
        (punto.get("distance") or 0) > MAXIMO_AJUSTE_M for punto in datos.get("waypoints", [])
    ):
        raise sin_ruta()
    if codigo != "Ok" or not datos.get("routes"):
        logger.warning("OSRM: error %s, código %s", respuesta.status_code, codigo)
        raise caido()
    try:
        return ruta_desde_osrm(datos)
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("OSRM: respuesta con formato inesperado")
        raise caido()


_turno_nominatim = asyncio.Lock()
_ultima_nominatim = 0.0


async def buscar_lugares(texto: str) -> list[Lugar]:
    global _ultima_nominatim
    # Una consulta por segundo: si la anterior fue hace menos, se espera
    async with _turno_nominatim:
        espera = ESPERA_NOMINATIM_S - (time.monotonic() - _ultima_nominatim)
        if espera > 0:
            await asyncio.sleep(espera)
        try:
            respuesta = await _llamar(
                "Nominatim",
                f"{settings.nominatim_url}/search",
                {
                    "q": texto,
                    "format": "jsonv2",
                    "countrycodes": PAIS.lower(),
                    "limit": MAXIMO_LUGARES,
                    "accept-language": "es",
                    "viewbox": CAJA_SANTA_CRUZ,
                },
            )
        finally:
            _ultima_nominatim = time.monotonic()
    if respuesta.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        logger.warning("Nominatim: límite de consultas")
        raise sin_cuota()
    if respuesta.status_code != status.HTTP_200_OK:
        logger.warning("Nominatim: error %s", respuesta.status_code)
        raise caido()
    datos = _json(respuesta, "Nominatim")
    if not isinstance(datos, list):
        raise caido()
    try:
        return lugares_desde_nominatim(datos)
    except (KeyError, TypeError, ValueError):
        logger.warning("Nominatim: respuesta con formato inesperado")
        raise caido()

