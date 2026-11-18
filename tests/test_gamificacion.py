"""Puntos, niveles y logros (HU-21) y desafíos semanales (HU-22): sin base de datos."""

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.exceptions import RequestValidationError

import app.models  # noqa: F401  (armar_semana recibe instancias del ORM)
from app.gamificacion.models import CodigoDesafio, CodigoLogro, DesafioSemanal, TipoDesafio
from app.gamificacion.schemas import DesafiosSemanaSalida, ProgresoSalida, RecompensaViajeSalida
from app.gamificacion.service import (
    BONO_SEMANA,
    DESAFIO_POR_CODIGO,
    DESAFIOS,
    LOGROS,
    NIVELES,
    DesafioCompletado,
    ResultadoDesafios,
    LogroDesbloqueado,
    Sincronizacion,
    ViajeDesafio,
    ViajeLogro,
    armar_progreso,
    armar_semana,
    completado_de,
    desafios_por_completar,
    elegir_desafios,
    evaluar_desafio,
    evaluar_logros,
    faltantes,
    fila_del_bono,
    nivel_de,
    punto_debil,
    puntos_viaje,
    recompensa_de,
    validar_semana,
)
from app.puntaje.service import ViajePuntuado
from app.telemetria.models import TipoEvento

INICIO = datetime(2026, 11, 1, 12, tzinfo=UTC)


def viaje(id: int, drivescore: int = 80, km: float = 5, eventos: int = 1) -> ViajeLogro:
    """El viaje `id` empieza `id` días después de INICIO y dura 20 min."""
    inicio = INICIO + timedelta(days=id)
    return ViajeLogro(id, inicio, inicio + timedelta(minutes=20), km * 1000, drivescore, eventos)


# --- Puntos --------------------------------------------------------------------


def test_puntos_del_ejemplo():
    # (10 + 2 × 7,4) × 1,25 (Muy bueno) = 31
    assert puntos_viaje(7_400, 86) == 31


def test_puntos_segun_la_calificacion():
    assert puntos_viaje(20_000, 95) == 75  # 50 × 1,5
    assert puntos_viaje(20_000, 75) == 63  # 50 × 1,25 = 62,5 → 63
    assert puntos_viaje(20_000, 74) == 50  # Regular × 1
    assert puntos_viaje(3_000, 55) == 8  # 16 × 0,5


def test_puntos_con_tope_de_100_km():
    assert puntos_viaje(250_000, 100) == puntos_viaje(100_000, 100) == 315


# --- Niveles -------------------------------------------------------------------


def test_niveles_en_los_bordes():
    assert nivel_de(0) == (NIVELES[0], NIVELES[1])
    assert nivel_de(99)[0].nombre == "Novato"
    assert nivel_de(100)[0].nombre == "Aprendiz"
    assert nivel_de(599)[0].numero == 3
    assert nivel_de(600) == (NIVELES[3], NIVELES[4])


def test_nivel_maximo_sin_siguiente():
    assert nivel_de(2500) == (NIVELES[-1], None)
    assert nivel_de(99_999)[0].nombre == "Leyenda"


# --- Logros --------------------------------------------------------------------


def test_sin_viajes_nada_desbloqueado():
    estados = evaluar_logros([])
    assert all(e.recorrido_id is None and e.progreso == 0 for e in estados.values())
    assert list(estados) == [logro.codigo for logro in LOGROS]


def test_logros_de_un_viaje_con_su_fecha():
    estados = evaluar_logros([viaje(1, 80, eventos=2), viaje(2, 92, eventos=0)])
    assert estados[CodigoLogro.primer_viaje].recorrido_id == 1
    assert estados[CodigoLogro.primer_viaje].fecha == viaje(1).fecha_fin
    assert estados[CodigoLogro.viaje_limpio].recorrido_id == 2
    assert estados[CodigoLogro.puntaje_excelente].recorrido_id == 2


def test_se_evaluan_en_orden_de_fecha():
    # Llegan desordenados: el primero es el del día 1
    estados = evaluar_logros([viaje(3), viaje(1)])
    assert estados[CodigoLogro.primer_viaje].recorrido_id == 1


