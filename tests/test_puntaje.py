"""Fórmula del DriveScore (HU-15), resumen del histórico (HU-17) y comparativo (HU-20):
sin base de datos."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.exceptions import RequestValidationError

from app.puntaje.service import (
    Agrupacion,
    Calificacion,
    Cambio,
    EventoPuntaje,
    PeriodoComparativo,
    Puntaje,
    Resultado,
    ViajePuntuado,
    agrupacion_para,
    agrupar_puntos,
    calcular_eventos_por_10km,
    calcular_puntaje,
    calificar,
    comparar,
    comparar_periodos,
    destacadas,
    inicio_anterior,
    mejor_y_peor,
    promedio_desde_sumas,
    promedio_ponderado,
    resumir_historico,
    resumir_periodo,
    validar_inicio,
)
from app.telemetria.models import TipoEvento

FRENADA = EventoPuntaje(TipoEvento.frenada_brusca)
ACELERACION = EventoPuntaje(TipoEvento.aceleracion_severa)
GIRO = EventoPuntaje(TipoEvento.giro_agresivo)


def exceso(segundos: float) -> EventoPuntaje:
    return EventoPuntaje(TipoEvento.exceso_velocidad, segundos)


def test_sin_eventos_todo_100():
    assert calcular_puntaje(7_400, 1_100, []) == Puntaje(100, 100, 100, 100, 100)


def test_viaje_de_ejemplo_da_86():
    # 7,4 km, 1 frenada y 1 giro, sin exceso
    puntaje = calcular_puntaje(7_400, 1_112, [FRENADA, GIRO])
    assert puntaje == Puntaje(
        drivescore=86, frenadas=70, aceleraciones=100, giros=76, velocidad=100
    )


def test_viaje_corto_cuenta_como_5_km():
    # 2 km y 5 km dan lo mismo: 1 frenada en 5 km resta 22 × 2 = 44
    corto = calcular_puntaje(2_000, 300, [FRENADA])
    assert corto == calcular_puntaje(5_000, 300, [FRENADA])
    assert corto.frenadas == 56


def test_viaje_largo_diluye_los_eventos():
    # 1 aceleración en 20 km: resta 18 × 0,5 = 9
    assert calcular_puntaje(20_000, 1_800, [ACELERACION]).aceleraciones == 91


def test_velocidad_segun_el_porcentaje_del_tiempo_en_exceso():
    # 10 % del viaje en exceso: 100 − 2 × 10
    puntaje = calcular_puntaje(10_000, 1_000, [exceso(60), exceso(40)])
    assert puntaje.velocidad == 80
    assert puntaje.drivescore == 94  # 0,3 × 80 + 70


def test_exceso_mayor_que_el_viaje_da_0_en_velocidad():
    assert calcular_puntaje(10_000, 600, [exceso(900)]).velocidad == 0


def test_muchos_eventos_no_bajan_de_0():
    puntaje = calcular_puntaje(5_000, 600, [FRENADA] * 20 + [GIRO] * 20 + [exceso(600)])
    assert puntaje.frenadas == 0
    assert puntaje.giros == 0
    assert puntaje.velocidad == 0
    assert puntaje.drivescore == 20  # solo aceleraciones: 0,2 × 100


def test_redondea_0_5_hacia_arriba():
    # 7 s de exceso en 400 s (1,75 %): 100 − 3,5 = 96,5 → 97 (round() daría 96)
    assert calcular_puntaje(10_000, 400, [exceso(7)]).velocidad == 97


# --- Histórico (HU-17) -------------------------------------------------------

DESDE = datetime(2026, 9, 5, 4, tzinfo=UTC)  # medianoche en UTC−4
HASTA = DESDE + timedelta(days=30)


def viaje(
    id: int,
    dia: float,
    drivescore: int,
    km: float = 5,
    frenadas: int = 100,
    aceleraciones: int = 100,
    giros: int = 100,
    velocidad: int = 100,
) -> ViajePuntuado:
    return ViajePuntuado(
        id, DESDE + timedelta(days=dia), km * 1000, drivescore,
        frenadas, aceleraciones, giros, velocidad,
    )


def test_promedio_ponderado_por_distancia():
    # 20 km con 90 y 5 km con 60: (90 × 20 + 60 × 5) / 25 = 84 (el simple daría 75)
    assert promedio_ponderado([viaje(1, 0, 90, km=20), viaje(2, 1, 60, km=5)]) == 84
    assert promedio_ponderado([]) is None


def test_promedio_ponderado_redondea_0_5_hacia_arriba():
    assert promedio_ponderado([viaje(1, 0, 85), viaje(2, 1, 86)]) == 86  # 85,5


def test_promedio_total_desde_las_sumas_de_la_bd():
    assert promedio_desde_sumas(90 * 20_000 + 60 * 5_000, 25_000) == 84
    assert promedio_desde_sumas(None, None) is None


def test_calificacion_en_los_bordes():
    assert calificar(90) == Calificacion.excelente
    assert calificar(89) == Calificacion.muy_bueno
    assert calificar(75) == Calificacion.muy_bueno
    assert calificar(74) == Calificacion.regular
    assert calificar(60) == Calificacion.regular
    assert calificar(59) == Calificacion.riesgoso


def test_agrupacion_segun_la_duracion():
    assert agrupacion_para(DESDE, DESDE + timedelta(days=7)) == Agrupacion.viaje
    assert agrupacion_para(DESDE, DESDE + timedelta(days=30)) == Agrupacion.dia
    assert agrupacion_para(DESDE, DESDE + timedelta(days=90)) == Agrupacion.semana


def test_un_punto_por_viaje_en_orden():
    puntos = agrupar_puntos([viaje(2, 3, 70), viaje(1, 1, 90)], DESDE, Agrupacion.viaje)
    assert [(p.drivescore, p.viajes) for p in puntos] == [(90, 1), (70, 1)]
    assert puntos[0].fecha == DESDE + timedelta(days=1)


def test_un_punto_por_dia_con_viajes():
    viajes = [viaje(1, 2.1, 90, km=20), viaje(2, 2.9, 60), viaje(3, 5.5, 80)]
    puntos = agrupar_puntos(viajes, DESDE, Agrupacion.dia)
    assert [(p.fecha, p.drivescore, p.viajes) for p in puntos] == [
        (DESDE + timedelta(days=2), 84, 2),
        (DESDE + timedelta(days=5), 80, 1),
    ]


def test_un_punto_por_semana():
    viajes = [viaje(1, 0, 90), viaje(2, 6.9, 70), viaje(3, 7, 80), viaje(4, 20, 60)]
    puntos = agrupar_puntos(viajes, DESDE, Agrupacion.semana)
    assert [(p.fecha, p.drivescore, p.viajes) for p in puntos] == [
        (DESDE, 80, 2),
        (DESDE + timedelta(days=7), 80, 1),
        (DESDE + timedelta(days=14), 60, 1),
    ]


def test_mejor_y_peor_con_empate_gana_el_mas_reciente():
    mejor, peor = mejor_y_peor(
        [viaje(1, 0, 95), viaje(2, 1, 95), viaje(3, 2, 70), viaje(4, 3, 70), viaje(5, 4, 80)]
    )
    assert mejor.id == 2
    assert peor.id == 4


def test_con_un_solo_viaje_no_hay_peor():
    mejor, peor = mejor_y_peor([viaje(1, 0, 88)])
    assert mejor.id == 1
    assert peor is None
    assert mejor_y_peor([]) == (None, None)


def test_resumen_con_tendencia_y_categorias():
    actuales = [
        viaje(3, 1, 90, frenadas=80, aceleraciones=94, giros=85, velocidad=100),
        viaje(4, 2, 80, frenadas=78, aceleraciones=94, giros=85, velocidad=80),
    ]
    anteriores = [viaje(1, -5, 79, frenadas=75, aceleraciones=94, giros=82, velocidad=92)]
    h = resumir_historico(actuales, anteriores, DESDE, HASTA, viajes_totales=3, promedio_total=83)
    assert (h.viajes, h.viajes_totales, h.promedio_total) == (2, 3, 83)
    assert h.promedio == 85
    assert h.calificacion == Calificacion.muy_bueno
    assert h.tendencia == 6
    assert h.agrupacion == Agrupacion.dia
    assert [(c, p.promedio, p.diferencia) for c, p in h.categorias.items()] == [
        ("frenadas", 79, 4),
        ("aceleraciones", 94, 0),
        ("giros", 85, 3),
        ("velocidad", 90, -2),
    ]
    assert (h.mejor.id, h.peor.id) == (3, 4)


def test_sin_periodo_anterior_la_tendencia_es_nula():
    h = resumir_historico([viaje(1, 1, 90)], [], DESDE, HASTA, 1, 90)
    assert h.tendencia is None
    assert all(p.diferencia is None for p in h.categorias.values())


def test_periodo_sin_viajes():
    h = resumir_historico([], [viaje(1, -3, 80)], DESDE, HASTA, viajes_totales=5, promedio_total=81)
    assert (h.viajes, h.promedio, h.calificacion, h.tendencia) == (0, None, None, None)
    assert h.puntos == []
    assert h.categorias is None
    assert (h.mejor, h.peor) == (None, None)
    assert h.promedio_total == 81


# --- Comparativo (HU-20) -----------------------------------------------------

ZONA = timezone(timedelta(hours=-5))
LUNES = datetime(2026, 11, 9, tzinfo=ZONA)


def puntuado(
    id: int, dia: float, drivescore: int, km: float = 10, eventos: int = 0, **categorias: int
) -> ViajePuntuado:
    valores = {"frenadas": 100, "aceleraciones": 100, "giros": 100, "velocidad": 100} | categorias
    return ViajePuntuado(
        id, LUNES + timedelta(days=dia), km * 1000, drivescore,
        valores["frenadas"], valores["aceleraciones"], valores["giros"], valores["velocidad"],
        eventos,
    )


def comparativo(actuales, anteriores):
    return comparar_periodos(
        PeriodoComparativo.semana, actuales, anteriores,
        LUNES, LUNES + timedelta(days=3), LUNES - timedelta(days=7),
    )


def test_inicio_anterior_semana_y_mes():
    assert inicio_anterior(PeriodoComparativo.semana, LUNES) == datetime(2026, 11, 2, tzinfo=ZONA)
    marzo = datetime(2026, 3, 1, tzinfo=ZONA)
    assert inicio_anterior(PeriodoComparativo.mes, marzo) == datetime(2026, 2, 1, tzinfo=ZONA)
    enero = datetime(2027, 1, 1, tzinfo=ZONA)
    assert inicio_anterior(PeriodoComparativo.mes, enero) == datetime(2026, 12, 1, tzinfo=ZONA)


def test_valida_el_inicio_en_la_zona_del_telefono():
    validar_inicio(PeriodoComparativo.semana, LUNES)
    validar_inicio(PeriodoComparativo.mes, datetime(2026, 11, 1, tzinfo=ZONA))
    # El mismo instante en UTC son las 5:00: no es medianoche
    with pytest.raises(RequestValidationError):
        validar_inicio(PeriodoComparativo.semana, LUNES.astimezone(UTC))
    with pytest.raises(RequestValidationError):
        validar_inicio(PeriodoComparativo.semana, LUNES + timedelta(days=1))
    with pytest.raises(RequestValidationError):
        validar_inicio(PeriodoComparativo.mes, LUNES)


def test_eventos_por_10km_con_un_decimal():
    assert calcular_eventos_por_10km(3, 20_000) == 1.5
    assert calcular_eventos_por_10km(1, 30_000) == 0.3
    assert calcular_eventos_por_10km(1, 40_000) == 0.3  # 0,25 → 0,3
    assert calcular_eventos_por_10km(0, 0) is None


def test_resumen_del_periodo_ponderado_por_distancia():
    r = resumir_periodo(
        [puntuado(1, 0, 90, km=20, eventos=1, frenadas=80), puntuado(2, 1, 60, km=5, eventos=4)],
        LUNES, LUNES + timedelta(days=2),
    )
    assert (r.viajes, r.distancia_m, r.eventos) == (2, 25_000, 5)
    assert r.drivescore == 84
    assert r.calificacion == Calificacion.muy_bueno
    assert r.categorias["frenadas"] == 84  # (80 × 20 + 100 × 5) / 25
    assert r.eventos_por_10km == 2.0


def test_periodo_sin_viajes():
    r = resumir_periodo([], LUNES, LUNES + timedelta(days=1))
    assert (r.viajes, r.distancia_m, r.eventos) == (0, 0, 0)
    assert (r.drivescore, r.calificacion, r.categorias, r.eventos_por_10km) == (None,) * 4


def test_sentido_de_la_mejora():
    assert comparar(85, 80) == Cambio(5, Resultado.mejoro)
    assert comparar(78, 80) == Cambio(-2, Resultado.empeoro)
    assert comparar(80, 80) == Cambio(0, Resultado.igual)
    # Menos eventos por distancia es mejor
    assert comparar(0.8, 1.1, mayor_es_mejor=False) == Cambio(-0.3, Resultado.mejoro)
    assert comparar(1.1, 0.8, mayor_es_mejor=False) == Cambio(0.3, Resultado.empeoro)
    assert comparar(1.0, 1.0, mayor_es_mejor=False).resultado == Resultado.igual


def test_destacadas_con_empate_por_riesgo():
    def cambios(**d):
        return {c: comparar(d.get(c, 0), 0) for c in ("frenadas", "aceleraciones", "giros", "velocidad")}

    assert destacadas(cambios(frenadas=8, giros=8, aceleraciones=-3)) == ("frenadas", "aceleraciones")
    assert destacadas(cambios(aceleraciones=5, velocidad=5, giros=-4, frenadas=-4)) == (
        "velocidad", "frenadas",
    )
    assert destacadas(cambios()) == (None, None)
    assert destacadas(cambios(giros=2)) == ("giros", None)


def test_comparativo_completo():
    actuales = [
        puntuado(3, 0, 90, eventos=1, frenadas=90, giros=80),
        puntuado(4, 1, 86, eventos=1, frenadas=90, giros=80),
    ]
    anteriores = [
        puntuado(1, -6, 80, km=20, eventos=4, frenadas=70, giros=90),
        puntuado(2, -5, 80, eventos=2, frenadas=70, giros=90),
    ]
    c = comparativo(actuales, anteriores)
    assert c.comparable and c.minimo_viajes == 2
    assert c.drivescore == Cambio(8, Resultado.mejoro)
    assert c.categorias["frenadas"] == Cambio(20, Resultado.mejoro)
    assert c.categorias["giros"] == Cambio(-10, Resultado.empeoro)
    assert c.categorias["velocidad"] == Cambio(0, Resultado.igual)
    assert (c.mas_mejoro, c.mas_empeoro) == ("frenadas", "giros")
    # 2 eventos en 20 km (1,0) frente a 6 en 30 km (2,0)
    assert c.eventos_por_10km == Cambio(-1.0, Resultado.mejoro)
    assert (c.viajes, c.distancia_m) == (0, -10_000)
    assert c.anterior.desde == LUNES - timedelta(days=7)
    assert c.anterior.hasta == LUNES


def test_sin_el_minimo_de_viajes_no_compara():
    dos = [puntuado(1, 0, 90), puntuado(2, 1, 80)]
    for actuales, anteriores in (([dos[0]], dos), (dos, [dos[0]]), ([], [])):
        c = comparativo(actuales, anteriores)
        assert not c.comparable
        assert (c.drivescore, c.categorias, c.eventos_por_10km) == (None, None, None)
        assert (c.viajes, c.distancia_m, c.mas_mejoro, c.mas_empeoro) == (None,) * 4
    # Los periodos vienen igual: la app muestra cuántos viajes lleva cada uno
    c = comparativo([dos[0]], dos)
    assert (c.actual.viajes, c.anterior.viajes) == (1, 2)
