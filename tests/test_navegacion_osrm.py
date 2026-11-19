"""Navegación (HU-30) con OSRM + Nominatim, el proveedor por defecto: traducción de maniobras,
indicaciones en español, parámetros, errores y el límite de Nominatim.

Nunca se llama a los servidores reales: el cliente HTTP se reemplaza por un `httpx.MockTransport`.
"""

import time

import httpx
import pytest

from app.core.config import settings
from app.navegacion import comun, proveedor_osrm, service
from app.navegacion.schemas import TipoPaso
from tests.conftest import cabecera

RUTA = "/api/v1/navegacion/ruta"
LUGARES = "/api/v1/navegacion/lugares"
PARAMETROS_RUTA = {
    "origen_lat": -17.78345,
    "origen_lon": -63.18212,
    "destino_lat": -17.76201,
    "destino_lon": -63.19555,
}


def paso(tipo, modificador=None, nombre="", puntos=2, distancia=100.0, salida=None):
    maniobra = {"type": tipo}
    if modificador:
        maniobra["modifier"] = modificador
    if salida:
        maniobra["exit"] = salida
    return {
        "distance": distancia,
        "duration": distancia / 10,
        "name": nombre,
        "maneuver": maniobra,
        "geometry": {"coordinates": [[-63.18, -17.78]] * puntos},
    }


# Trazado de 6 puntos: salida (3 puntos), rotonda (3 puntos), llegada (1 punto)
RESPUESTA_RUTA = {
    "code": "Ok",
    "routes": [
        {
            "distance": 1500.0,
            "duration": 240.0,
            "geometry": {
                "coordinates": [
                    [-63.18212, -17.78345],
                    [-63.1825, -17.7820],
                    [-63.1830, -17.7800],
                    [-63.1850, -17.7750],
                    [-63.1900, -17.7700],
                    [-63.19555, -17.76201],
                ]
            },
            "legs": [
                {
                    "steps": [
                        paso("depart", nombre="Calle Junín", puntos=3, distancia=400),
                        paso("roundabout", "right", nombre="Avenida Busch", puntos=4, distancia=1100, salida=2),
                        paso("arrive", puntos=1, distancia=0),
                    ]
                }
            ],
        }
    ],
}

RESPUESTA_NOMINATIM = [
    {
        "lat": "-17.7676",
        "lon": "-63.1975",
        "name": "Cristo Redentor",
        "display_name": "Cristo Redentor, Santa Cruz de la Sierra, Santa Cruz, Bolivia",
    },
    # Sin `name`: se toma la primera parte de la dirección
    {
        "lat": "-17.7",
        "lon": "-63.2",
        "name": "",
        "display_name": "Calle Junín, Santa Cruz de la Sierra, Bolivia",
    },
]


@pytest.fixture(autouse=True)
def limpiar_caches():
    service.cache_rutas.limpiar()
    service.cache_lugares.limpiar()
    yield
    service.cache_rutas.limpiar()
    service.cache_lugares.limpiar()


@pytest.fixture
def osrm(monkeypatch):
    """OSRM y Nominatim simulados; devuelve el registro de pedidos."""

    class Simulado:
        ruta: tuple[int, object] = (200, RESPUESTA_RUTA)
        lugares: tuple[int, object] = (200, RESPUESTA_NOMINATIM)
        error: Exception | None = None
        pedidos: list[httpx.Request] = []

    simulado = Simulado()
    simulado.pedidos = []

    def manejar(pedido: httpx.Request) -> httpx.Response:
        simulado.pedidos.append(pedido)
        if simulado.error is not None:
            raise simulado.error
        estado, cuerpo = simulado.ruta if "/route/" in pedido.url.path else simulado.lugares
        if isinstance(cuerpo, str):
            return httpx.Response(estado, text=cuerpo)
        return httpx.Response(estado, json=cuerpo)

    monkeypatch.setattr(settings, "navegacion_proveedor", "osrm")
    monkeypatch.setattr(settings, "osrm_url", "https://osrm.example.com")
    monkeypatch.setattr(settings, "nominatim_url", "https://nominatim.example.com")
    monkeypatch.setattr(
        proveedor_osrm,
        "_cliente",
        httpx.AsyncClient(
            headers={"User-Agent": comun.USER_AGENT}, transport=httpx.MockTransport(manejar)
        ),
    )
    # Sin esperar el segundo de Nominatim entre pruebas
    monkeypatch.setattr(proveedor_osrm, "_ultima_nominatim", 0.0)
    return simulado