def test_racha_se_corta_y_vuelve_a_empezar():
    puntajes = [80, 80, 80, 80, 60, 80, 80, 80]
    estados = evaluar_logros([viaje(i + 1, p) for i, p in enumerate(puntajes)])
    buena = estados[CodigoLogro.buena_racha]
    assert buena.recorrido_id is None
    assert buena.progreso == 3  # racha actual
    puntajes += [75, 90]
    estados = evaluar_logros([viaje(i + 1, p) for i, p in enumerate(puntajes)])
    # La quinta seguida es el viaje 10
    assert estados[CodigoLogro.buena_racha].recorrido_id == 10
    assert estados[CodigoLogro.racha_imparable].progreso == 5


def test_cantidad_y_km_con_su_progreso():
    viajes = [viaje(i, km=12.5) for i in range(1, 11)]  # 10 viajes, 125 km
    estados = evaluar_logros(viajes)
    assert estados[CodigoLogro.constante].recorrido_id == 10
    assert estados[CodigoLogro.veterano].progreso == 10
    assert estados[CodigoLogro.veterano].recorrido_id is None
    # 100 km se cumplen en el octavo
    assert estados[CodigoLogro.cien_kilometros].recorrido_id == 8
    assert estados[CodigoLogro.gran_ruta].progreso == 125
    # Los cumplidos muestran la meta
    assert estados[CodigoLogro.primer_viaje].progreso == 1


# --- Lo que falta guardar y la recompensa --------------------------------------


def test_faltantes_no_repite_lo_guardado():
    viajes = [viaje(1, 92, eventos=0), viaje(2, 80)]
    estados = evaluar_logros(viajes)
    puntos, logros = faltantes(viajes, {1}, estados, {CodigoLogro.primer_viaje})
    assert puntos == {2: puntos_viaje(5_000, 80)}
    assert {logro.codigo for logro in logros} == {
        CodigoLogro.viaje_limpio,
        CodigoLogro.puntaje_excelente,
    }


def _sincronizacion(puntos, puntos_nuevos, logros_nuevos, viajes=()):
    return Sincronizacion(
        puntos=puntos,
        puntos_nuevos=puntos_nuevos,
        estados=evaluar_logros(viajes),
        desbloqueados={logro.codigo: logro for logro in logros_nuevos},
        logros_nuevos=logros_nuevos,
    )


def test_recompensa_solo_de_este_viaje():
    ahora = INICIO
    # Al sincronizar se calcularon el viaje 1 (antiguo) y el 2 (recién finalizado)
    sinc = _sincronizacion(
        puntos=110,
        puntos_nuevos={1: 80, 2: 30},
        logros_nuevos=[
            LogroDesbloqueado(CodigoLogro.primer_viaje, 1, ahora),
            LogroDesbloqueado(CodigoLogro.viaje_limpio, 2, ahora),
        ],
    )
    r = recompensa_de(sinc, 2)
    assert r.puntos == 30
    # 80 → 110: pasa de Novato a Aprendiz con este viaje
    assert r.subio_de_nivel
    assert r.nivel.nombre == "Aprendiz"
    assert [logro.codigo for logro in r.logros_nuevos] == [CodigoLogro.viaje_limpio]
    salida = RecompensaViajeSalida.model_validate(r)
    assert salida.logros_nuevos[0].nombre == "Viaje limpio"


def test_recompensa_sin_subir_de_nivel():
    r = recompensa_de(_sincronizacion(150, {5: 30}, []), 5)
    assert (r.puntos, r.subio_de_nivel, r.logros_nuevos) == (30, False, [])


def test_progreso_con_logros_guardados():
    viajes = [viaje(1, 80, eventos=2), viaje(2, 80)]
    guardado = LogroDesbloqueado(CodigoLogro.primer_viaje, 1, viaje(1).fecha_fin)
    sinc = _sincronizacion(320, {}, [guardado], viajes)
    p = armar_progreso(sinc)
    assert (p.puntos, p.nivel.nombre, p.siguiente_nivel.nombre) == (320, "Conductor", "Conductor seguro")
    primero = p.logros[0]
    assert (primero.desbloqueado, primero.fecha_desbloqueo, primero.progreso) == (
        True, viaje(1).fecha_fin, 1,
    )
    racha = next(logro for logro in p.logros if logro.codigo == CodigoLogro.buena_racha)
    assert (racha.desbloqueado, racha.progreso, racha.meta) == (False, 2, 5)
    salida = ProgresoSalida.model_validate(p)
    assert len(salida.logros) == 9
    assert salida.logros[3].tipo.value == "racha"


# --- Desafíos semanales (HU-22) -----------------------------------------------

