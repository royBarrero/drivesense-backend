"""Recomendaciones redactadas por la IA (HU-18 y HU-19), sin base de datos ni llamadas reales a la API.

`ia.redactar` se reemplaza por un doble: aquí se prueba qué se envía, cómo se validan las respuestas
y que, sin IA o con una respuesta que no sirve, quedan las plantillas.
"""

import json

import pytest

import app.models  # noqa: F401  (registra los modelos: `filas` crea instancias del ORM)
from app.core.config import settings
from app.puntaje.service import EventoPuntaje, calcular_puntaje
from app.recomendaciones import ia
from app.recomendaciones.ia import ContextoViaje, EventoViaje, RespuestaIA, TextoIA, mensaje_de, validar
from app.recomendaciones.models import TipoRecomendacion
from app.recomendaciones.service import filas, filas_con_ia
from app.telemetria.models import TipoEvento

FRENADA = EventoViaje(TipoEvento.frenada_brusca, 52, 3.4)
GIRO = EventoViaje(TipoEvento.giro_agresivo, 38, 4.1)
EXCESO = EventoViaje(TipoEvento.exceso_velocidad, 63, 2.5, duracion_s=45, velocidad_maxima_kmh=71)
DISTANCIA_M, DURACION_S = 7400, 900


def puntaje_de(eventos):
    return calcular_puntaje(DISTANCIA_M, DURACION_S, (EventoPuntaje(e.tipo, e.duracion_s) for e in eventos))


async def generar(eventos, recorrido_id=7):
    return await filas_con_ia(recorrido_id, puntaje_de(eventos), eventos, DISTANCIA_M, DURACION_S, 30, 71)


def contexto(*consejos) -> ContextoViaje:
    return ContextoViaje(DISTANCIA_M, DURACION_S, 30, 71, puntaje_de([]), [], list(consejos))


@pytest.fixture
def doble(monkeypatch):
    """Reemplaza `ia.redactar`: guarda el contexto recibido y devuelve lo que se le indique."""
    llamadas: list[ContextoViaje] = []
    respuesta: dict = {"valor": None}

    async def redactar(ctx):
        llamadas.append(ctx)
        return respuesta["valor"]

    monkeypatch.setattr(ia, "redactar", redactar)
    return llamadas, respuesta


async def test_con_ia_reemplaza_textos_y_conserva_tipo_y_orden(doble):
    llamadas, respuesta = doble
    respuesta["valor"] = {
        0: TextoIA(orden=0, titulo="Suelta antes el acelerador", texto="Frenaste fuerte una vez a 52 km/h."),
        1: TextoIA(orden=1, titulo="Curvas con calma", texto="Tomaste una curva a 38 km/h."),
    }
    eventos = [FRENADA, GIRO]
    resultado = await generar(eventos)
    plantillas = filas(7, puntaje_de(eventos), (EventoPuntaje(e.tipo, e.duracion_s) for e in eventos))

    assert [(f.orden, f.tipo) for f in resultado] == [(f.orden, f.tipo) for f in plantillas]
    assert [f.titulo for f in resultado] == ["Suelta antes el acelerador", "Curvas con calma"]
    assert all(f.generada_por_ia for f in resultado)
    # Se piden los mismos consejos que eligen las reglas, con su cantidad de eventos
    assert llamadas[0].consejos == [(f.orden, f.tipo, 1) for f in plantillas]


async def test_sin_respuesta_quedan_las_plantillas(doble):
    eventos = [FRENADA, GIRO]
    resultado = await generar(eventos)
    plantillas = filas(7, puntaje_de(eventos), (EventoPuntaje(e.tipo, e.duracion_s) for e in eventos))

    assert [(f.titulo, f.texto) for f in resultado] == [(f.titulo, f.texto) for f in plantillas]
    assert not any(f.generada_por_ia for f in resultado)


async def test_fila_sin_texto_de_la_ia_conserva_su_plantilla(doble):
    _, respuesta = doble
    respuesta["valor"] = {0: TextoIA(orden=0, titulo="Título IA", texto="Texto IA.")}
    resultado = await generar([FRENADA, GIRO])

    assert [f.generada_por_ia for f in resultado] == [True, False]
    assert resultado[1].titulo != "Título IA"


async def test_viaje_sin_eventos_pide_el_consejo_positivo(doble):
    llamadas, _ = doble
    resultado = await generar([])

    assert [f.tipo for f in resultado] == [TipoRecomendacion.sin_eventos]
    assert llamadas[0].consejos == [(0, TipoRecomendacion.sin_eventos, 0)]


async def test_sin_clave_no_llama_a_la_api(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    def no_llamar():
        raise AssertionError("no debe crear el cliente sin clave")

    monkeypatch.setattr(ia, "_obtener_cliente", no_llamar)
    assert await ia.redactar(contexto((0, TipoRecomendacion.sin_eventos, 0))) is None


def test_mensaje_sin_datos_personales_ni_ubicacion():
    ctx = ContextoViaje(
        DISTANCIA_M, DURACION_S, 30.4, 71.2, puntaje_de([EXCESO]), [EXCESO],
        [(0, TipoRecomendacion.exceso_velocidad, 1)],
    )
    datos = json.loads(mensaje_de(ctx))

    assert datos["viaje"]["distancia_km"] == 7.4
    assert datos["viaje"]["duracion_min"] == 15
    assert datos["eventos"] == [
        {"tipo": "exceso_velocidad", "velocidad_kmh": 63, "intensidad": 2.5, "duracion_s": 45, "velocidad_maxima_kmh": 71}
    ]
    assert datos["consejos_a_escribir"] == [{"orden": 0, "tipo": "exceso_velocidad", "cantidad_de_eventos": 1}]
    texto = mensaje_de(ctx)
    for prohibido in ("lat", "lon", "nombre", "email"):
        assert prohibido not in texto


def test_validar_exige_los_mismos_ordenes():
    ctx = contexto((0, TipoRecomendacion.frenada_brusca, 1), (1, TipoRecomendacion.giro_agresivo, 1))
    solo_uno = RespuestaIA(recomendaciones=[TextoIA(orden=0, titulo="A", texto="B")])
    otro_orden = RespuestaIA(
        recomendaciones=[TextoIA(orden=0, titulo="A", texto="B"), TextoIA(orden=5, titulo="C", texto="D")]
    )
    assert validar(solo_uno, ctx) is None
    assert validar(otro_orden, ctx) is None


def test_validar_descarta_textos_vacios_o_demasiado_largos():
    ctx = contexto(
        (0, TipoRecomendacion.frenada_brusca, 1),
        (1, TipoRecomendacion.giro_agresivo, 1),
        (2, TipoRecomendacion.exceso_velocidad, 1),
    )
    respuesta = RespuestaIA(
        recomendaciones=[
            TextoIA(orden=0, titulo="  Frena suave  ", texto=" Bien. "),
            TextoIA(orden=1, titulo="x" * 61, texto="Texto"),
            TextoIA(orden=2, titulo="Título", texto="   "),
        ]
    )
    validos = validar(respuesta, ctx)

    assert list(validos) == [0]
    assert (validos[0].titulo, validos[0].texto) == ("Frena suave", "Bien.")
