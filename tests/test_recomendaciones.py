"""Recomendaciones del recorrido (HU-18 y HU-19): elección, textos y secundarias, sin base de datos."""

import re

import pytest

import app.models  # noqa: F401  (registra los modelos: `filas` crea instancias del ORM)
from app.puntaje.service import EventoPuntaje, Puntaje, calcular_puntaje
from app.recomendaciones.models import Recomendacion, TipoRecomendacion
from app.recomendaciones.service import (
    MAXIMO_SECUNDARIAS,
    PLANTILLAS,
    faltantes,
    filas,
    formatear_duracion,
    generar_recomendaciones,
    ordenar_tipos,
)
from app.telemetria.models import TipoEvento

FRENADA = EventoPuntaje(TipoEvento.frenada_brusca)
ACELERACION = EventoPuntaje(TipoEvento.aceleracion_severa)
GIRO = EventoPuntaje(TipoEvento.giro_agresivo)


def exceso(segundos: float) -> EventoPuntaje:
    return EventoPuntaje(TipoEvento.exceso_velocidad, segundos)


def puntaje(frenadas=100, aceleraciones=100, giros=100, velocidad=100) -> Puntaje:
    return Puntaje(
        drivescore=0,
        frenadas=frenadas,
        aceleraciones=aceleraciones,
        giros=giros,
        velocidad=velocidad,
    )


def conteo(*eventos: EventoPuntaje) -> dict[TipoEvento, int]:
    resultado: dict[TipoEvento, int] = {}
    for e in eventos:
        resultado[e.tipo] = resultado.get(e.tipo, 0) + 1
    return resultado


# --- Qué tipo se recomienda ---------------------------------------------------


def test_elige_la_categoria_con_menor_puntaje():
    tipos = ordenar_tipos(puntaje(frenadas=70, giros=64), conteo(FRENADA, GIRO, GIRO))
    assert tipos == [TipoEvento.giro_agresivo, TipoEvento.frenada_brusca]


def test_empate_prioriza_el_de_mayor_riesgo():
    # Mismo puntaje en las cuatro: exceso > frenada > giro > aceleración
    p = puntaje(frenadas=80, aceleraciones=80, giros=80, velocidad=80)
    tipos = ordenar_tipos(p, conteo(ACELERACION, GIRO, FRENADA, exceso(30)))
    assert tipos == [
        TipoEvento.exceso_velocidad,
        TipoEvento.frenada_brusca,
        TipoEvento.giro_agresivo,
        TipoEvento.aceleracion_severa,
    ]


def test_empate_giro_y_aceleracion_gana_el_giro():
    tipos = ordenar_tipos(puntaje(aceleraciones=82, giros=82), conteo(ACELERACION, GIRO))
    assert tipos[0] == TipoEvento.giro_agresivo


def test_evento_con_categoria_en_100_por_redondeo_igual_se_recomienda():
    # 1 s de exceso en una hora: la velocidad redondea a 100
    p = calcular_puntaje(40_000, 3600, [exceso(1)])
    assert p.velocidad == 100
    assert ordenar_tipos(p, conteo(exceso(1))) == [TipoEvento.exceso_velocidad]


def test_los_tipos_en_100_van_despues_de_los_que_restaron():
    tipos = ordenar_tipos(puntaje(frenadas=70), conteo(FRENADA, exceso(1)))
    assert tipos == [TipoEvento.frenada_brusca, TipoEvento.exceso_velocidad]


def test_con_el_puntaje_real_coincide_con_el_ejemplo_de_la_formula():
    # 7,4 km, 1 frenada y 1 giro → frenadas 70, giros 76: la frenada restó más
    eventos = [FRENADA, GIRO]
    p = calcular_puntaje(7400, 1112, eventos)
    principal = generar_recomendaciones(1, p, eventos)[0]
    assert principal.tipo == TipoRecomendacion.frenada_brusca


# --- Lista y textos -----------------------------------------------------------


def test_sin_eventos_solo_el_mensaje_positivo():
    recomendaciones = generar_recomendaciones(5, puntaje(), [])
    assert len(recomendaciones) == 1
    assert recomendaciones[0].tipo == TipoRecomendacion.sin_eventos


def test_devuelve_una_por_tipo_con_eventos_en_orden():
    # Lista lista para las secundarias (HU-19)
    eventos = [FRENADA, GIRO, GIRO]
    recomendaciones = generar_recomendaciones(1, puntaje(frenadas=70, giros=52), eventos)
    assert [r.tipo for r in recomendaciones] == [
        TipoRecomendacion.giro_agresivo,
        TipoRecomendacion.frenada_brusca,
    ]


def test_un_evento_usa_el_texto_de_una_vez():
    r = generar_recomendaciones(0, puntaje(frenadas=70), [FRENADA])[0]
    assert r.texto == PLANTILLAS[TipoRecomendacion.frenada_brusca][0].texto_una


def test_varios_eventos_mencionan_la_cantidad():
    r = generar_recomendaciones(0, puntaje(frenadas=40), [FRENADA] * 3)[0]
    assert r.texto.startswith("Frenaste bruscamente 3 veces.")


def test_el_exceso_menciona_el_tiempo_total():
    r = generar_recomendaciones(0, puntaje(velocidad=90), [exceso(40), exceso(50)])[0]
    assert "2 veces, en total 2 minutos" in r.texto
    una = generar_recomendaciones(0, puntaje(velocidad=95), [exceso(12)])[0]
    assert "durante 12 segundos" in una.texto