LUNES = datetime(2026, 11, 9, 5, tzinfo=UTC)  # lunes 00:00 en UTC−5


def puntuado(id: int, frenadas=100, aceleraciones=100, giros=100, velocidad=100, km=10):
    """Viaje de las semanas anteriores con sus categorías."""
    return ViajePuntuado(
        id, LUNES - timedelta(days=id), km * 1000, 90, frenadas, aceleraciones, giros, velocidad
    )


def de_semana(id: int, drivescore: int = 85, km: float = 6, **eventos: int) -> ViajeDesafio:
    """Viaje `id` de la semana (empieza `id` horas después del lunes)."""
    return ViajeDesafio(
        id,
        LUNES + timedelta(hours=id),
        km * 1000,
        drivescore,
        {TipoEvento[tipo]: n for tipo, n in eventos.items()},
    )


def avance(codigo: CodigoDesafio, viajes: list[ViajeDesafio]):
    desafio = DESAFIO_POR_CODIGO[codigo]
    return evaluar_desafio(desafio, desafio.meta, viajes)


def test_catalogo_de_desafios():
    assert len(DESAFIOS) == 9
    puntos = {d.codigo: (d.meta, d.puntos) for d in DESAFIOS}
    assert puntos[CodigoDesafio.semana_activa] == (5, 150)
    assert puntos[CodigoDesafio.kilometros_seguros] == (50, 150)
    assert puntos[CodigoDesafio.viaje_perfecto] == (1, 200)
    assert puntos[CodigoDesafio.racha_excelente] == (3, 250)
    assert puntos[CodigoDesafio.promedio_muy_bueno] == (80, 200)
    debiles = [d for d in DESAFIOS if d.tipo == TipoDesafio.punto_debil]
    assert len(debiles) == 4
    assert all(d.puntos == 200 and d.meta == 3 for d in debiles)


def test_punto_debil_es_la_peor_categoria():
    historial = [puntuado(1, giros=60), puntuado(2, giros=70, frenadas=80), puntuado(3)]
    assert punto_debil(historial, random.Random(0)) == "giros"


def test_punto_debil_pondera_por_distancia():
    # Frenadas 50 en 1 km y 100 en 40 km → 99; aceleraciones 90 en todos
    historial = [
        puntuado(1, frenadas=50, aceleraciones=90, km=1),
        puntuado(2, aceleraciones=90, km=20),
        puntuado(3, aceleraciones=90, km=20),
    ]
    assert punto_debil(historial, random.Random(0)) == "aceleraciones"


def test_punto_debil_empate_por_orden_de_riesgo():
    historial = [puntuado(i, frenadas=80, aceleraciones=80, giros=80) for i in range(1, 4)]
    assert punto_debil(historial, random.Random(0)) == "frenadas"
    # Todo en 100: la de mayor riesgo
    assert punto_debil([puntuado(i) for i in range(1, 4)], random.Random(0)) == "velocidad"


def test_punto_debil_al_azar_con_pocos_viajes():
    historial = [puntuado(1, giros=10), puntuado(2, giros=10)]
    elegidas = {punto_debil(historial, random.Random(semilla)) for semilla in range(40)}
    assert elegidas == {"velocidad", "frenadas", "giros", "aceleraciones"}


def test_elegir_no_repite_la_semana_anterior():
    anteriores = {CodigoDesafio.semana_activa, CodigoDesafio.viaje_perfecto}
    for semilla in range(30):
        constancia, calidad, debil = elegir_desafios(anteriores, [], random.Random(semilla))
        assert constancia.codigo == CodigoDesafio.kilometros_seguros
        assert calidad.codigo in {CodigoDesafio.racha_excelente, CodigoDesafio.promedio_muy_bueno}
        assert debil.tipo == TipoDesafio.punto_debil


def test_elegir_sin_semana_anterior_usa_todos():
    calidades = {
        elegir_desafios(set(), [], random.Random(semilla))[1].codigo for semilla in range(40)
    }
    assert len(calidades) == 3


def test_elegir_con_historial_usa_el_punto_debil():
    historial = [puntuado(i, velocidad=40) for i in range(1, 4)]
    *_, debil = elegir_desafios(set(), historial, random.Random(1))
    assert debil.codigo == CodigoDesafio.sin_excesos_velocidad


