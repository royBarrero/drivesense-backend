"""Navegación (HU-30) con OpenRouteService: traducción, errores, caché, acceso y destino del
recorrido. OSRM + Nominatim, en test_navegacion_osrm.py.

ORS nunca se llama de verdad: el cliente HTTP se reemplaza por un `httpx.MockTransport`.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text

from app.core.config import settings
from app.navegacion import proveedor_ors as proveedor
from app.navegacion import service
from app.navegacion.cache import CacheTTL
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

RESPUESTA_RUTA = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "summary": {"distance": 3120.4, "duration": 410.2},
                "segments": [
                    {
                        "distance": 3120.4,
                        "duration": 410.2,
                        "steps": [
                            {
                                "distance": 200.0,
                                "duration": 30.0,
                                "type": 11,
                                "instruction": "Diríjase al norte por Calle Junín",
                                "name": "Calle Junín",
                                "way_points": [0, 1],
                            },
                            {
                                "distance": 2920.4,
                                "duration": 380.2,
                                "type": 7,
                                "instruction": "Entre en la rotonda y tome la 2.ª salida",
                                "name": "-",
                                "exit_number": 2,
                                "way_points": [1, 2],
                            },
                            {
                                "distance": 0.0,
                                "duration": 0.0,
                                "type": 10,
                                "instruction": "Llegada a su destino",
                                "name": "-",
                                "way_points": [2, 2],
                            },
                        ],
                    }
                ],
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[-63.18212, -17.78345], [-63.1830, -17.7800], [-63.19555, -17.76201]],
            },
        }
    ],
}

RESPUESTA_LUGARES = {
    "features": [
        {
            "geometry": {"coordinates": [-63.1975, -17.7676]},
            "properties": {
                "name": "Cristo Redentor",
                "label": "Cristo Redentor, Santa Cruz de la Sierra, Santa Cruz, Bolivia",
            },
        }
    ]
}


@pytest.fixture(autouse=True)
def limpiar_caches():
    service.cache_rutas.limpiar()
    service.cache_lugares.limpiar()
    yield
    service.cache_rutas.limpiar()
    service.cache_lugares.limpiar()


@pytest.fixture
def ors(monkeypatch):
    """Activa la navegación con un ORS simulado; devuelve la lista de pedidos recibidos.

    `ors.responder = (estado, cuerpo)` cambia la respuesta; `ors.error = excepción` la hace fallar.
    """

    class Simulado:
        responder: tuple[int, dict | str] | None = None
        error: Exception | None = None
        pedidos: list[httpx.Request] = []

    simulado = Simulado()
    simulado.pedidos = []

    def manejar(pedido: httpx.Request) -> httpx.Response:
        simulado.pedidos.append(pedido)
        if simulado.error is not None:
            raise simulado.error
        if simulado.responder is not None:
            estado, cuerpo = simulado.responder
        elif pedido.url.path.startswith("/v2/directions"):
            estado, cuerpo = 200, RESPUESTA_RUTA
        else:
            estado, cuerpo = 200, RESPUESTA_LUGARES
        if isinstance(cuerpo, str):
            return httpx.Response(estado, text=cuerpo)
        return httpx.Response(estado, json=cuerpo)

    monkeypatch.setattr(settings, "navegacion_proveedor", "ors")
    monkeypatch.setattr(settings, "ors_api_key", "clave-de-prueba")
    monkeypatch.setattr(
        proveedor,
        "_cliente",
        httpx.AsyncClient(
            base_url="https://ors.example.com",
            headers={"Authorization": "clave-de-prueba"},
            transport=httpx.MockTransport(manejar),
        ),
    )
    return simulado


async def registrar_conductor(cliente, numero: int = 1) -> dict:
    respuesta = await cliente.post(
        "/api/v1/auth/registro",
        json={
            "nombre": f"Conductor {numero}",
            "email": f"conductor{numero}@empresa.example.com",
            "telefono": "72000000",
            "contrasenia": "Clave1234",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# --- Traducción (puras) -----------------------------------------------------


def test_traduce_la_ruta_con_trazado_y_pasos():
    ruta = proveedor.ruta_desde_ors(RESPUESTA_RUTA)
    assert ruta.distancia_m == 3120.4
    assert ruta.duracion_s == 410.2
    # ORS da [lon, lat]
    assert (ruta.trazado[0].lat, ruta.trazado[0].lon) == (-17.78345, -63.18212)
    assert [p.tipo for p in ruta.pasos] == [TipoPaso.salida, TipoPaso.entrar_rotonda, TipoPaso.llegada]
    assert ruta.pasos[0].calle == "Calle Junín"
    assert ruta.pasos[0].instruccion == "Diríjase al norte por Calle Junín"
    # "-" es una vía sin nombre
    assert ruta.pasos[1].calle is None
    assert ruta.pasos[1].salida_rotonda == 2
    assert (ruta.pasos[1].inicio, ruta.pasos[1].fin) == (1, 2)


def test_ruta_con_origen_igual_al_destino_tiene_resumen_vacio():
    datos = json.loads(json.dumps(RESPUESTA_RUTA))
    datos["features"][0]["properties"]["summary"] = {}
    ruta = proveedor.ruta_desde_ors(datos)
    assert ruta.distancia_m == 0
    assert ruta.duracion_s == 0


def test_tipos_de_paso():
    assert proveedor.tipo_de_paso(0) == TipoPaso.girar_izquierda
    assert proveedor.tipo_de_paso(9) == TipoPaso.media_vuelta
    assert proveedor.tipo_de_paso(13) == TipoPaso.mantener_derecha
    # Un código desconocido no rompe la ruta
    assert proveedor.tipo_de_paso(99) == TipoPaso.seguir_recto


def test_traduce_los_lugares():
    lugares = proveedor.lugares_desde_ors(RESPUESTA_LUGARES)
    assert len(lugares) == 1
    assert lugares[0].nombre == "Cristo Redentor"
    assert lugares[0].direccion.endswith("Bolivia")
    assert (lugares[0].lat, lugares[0].lon) == (-17.7676, -63.1975)


def test_los_lugares_se_limitan_a_cinco():
    datos = {"features": RESPUESTA_LUGARES["features"] * 8}
    assert len(proveedor.lugares_desde_ors(datos)) == proveedor.MAXIMO_LUGARES


# --- Caché ------------------------------------------------------------------


def test_cache_vence_y_descarta_la_menos_usada():
    ahora = [0.0]
    cache = CacheTTL(ttl_s=10, maximo=2, reloj=lambda: ahora[0])
    cache.guardar("a", 1)
    cache.guardar("b", 2)
    assert cache.obtener("a") == 1  # "a" pasa a ser la más reciente
    cache.guardar("c", 3)  # descarta "b"
    assert cache.obtener("b") is None
    assert cache.obtener("c") == 3
    ahora[0] = 10.0
    assert cache.obtener("a") is None
    assert len(cache) == 1


def test_clave_de_la_ruta_redondea_a_unos_metros():
    from app.navegacion.schemas import Coordenada

    a = service.clave_ruta(Coordenada(lat=-17.78341, lon=-63.18212), Coordenada(lat=-17.762, lon=-63.1955))
    b = service.clave_ruta(Coordenada(lat=-17.78338, lon=-63.182118), Coordenada(lat=-17.762, lon=-63.1955))
    assert a == b


# --- Endpoints con ORS simulado ---------------------------------------------


async def test_ruta_y_pedido_a_ors(cliente, ors):
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["distancia_m"] == 3120.4
    assert len(cuerpo["trazado"]) == 3
    assert cuerpo["pasos"][1]["tipo"] == "entrar_rotonda"

    pedido = ors.pedidos[0]
    assert pedido.url.path == "/v2/directions/driving-car/geojson"
    assert pedido.headers["Authorization"] == "clave-de-prueba"
    enviado = json.loads(pedido.content)
    assert enviado["language"] == "es"
    assert enviado["instructions"] is True
    # [lon, lat] redondeados
    assert enviado["coordinates"][0] == [-63.1821, -17.7834]


async def test_ruta_repetida_sale_de_la_cache(cliente, ors):
    sesion = await registrar_conductor(cliente)
    for _ in range(3):
        respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
        assert respuesta.status_code == 200
    assert len(ors.pedidos) == 1


async def test_lugares_solo_bolivia_con_foco_en_santa_cruz(cliente, ors):
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(LUGARES, params={"q": "  Cristo   Redentor "}, headers=cabecera(sesion))
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["lugares"][0]["nombre"] == "Cristo Redentor"

    params = ors.pedidos[0].url.params
    assert ors.pedidos[0].url.path == "/geocode/search"
    assert params["text"] == "Cristo Redentor"
    assert params["boundary.country"] == "BO"
    assert float(params["focus.point.lat"]) == proveedor.FOCO_LAT
    assert float(params["focus.point.lon"]) == proveedor.FOCO_LON
    assert params["lang"] == "es"
    # La clave no va en la URL
    assert "api_key" not in params

    # Misma búsqueda con otras mayúsculas: desde la caché
    await cliente.get(LUGARES, params={"q": "cristo redentor"}, headers=cabecera(sesion))
    assert len(ors.pedidos) == 1


async def test_lugares_sin_resultados(cliente, ors):
    ors.responder = (200, {"features": []})
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(LUGARES, params={"q": "zzzzzz"}, headers=cabecera(sesion))
    assert respuesta.status_code == 200
    assert respuesta.json() == {"lugares": []}


async def test_lugares_texto_corto_o_largo_es_422(cliente, ors):
    sesion = await registrar_conductor(cliente)
    for q in ("  ab  ", "x" * 101):
        respuesta = await cliente.get(LUGARES, params={"q": q}, headers=cabecera(sesion))
        assert respuesta.status_code == 422
        assert respuesta.json()["errores"][0]["campo"] == "q"
    assert ors.pedidos == []


async def test_ruta_con_coordenadas_invalidas_es_422(cliente, ors):
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(
        RUTA, params=PARAMETROS_RUTA | {"destino_lat": 95}, headers=cabecera(sesion)
    )
    assert respuesta.status_code == 422
    assert ors.pedidos == []


@pytest.mark.parametrize(
    ("estado", "cuerpo", "esperado", "codigo"),
    [
        (404, {"error": {"code": 2010, "message": "Could not find routable point"}}, 404, "sin_ruta"),
        (404, {"error": {"code": 2009, "message": "Route could not be found"}}, 404, "sin_ruta"),
        (400, {"error": {"code": 2004, "message": "Request parameters exceed limits"}}, 404, "sin_ruta"),
        (429, {"error": "Rate Limit Exceeded"}, 503, "sin_cuota"),
        (403, {"error": "Access to this API has been disallowed"}, 503, "proveedor_caido"),
        (502, "Bad Gateway", 503, "proveedor_caido"),
    ],
)
async def test_errores_del_proveedor(cliente, ors, estado, cuerpo, esperado, codigo):
    ors.responder = (estado, cuerpo)
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert respuesta.status_code == esperado
    assert respuesta.json()["codigo"] == codigo
    # Los errores no se guardan: el siguiente intento vuelve a consultar
    await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert len(ors.pedidos) == 2


async def test_destino_lejano_tiene_su_mensaje(cliente, ors):
    ors.responder = (400, {"error": {"code": 2004, "message": "exceed"}})
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.get(RUTA, params=PARAMETROS_RUTA, headers=cabecera(sesion))
    assert respuesta.json()["detail"] == proveedor.DESTINO_LEJANO


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("tiempo"), httpx.ConnectError("sin red")]
)
async def test_proveedor_sin_respuesta(cliente, ors, error):
    ors.error = error
    sesion = await registrar_conductor(cliente)
    for url, params in ((RUTA, PARAMETROS_RUTA), (LUGARES, {"q": "Cristo"})):
        respuesta = await cliente.get(url, params=params, headers=cabecera(sesion))
        assert respuesta.status_code == 503
        assert respuesta.json() == {"detail": proveedor.PROVEEDOR_CAIDO, "codigo": "proveedor_caido"}


async def test_sin_clave_queda_desactivada_sin_llamar(cliente, ors, monkeypatch):
    monkeypatch.setattr(settings, "ors_api_key", "")
    sesion = await registrar_conductor(cliente)
    for url, params in ((RUTA, PARAMETROS_RUTA), (LUGARES, {"q": "Cristo"})):
        respuesta = await cliente.get(url, params=params, headers=cabecera(sesion))
        assert respuesta.status_code == 503
        assert respuesta.json() == {
            "detail": service.NAVEGACION_DESACTIVADA,
            "codigo": "navegacion_desactivada",
        }
    assert ors.pedidos == []


# --- Acceso -----------------------------------------------------------------


async def test_solo_conductores_autenticados(cliente, ors, registrar_empresa):
    for url, params in ((RUTA, PARAMETROS_RUTA), (LUGARES, {"q": "Cristo"})):
        assert (await cliente.get(url, params=params)).status_code == 401
    admin = await registrar_empresa(1)
    for url, params in ((RUTA, PARAMETROS_RUTA), (LUGARES, {"q": "Cristo"})):
        assert (await cliente.get(url, params=params, headers=cabecera(admin))).status_code == 403
    assert ors.pedidos == []


# --- Destino del recorrido --------------------------------------------------


INICIO = {"lat_inicio": -17.78345, "lon_inicio": -63.18212}
DESTINO = {"nombre": "  Cristo Redentor  ", "lat": -17.7676, "lon": -63.1975}


async def test_recorrido_guarda_el_destino(cliente, db):
    sesion = await registrar_conductor(cliente)
    creado = await cliente.post(
        "/api/v1/recorridos", json=INICIO | {"destino": DESTINO}, headers=cabecera(sesion)
    )
    assert creado.status_code == 201, creado.text
    esperado = {"nombre": "Cristo Redentor", "lat": -17.7676, "lon": -63.1975}
    assert creado.json()["destino"] == esperado

    activo = await cliente.get("/api/v1/recorridos/activo", headers=cabecera(sesion))
    assert activo.json()["destino"] == esperado

    recorrido_id = creado.json()["id"]
    # El viaje empezó hace 15 minutos
    await db.execute(
        text("UPDATE recorridos SET fecha_inicio = now() - interval '15 minutes' WHERE id = :id"),
        {"id": recorrido_id},
    )
    await db.commit()
    final = await cliente.patch(
        f"/api/v1/recorridos/{recorrido_id}/finalizar",
        json={
            "fecha_fin": datetime.now(UTC).isoformat(),
            "distancia_m": 3200,
            "duracion_s": 600,
            "velocidad_maxima_kmh": 50,
            "velocidad_promedio_kmh": 20,
            "lat_fin": -17.7676,
            "lon_fin": -63.1975,
        },
        headers=cabecera(sesion),
    )
    assert final.status_code == 200, final.text
    assert final.json()["destino"] == esperado

    detalle = await cliente.get(f"/api/v1/recorridos/{recorrido_id}", headers=cabecera(sesion))
    assert detalle.status_code == 200
    assert detalle.json()["destino"] == esperado


async def test_recorrido_sin_destino(cliente):
    sesion = await registrar_conductor(cliente)
    creado = await cliente.post("/api/v1/recorridos", json=INICIO, headers=cabecera(sesion))
    assert creado.status_code == 201
    assert creado.json()["destino"] is None


@pytest.mark.parametrize(
    ("destino", "campo"),
    [
        ({"lat": -17.7, "lon": -63.1}, "destino.nombre"),
        ({"nombre": "Casa", "lon": -63.1}, "destino.lat"),
        ({"nombre": "   ", "lat": -17.7, "lon": -63.1}, "destino.nombre"),
        ({"nombre": "x" * 151, "lat": -17.7, "lon": -63.1}, "destino.nombre"),
        ({"nombre": "Casa", "lat": -91, "lon": -63.1}, "destino.lat"),
        ({"nombre": "Casa", "lat": -17.7, "lon": 181}, "destino.lon"),
    ],
)
async def test_destino_invalido_es_422(cliente, destino, campo):
    sesion = await registrar_conductor(cliente)
    respuesta = await cliente.post(
        "/api/v1/recorridos", json=INICIO | {"destino": destino}, headers=cabecera(sesion)
    )
    assert respuesta.status_code == 422
    assert [e["campo"] for e in respuesta.json()["errores"]] == [campo]