def test_la_variante_sale_del_id_del_recorrido():
    titulos = [
        generar_recomendaciones(i, puntaje(frenadas=70), [FRENADA])[0].titulo for i in range(4)
    ]
    variantes = PLANTILLAS[TipoRecomendacion.frenada_brusca]
    assert titulos == [variantes[0].titulo, variantes[1].titulo, variantes[2].titulo,
                       variantes[0].titulo]


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [(0.4, "1 segundo"), (1, "1 segundo"), (45, "45 segundos"), (59.4, "59 segundos"),
     (60, "1 minuto"), (89, "1 minuto"), (90, "2 minutos"), (600, "10 minutos")],
)
def test_formatear_duracion(segundos, texto):
    assert formatear_duracion(segundos) == texto


@pytest.mark.parametrize("tipo", list(TipoRecomendacion))
def test_plantillas_completas(tipo):
    variantes = PLANTILLAS[tipo]
    assert len(variantes) == 3
    for plantilla in variantes:
        assert len(plantilla.titulo) <= 40
        for texto in (plantilla.texto_una, plantilla.texto_varias):
            armado = texto.format(n=3, duracion="2 minutos")
            assert len(armado) <= 300  # largo de la columna
            assert not re.search(r"[{}]", armado)
        # La versión de varias veces menciona la cantidad
        if tipo != TipoRecomendacion.sin_eventos:
            assert "{n}" in plantilla.texto_varias


# --- Secundarias (HU-19) ------------------------------------------------------


def test_filas_principal_y_secundarias_en_orden():
    eventos = [FRENADA, GIRO, GIRO, ACELERACION]
    p = puntaje(frenadas=70, giros=52, aceleraciones=76)
    resultado = filas(1, p, eventos)
    assert [(r.orden, r.tipo) for r in resultado] == [
        (0, TipoRecomendacion.giro_agresivo),
        (1, TipoRecomendacion.frenada_brusca),
        (2, TipoRecomendacion.aceleracion_severa),
    ]
    assert all(r.recorrido_id == 1 for r in resultado)


def test_con_los_cuatro_tipos_solo_dos_secundarias():
    eventos = [FRENADA, GIRO, ACELERACION, exceso(60)]
    p = puntaje(frenadas=70, giros=76, aceleraciones=76, velocidad=89)
    resultado = filas(1, p, eventos)
    assert len(resultado) == 1 + MAXIMO_SECUNDARIAS
    assert [r.tipo for r in resultado] == [
        TipoRecomendacion.frenada_brusca,
        # Giro y aceleración empatan en 76: gana el giro (más riesgo)
        TipoRecomendacion.giro_agresivo,
        TipoRecomendacion.aceleracion_severa,
    ]


def test_un_solo_tipo_sin_secundarias():
    resultado = filas(1, puntaje(frenadas=40), [FRENADA] * 3)
    assert [r.tipo for r in resultado] == [TipoRecomendacion.frenada_brusca]


def test_sin_eventos_solo_la_positiva():
    resultado = filas(1, puntaje(), [])
    assert [(r.orden, r.tipo) for r in resultado] == [(0, TipoRecomendacion.sin_eventos)]


def test_las_secundarias_no_repiten_tipo_ni_texto_de_la_principal():
    eventos = [FRENADA, GIRO, ACELERACION, exceso(60)]
    p = puntaje(frenadas=70, giros=76, aceleraciones=76, velocidad=89)
    for recorrido_id in range(6):
        principal, *secundarias = filas(recorrido_id, p, eventos)
        assert all(s.tipo != principal.tipo for s in secundarias)
        assert all(s.texto != principal.texto for s in secundarias)
        assert len({s.tipo for s in secundarias}) == len(secundarias)


def _fila(orden: int, tipo: TipoRecomendacion) -> Recomendacion:
    return Recomendacion(recorrido_id=1, orden=orden, tipo=tipo, titulo="t", texto="x")


def test_faltantes_completa_un_viaje_de_hu18():
    # Guardada solo la principal: faltan las secundarias
    guardadas = [_fila(0, TipoRecomendacion.frenada_brusca)]
    esperadas = [
        _fila(0, TipoRecomendacion.frenada_brusca),
        _fila(1, TipoRecomendacion.giro_agresivo),
    ]
    assert [r.orden for r in faltantes(guardadas, esperadas)] == [1]


def test_faltantes_no_toca_las_guardadas_ni_repite_la_principal():
    # La principal guardada es de otro tipo que la calculada ahora
    guardadas = [_fila(0, TipoRecomendacion.giro_agresivo)]
    esperadas = [
        _fila(0, TipoRecomendacion.frenada_brusca),
        _fila(1, TipoRecomendacion.giro_agresivo),
        _fila(2, TipoRecomendacion.aceleracion_severa),
    ]
    assert [(r.orden, r.tipo) for r in faltantes(guardadas, esperadas)] == [
        (2, TipoRecomendacion.aceleracion_severa)
    ]


def test_faltantes_vacia_si_ya_estan_todas():
    esperadas = [_fila(0, TipoRecomendacion.sin_eventos)]
    assert faltantes(list(esperadas), esperadas) == []
    # Viaje anterior a HU-18: se generan todas
    assert faltantes([], esperadas) == esperadas