def test_semana_activa_y_kilometros():
    viajes = [de_semana(i, km=12.7) for i in range(1, 5)]
    a = avance(CodigoDesafio.semana_activa, viajes)
    assert (a.progreso, a.completado) == (4, False)
    a = avance(CodigoDesafio.semana_activa, viajes + [de_semana(9), de_semana(10)])
    assert (a.progreso, a.completado) == (5, True)
    km = avance(CodigoDesafio.kilometros_seguros, viajes)
    assert (km.progreso, km.completado) == (50, True)  # 50,8 km
    km = avance(CodigoDesafio.kilometros_seguros, viajes[:3])
    assert (km.progreso, km.completado) == (38, False)


def test_viaje_perfecto_exige_5_km_sin_eventos():
    assert not avance(CodigoDesafio.viaje_perfecto, [de_semana(1, km=4.9)]).completado
    assert not avance(CodigoDesafio.viaje_perfecto, [de_semana(1, giro_agresivo=1)]).completado
    a = avance(CodigoDesafio.viaje_perfecto, [de_semana(1, giro_agresivo=1), de_semana(2, km=5)])
    assert (a.progreso, a.completado) == (1, True)


def test_racha_excelente():
    viajes = [de_semana(1, 95), de_semana(2, 92), de_semana(3, 70), de_semana(4, 90)]
    a = avance(CodigoDesafio.racha_excelente, viajes)
    assert (a.progreso, a.completado) == (1, False)  # la racha actual
    viajes += [de_semana(5, 99), de_semana(6, 91)]
    assert avance(CodigoDesafio.racha_excelente, viajes).completado
    # Cumplida, no se pierde aunque se corte después
    a = avance(CodigoDesafio.racha_excelente, viajes + [de_semana(7, 50)])
    assert (a.progreso, a.completado) == (3, True)


def test_racha_excelente_sigue_el_orden_de_los_viajes():
    # Llegan desordenados: 95, 95, 60, 95 por fecha → racha actual 1
    viajes = [de_semana(4, 95), de_semana(1, 95), de_semana(3, 60), de_semana(2, 95)]
    a = avance(CodigoDesafio.racha_excelente, viajes)
    assert (a.progreso, a.completado) == (1, False)


def test_promedio_muy_bueno():
    dos = [de_semana(1, 90), de_semana(2, 90)]
    a = avance(CodigoDesafio.promedio_muy_bueno, dos)
    assert (a.completado, a.promedio, a.viajes) == (False, 90, 2)  # faltan viajes
    tres = dos + [de_semana(3, 60)]  # (90 + 90 + 60) / 3 = 80
    a = avance(CodigoDesafio.promedio_muy_bueno, tres)
    assert (a.progreso, a.completado, a.promedio) == (80, True, 80)
    # Baja después, pero ya se cumplió
    a = avance(CodigoDesafio.promedio_muy_bueno, tres + [de_semana(4, 40)])
    assert (a.completado, a.promedio, a.viajes) == (True, 70, 4)


def test_promedio_muy_bueno_pendiente():
    a = avance(CodigoDesafio.promedio_muy_bueno, [de_semana(1, 70), de_semana(2, 76), de_semana(3, 76)])
    assert (a.progreso, a.completado, a.promedio) == (74, False, 74)
    a = avance(CodigoDesafio.promedio_muy_bueno, [])
    assert (a.progreso, a.completado, a.promedio, a.viajes) == (0, False, None, 0)


def test_punto_debil_cuenta_viajes_largos_sin_ese_evento():
    viajes = [
        de_semana(1, frenada_brusca=1),
        de_semana(2, giro_agresivo=2),  # sin frenadas: cuenta
        de_semana(3, km=3),  # corto
        de_semana(4),
    ]
    a = avance(CodigoDesafio.sin_frenadas_bruscas, viajes)
    assert (a.progreso, a.completado) == (2, False)
    a = avance(CodigoDesafio.sin_frenadas_bruscas, viajes + [de_semana(5, exceso_velocidad=1)])
    assert (a.progreso, a.completado) == (3, True)


def fila(
    tipo: TipoDesafio,
    codigo: CodigoDesafio,
    id: int | None = None,
    completado: datetime | None = None,
) -> DesafioSemanal:
    desafio = DESAFIO_POR_CODIGO[codigo]
    return DesafioSemanal(
        id=id,
        semana_inicio=LUNES,
        semana_fin=LUNES + timedelta(days=7),
        tipo=tipo,
        codigo=codigo,
        meta=desafio.meta,
        puntos=desafio.puntos,
        fecha_completado=completado,
    )