async def registrar_conductor(cliente) -> dict:
    respuesta = await cliente.post(
        "/api/v1/auth/registro",
        json={
            "nombre": "Conductor",
            "email": "conductor.osrm@empresa.example.com",
            "telefono": "72000000",
            "contrasenia": "Clave1234",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# --- Traducción (puras) -----------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "modificador", "esperado"),
    [
        ("depart", None, TipoPaso.salida),
        ("arrive", None, TipoPaso.llegada),
        ("turn", "left", TipoPaso.girar_izquierda),
        ("turn", "sharp right", TipoPaso.girar_fuerte_derecha),
        ("end of road", "slight left", TipoPaso.girar_leve_izquierda),
        ("new name", "straight", TipoPaso.seguir_recto),
        ("continue", None, TipoPaso.seguir_recto),
        ("turn", "uturn", TipoPaso.media_vuelta),
        ("roundabout", "right", TipoPaso.entrar_rotonda),
        ("rotary", None, TipoPaso.entrar_rotonda),
        ("exit roundabout", "right", TipoPaso.salir_rotonda),
        ("fork", "slight left", TipoPaso.mantener_izquierda),
        ("off ramp", "right", TipoPaso.mantener_derecha),
        ("merge", "straight", TipoPaso.seguir_recto),
        ("notification", "left", TipoPaso.girar_izquierda),
        ("algo nuevo", None, TipoPaso.seguir_recto),
    ],
)
def test_tipos_de_maniobra(tipo, modificador, esperado):
    assert proveedor_osrm.tipo_de_maniobra(tipo, modificador) == esperado


def test_traduce_la_ruta_con_indices_e_indicaciones():
    ruta = proveedor_osrm.ruta_desde_osrm(RESPUESTA_RUTA)
    assert ruta.distancia_m == 1500
    assert ruta.duracion_s == 240
    # OSRM da [lon, lat]
    assert (ruta.trazado[0].lat, ruta.trazado[0].lon) == (-17.78345, -63.18212)
    assert [p.tipo for p in ruta.pasos] == [
        TipoPaso.salida,
        TipoPaso.entrar_rotonda,
        TipoPaso.llegada,
    ]
    # Índices en el trazado: los pasos comparten su punto de unión
    assert [(p.inicio, p.fin) for p in ruta.pasos] == [(0, 2), (2, 5), (5, 5)]
    assert ruta.pasos[0].instruccion == "Sal por Calle Junín"
    assert ruta.pasos[1].instruccion == "En la rotonda, toma la segunda salida"
    assert ruta.pasos[1].salida_rotonda == 2
    assert ruta.pasos[2].instruccion == "Llegaste a tu destino"
    assert ruta.pasos[2].calle is None


def test_indicaciones_en_espanol():
    assert comun.instruccion(TipoPaso.girar_derecha, "Avenida Busch") == (
        "Gira a la derecha en Avenida Busch"
    )
    assert comun.instruccion(TipoPaso.seguir_recto, "Calle Junín") == "Sigue derecho por Calle Junín"
    assert comun.instruccion(TipoPaso.entrar_rotonda, None, 11) == "En la rotonda, toma la 11.ª salida"
    assert comun.instruccion(TipoPaso.entrar_rotonda, None) == "Entra a la rotonda"
    assert comun.instruccion(TipoPaso.salida, None) == "Comienza el recorrido"


def test_traduce_los_lugares():
    lugares = proveedor_osrm.lugares_desde_nominatim(RESPUESTA_NOMINATIM)
    assert [l.nombre for l in lugares] == ["Cristo Redentor", "Calle Junín"]
    assert lugares[0].direccion.endswith("Bolivia")
    assert (lugares[0].lat, lugares[0].lon) == (-17.7676, -63.1975)
    assert len(proveedor_osrm.lugares_desde_nominatim(RESPUESTA_NOMINATIM * 4)) == 5


# --- Endpoints con OSRM y Nominatim simulados -------------------------------


async def test_ruta_y_pedido_a_osrm(cliente, osrm):
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["pasos"][1]["tipo"] == "entrar_rotonda"
    assert cuerpo["pasos"][1]["instruccion"] == "En la rotonda, toma la segunda salida"

    pedido = osrm.pedidos[0]
    # [lon, lat] con las coordenadas redondeadas de la caché
    assert pedido.url.path == "/route/v1/driving/-63.182100,-17.783400;-63.195500,-17.762000"
    assert pedido.url.params["steps"] == "true"
    assert pedido.url.params["geometries"] == "geojson"
    assert pedido.headers["User-Agent"] == comun.USER_AGENT

    # La misma consulta sale de la caché
    await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert len(osrm.pedidos) == 1


async def test_lugares_con_nominatim(cliente, osrm):
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(LUGARES, params={"q": " Cristo  Redentor "}, headers=cabecera(sesion))
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["lugares"][0]["nombre"] == "Cristo Redentor"

    params = osrm.pedidos[0].url.params
    assert osrm.pedidos[0].url.path == "/search"
    assert params["q"] == "Cristo Redentor"
    assert params["countrycodes"] == "bo"
    assert params["accept-language"] == "es"
    assert params["format"] == "jsonv2"
    assert params["viewbox"] == proveedor_osrm.CAJA_SANTA_CRUZ
    # Prioriza Santa Cruz sin excluir el resto de Bolivia
    assert "bounded" not in params
    assert osrm.pedidos[0].headers["User-Agent"] == comun.USER_AGENT


async def test_nominatim_una_consulta_por_segundo(cliente, osrm):
    sesion = await registrar_conductor(cliente)
    inicio = time.monotonic()
    for texto in ("Cristo Redentor", "Plaza 24 de Septiembre"):
        respuesta = await cliente.get(LUGARES, params={"q": texto}, headers=cabecera(sesion))
        assert respuesta.status_code == 200
    assert len(osrm.pedidos) == 2
    assert time.monotonic() - inicio >= proveedor_osrm.ESPERA_NOMINATIM_S * 0.95


@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado", "codigo"),
    [
        (400, {"code": "NoRoute", "message": "Impossible route"}, 404, "sin_ruta"),
        (400, {"code": "NoSegment", "message": "Could not find a matching segment"}, 404, "sin_ruta"),
        (429, {"message": "Too Many Requests"}, 503, "sin_cuota"),
        (502, "Bad Gateway", 503, "proveedor_caido"),
        (400, {"code": "InvalidQuery"}, 503, "proveedor_caido"),
        (200, "<html>no es json</html>", 503, "proveedor_caido"),
    ],
)
async def test_errores_de_osrm(cliente, osrm, estado, cuerpo, esperado, codigo):
    osrm.ruta = (estado, cuerpo)
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert respuesta.status_code == esperado
    assert respuesta.json()["codigo"] == codigo