def test_armar_semana_ordena_por_tipo():
    filas = [
        fila(TipoDesafio.punto_debil, CodigoDesafio.sin_giros_agresivos),
        fila(TipoDesafio.calidad, CodigoDesafio.promedio_muy_bueno),
        fila(TipoDesafio.constancia, CodigoDesafio.semana_activa),
    ]
    semana = armar_semana(filas, [de_semana(1), de_semana(2)])
    assert (semana.inicio, semana.fin) == (LUNES, LUNES + timedelta(days=7))
    constancia, calidad, debil = semana.desafios
    assert [constancia.tipo, calidad.tipo, debil.tipo] == [
        TipoDesafio.constancia,
        TipoDesafio.calidad,
        TipoDesafio.punto_debil,
    ]
    assert (constancia.progreso, constancia.meta, constancia.viajes_minimos) == (2, 5, None)
    assert (calidad.promedio, calidad.viajes, calidad.viajes_minimos) == (85, 2, 3)
    assert (debil.categoria, debil.progreso, debil.puntos) == ("giros", 2, 200)
    salida = DesafiosSemanaSalida.model_validate(semana)
    assert salida.desafios[1].tipo_progreso.value == "promedio"


def test_validar_semana():
    lunes_local = datetime(2026, 11, 9, tzinfo=timezone(timedelta(hours=-5)))
    validar_semana(lunes_local, lunes_local + timedelta(days=3))
    with pytest.raises(RequestValidationError):  # martes
        validar_semana(lunes_local + timedelta(days=1), lunes_local + timedelta(days=3))
    with pytest.raises(RequestValidationError):  # futuro
        validar_semana(lunes_local, lunes_local - timedelta(hours=1))
    with pytest.raises(RequestValidationError):  # semana pasada
        validar_semana(lunes_local, lunes_local + timedelta(days=7))


# --- Completar desafíos y recompensa (HU-23) ----------------------------------

COMPLETADO = LUNES + timedelta(days=2)


def semana_de_tres(*completados: datetime | None) -> list[DesafioSemanal]:
    """Semana activa (id 1), Promedio muy bueno (2) y Sin giros agresivos (3)."""
    c1, c2, c3 = (list(completados) + [None, None, None])[:3]
    return [
        fila(TipoDesafio.constancia, CodigoDesafio.semana_activa, 1, c1),
        fila(TipoDesafio.calidad, CodigoDesafio.promedio_muy_bueno, 2, c2),
        fila(TipoDesafio.punto_debil, CodigoDesafio.sin_giros_agresivos, 3, c3),
    ]


def test_por_completar_los_que_llegaron_a_la_meta():
    # 5 viajes de 6 km limpios con 95: cumple la semana activa, el promedio y los giros
    viajes = [de_semana(i, 95) for i in range(1, 6)]
    codigos = [f.codigo for f in desafios_por_completar(semana_de_tres(), viajes)]
    assert codigos == [
        CodigoDesafio.semana_activa,
        CodigoDesafio.promedio_muy_bueno,
        CodigoDesafio.sin_giros_agresivos,
    ]


def test_por_completar_solo_los_que_cumplen():
    viajes = [de_semana(i, 70, giro_agresivo=1) for i in range(1, 4)]
    assert desafios_por_completar(semana_de_tres(), viajes) == []


def test_por_completar_no_repite_los_completados():
    viajes = [de_semana(i, 95) for i in range(1, 6)]
    codigos = [f.codigo for f in desafios_por_completar(semana_de_tres(COMPLETADO), viajes)]
    assert CodigoDesafio.semana_activa not in codigos
    assert len(codigos) == 2
    # Con los tres ya completados no queda nada, aunque los viajes sigan cumpliendo
    assert desafios_por_completar(semana_de_tres(COMPLETADO, COMPLETADO, COMPLETADO), viajes) == []


def test_por_completar_sin_viajes_no_completa_nada():
    assert desafios_por_completar(semana_de_tres(), []) == []


def test_bono_solo_con_los_tres_completados():
    assert fila_del_bono(semana_de_tres()) is None
    assert fila_del_bono(semana_de_tres(COMPLETADO, COMPLETADO)) is None
    assert fila_del_bono([]) is None


def test_bono_se_guarda_con_el_ultimo_completado():
    tarde = COMPLETADO + timedelta(hours=3)
    assert fila_del_bono(semana_de_tres(COMPLETADO, tarde, COMPLETADO)).id == 2
    # Empate de fecha: el de mayor id
    assert fila_del_bono(semana_de_tres(COMPLETADO, COMPLETADO, COMPLETADO)).id == 3


def desafios_nuevos(*filas: DesafioSemanal, bono: int | None = None) -> ResultadoDesafios:
    return ResultadoDesafios(
        filas=list(filas),
        viajes=[],
        completados=[completado_de(f) for f in filas],
        bono=bono,
        bono_recibido=bono is not None,
    )


def test_completado_de_toma_los_puntos_de_la_fila():
    c = completado_de(fila(TipoDesafio.punto_debil, CodigoDesafio.sin_giros_agresivos))
    assert c == DesafioCompletado(
        CodigoDesafio.sin_giros_agresivos, TipoDesafio.punto_debil, "Sin giros agresivos", 200, "giros"
    )
    c = completado_de(fila(TipoDesafio.constancia, CodigoDesafio.semana_activa))
    assert (c.puntos, c.categoria) == (150, None)


def test_recompensa_sin_desafios_queda_igual():
    r = recompensa_de(_sincronizacion(150, {5: 30}, []), 5)
    assert (r.desafios_completados, r.bono_semanal) == ((), None)
    salida = RecompensaViajeSalida.model_validate(r)
    assert (salida.desafios_completados, salida.bono_semanal) == ([], None)


def test_recompensa_con_desafios_y_bono():
    sinc = _sincronizacion(150, {5: 30}, [])
    desafios = desafios_nuevos(*semana_de_tres(), bono=BONO_SEMANA)
    r = recompensa_de(sinc, 5, desafios)
    # Los puntos del viaje no incluyen los de los desafíos
    assert r.puntos == 30
    assert r.bono_semanal == 100
    assert [d.puntos for d in r.desafios_completados] == [150, 200, 200]
    # 120 antes del viaje (Aprendiz) → 150 + 550 + 100 = 800 (Conductor seguro)
    assert r.subio_de_nivel
    assert r.nivel.nombre == "Conductor seguro"
    salida = RecompensaViajeSalida.model_validate(r)
    assert salida.desafios_completados[2].categoria == "giros"
    assert salida.desafios_completados[0].categoria is None
    assert salida.bono_semanal == 100


def test_recompensa_sube_de_nivel_solo_por_un_desafio():
    # El viaje deja 90 pts (Novato); el desafío de 150 lleva a 240 (Aprendiz)
    sinc = _sincronizacion(90, {7: 30}, [])
    assert not recompensa_de(sinc, 7).subio_de_nivel
    desafios = desafios_nuevos(fila(TipoDesafio.constancia, CodigoDesafio.semana_activa))
    r = recompensa_de(sinc, 7, desafios)
    assert (r.subio_de_nivel, r.nivel.nombre) == (True, "Aprendiz")
    assert r.bono_semanal is None


def test_resultado_de_desafios_suma_puntos_y_escribe():
    nada = ResultadoDesafios([], [], [], None, False)
    assert (nada.puntos, nada.escribio) == (0, False)
    solo_bono = ResultadoDesafios([], [], [], BONO_SEMANA, True)
    assert (solo_bono.puntos, solo_bono.escribio) == (100, True)
    r = desafios_nuevos(*semana_de_tres()[:2])
    assert (r.puntos, r.escribio) == (350, True)


def test_armar_semana_toma_completado_de_la_fecha():
    # Sin viajes que lo cumplan, pero ya completado: sigue completado y con la meta
    filas = semana_de_tres(COMPLETADO)
    semana = armar_semana(filas, [])
    constancia, calidad, debil = semana.desafios
    assert (constancia.completado, constancia.progreso, constancia.meta) == (True, 5, 5)
    assert (calidad.completado, debil.completado) == (False, False)
    assert (semana.bono.puntos, semana.bono.recibido) == (100, False)
    salida = DesafiosSemanaSalida.model_validate(armar_semana(filas, [], bono_recibido=True))
    assert (salida.bono.puntos, salida.bono.recibido) == (100, True)


def test_armar_semana_sin_completar_calcula_el_avance():
    semana = armar_semana(semana_de_tres(), [de_semana(1), de_semana(2)])
    assert [d.completado for d in semana.desafios] == [False, False, False]
    assert semana.desafios[0].progreso == 2