async def test_punto_lejos_de_una_calle_es_sin_ruta(cliente, osrm):
    # OSRM lo llevó 5,7 km hasta la calle más cercana
    respuesta = dict(RESPUESTA_RUTA)
    respuesta["waypoints"] = [{"distance": 55.0}, {"distance": 5705.0}]
    osrm.ruta = (200, respuesta)
    sesion = await registrar_conductor(cliente)
    r = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert r.status_code == 404
    assert r.json()["codigo"] == "sin_ruta"

    # Cerca de una calle, sí
    respuesta["waypoints"] = [{"distance": 55.0}, {"distance": 120.0}]
    service.cache_rutas.limpiar()
    r = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert r.status_code == 200


@pytest.mark.parametrize(
    ("estado", "codigo"),
    [(429, "sin_cuota"), (503, "proveedor_caido"), (403, "proveedor_caido")],
)
async def test_errores_de_nominatim(cliente, osrm, estado, codigo):
    osrm.lugares = (estado, {"error": "x"})
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(LUGARES, params={"q": "Cristo"}, headers=cabecera(sesion))
    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == codigo


@pytest.mark.parametrize("error", [httpx.ReadTimeout("tiempo"), httpx.ConnectError("sin red")])
async def test_sin_respuesta(cliente, osrm, error):
    osrm.error = error
    sesion = await registrar_conductor(cliente)
    for url, params in ((RUTA, PARAMETROS_RUTA), (LUGARES, {"q": "Cristo"})):
        respuesta = await cliente.get(url, params=params, headers=cabecera(sesion))
        assert respuesta.status_code == 503
        assert respuesta.json()["codigo"] == "proveedor_caido"


@pytest.mark.parametrize("proveedor", ["desactivada", "ors", "otro"])
async def test_desactivada_no_llama(cliente, osrm, monkeypatch, proveedor):
    # ORS sin clave también queda desactivada
    monkeypatch.setattr(settings, "navegacion_proveedor", proveedor)
    monkeypatch.setattr(settings, "ors_api_key", "")
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == "navegacion_desactivada"
    assert osrm.pedidos == []
