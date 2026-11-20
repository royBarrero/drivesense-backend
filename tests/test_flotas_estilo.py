"""Estilo de conducción (HU-25, K-means), con y sin base de datos."""

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.auth.models import Usuario
from app.flotas import estilo as e
from app.flotas.models import EstiloConduccion as Estilo
from app.flotas.models import EstiloConductor
from app.telemetria.models import TipoEvento
from tests.conftest import cabecera
from tests.test_flotas import registrar_conductor
from tests.test_flotas_perfil import crear_viaje

AHORA = datetime(2026, 11, 19, 15, tzinfo=UTC)
CONDUCTORES = "/api/v1/flotas/conductores"
TABLERO = "/api/v1/flotas/tablero"
FRENADA = TipoEvento.frenada_brusca
ACELERACION = TipoEvento.aceleracion_severa
GIRO = TipoEvento.giro_agresivo
EXCESO = TipoEvento.exceso_velocidad


def variables(
    usuario_id=1,
    frenadas=0.0,
    aceleraciones=0.0,
    giros=0.0,
    excesos=0.0,
    drivescore=90,
    velocidad_maxima=60.0,
    viajes=5,
    distancia_m=50_000.0,
) -> e.VariablesConductor:
    return e.VariablesConductor(
        usuario_id, viajes, distancia_m, frenadas, aceleraciones, giros, excesos, drivescore, velocidad_maxima
    )


def poblacion(rng: random.Random) -> list[e.VariablesConductor]:
    """Conductores sintéticos con estilos claros: prudentes, intermedios y uno de cada riesgo."""
    gente = []
    n = 0

    def agregar(cantidad, **base):
        nonlocal n
        for _ in range(cantidad):
            n += 1
            ruido = {k: max(0.0, v + rng.uniform(-0.1, 0.1)) for k, v in base.items() if k != "drivescore"}
            gente.append(variables(n, drivescore=base["drivescore"] + rng.randint(-2, 2), **ruido))

    agregar(6, frenadas=0.1, aceleraciones=0.1, giros=0.1, excesos=0.0, drivescore=95)
    agregar(4, frenadas=0.9, aceleraciones=0.7, giros=0.7, excesos=0.4, drivescore=78)
    agregar(3, frenadas=3.5, aceleraciones=0.5, giros=0.5, excesos=0.2, drivescore=58)
    agregar(3, frenadas=0.6, aceleraciones=3.0, giros=0.5, excesos=0.2, drivescore=62)
    return gente


# --- Sin base de datos ----------------------------------------------------------


def test_orden_de_riesgo_fijo():
    assert e.orden_riesgo(Estilo.prudente) == 0
    assert e.orden_riesgo(Estilo.veloz) == 5
    assert e.orden_riesgo(Estilo.sin_datos) is None
    assert e.es_mayor_riesgo(Estilo.intermedio, Estilo.brusco_frenar)
    assert not e.es_mayor_riesgo(Estilo.veloz, Estilo.prudente)
    assert not e.es_mayor_riesgo(Estilo.sin_datos, Estilo.veloz)


def test_variables_por_10_km_y_ponderadas():
    viajes = [
        e.ViajeEstilo(1, AHORA, 10_000, 100, 50, Counter({FRENADA: 1})),
        e.ViajeEstilo(2, AHORA, 30_000, 60, 70, Counter({FRENADA: 3, EXCESO: 2})),
    ]
    v = e.resumir_variables(7, viajes)
    assert (v.frenadas, v.excesos, v.aceleraciones) == (1.0, 0.5, 0.0)
    assert v.drivescore == 70  # (100·10 + 60·30) / 40
    assert v.velocidad_maxima == 60
    assert not v.suficiente  # 2 viajes


def test_minimos_de_viajes_y_distancia():
    assert e.MINIMO_VIAJES_ESTILO == 3 and e.MINIMO_DISTANCIA_ESTILO_M == 10_000
    assert variables(viajes=3, distancia_m=10_000).suficiente
    assert not variables(viajes=2, distancia_m=50_000).suficiente
    assert not variables(viajes=5, distancia_m=9_999).suficiente
    assert e.resumir_variables(1, []) is None


def z(**valores):
    return {v: 0.0 for v in e.VARIABLES} | valores


def centro(**valores):
    return {"frenadas": 1, "aceleraciones": 1, "giros": 1, "excesos": 1, "drivescore": 80, "velocidad_maxima": 60} | valores


def test_nombre_por_la_tasa_mas_distintiva():
    assert e.nombrar_grupo(z(frenadas=1.5, aceleraciones=0.8), centro()) == Estilo.brusco_frenar
    assert e.nombrar_grupo(z(aceleraciones=1.0), centro()) == Estilo.acelerador
    assert e.nombrar_grupo(z(giros=0.9), centro()) == Estilo.brusco_curvas
    assert e.nombrar_grupo(z(excesos=2.0), centro()) == Estilo.veloz
    # Empate: la de mayor riesgo (exceso antes que frenada)
    assert e.nombrar_grupo(z(frenadas=1.0, excesos=1.0), centro()) == Estilo.veloz


def test_una_tasa_alta_pero_baja_en_valor_no_da_nombre():
    # Muy por encima de la media pero con menos de 0,5 eventos cada 10 km
    assert e.nombrar_grupo(z(frenadas=2.0), centro(frenadas=0.3)) == Estilo.intermedio


def test_prudente_e_intermedio():
    bajo = z(frenadas=-1, aceleraciones=-1, giros=-1, excesos=-0.5, drivescore=1)
    assert e.nombrar_grupo(bajo, centro(drivescore=93)) == Estilo.prudente
    # Mismo patrón con DriveScore bajo en valor: no es prudente
    assert e.nombrar_grupo(bajo, centro(drivescore=70)) == Estilo.intermedio
    assert e.nombrar_grupo(z(frenadas=0.3, drivescore=-0.2), centro()) == Estilo.intermedio


def test_kmeans_asigna_estilos_con_sentido():
    gente = poblacion(random.Random(1))
    modelo = e.entrenar(gente, AHORA)
    estilos = dict(zip((v.usuario_id for v in gente), modelo.predecir(gente)))
    assert {estilos[i] for i in range(1, 7)} == {Estilo.prudente}
    assert {estilos[i] for i in range(11, 14)} == {Estilo.brusco_frenar}
    assert {estilos[i] for i in range(14, 17)} == {Estilo.acelerador}
    # Un conductor nuevo muy riesgoso nunca cae como prudente
    riesgoso = variables(99, frenadas=4.0, aceleraciones=3.5, giros=2.0, excesos=1.5, drivescore=40)
    assert modelo.predecir([riesgoso]) != [Estilo.prudente]


def test_sin_suficientes_conductores_no_hay_modelo():
    assert e.entrenar([variables(i) for i in range(3)], AHORA) is None
    # Los que no llegan al mínimo de viajes no entrenan
    pocos = [variables(i, frenadas=i, viajes=1) for i in range(10)]
    assert e.entrenar(pocos, AHORA) is None


def test_transicion_registra_la_alerta_solo_al_subir_el_riesgo():
    antes = AHORA - timedelta(days=3)
    nuevo = e.transicion(None, Estilo.intermedio, antes)
    assert nuevo == e.EstadoEstilo(Estilo.intermedio, antes, None, None)
    assert e.transicion(nuevo, Estilo.intermedio, AHORA) is nuevo

    subio = e.transicion(nuevo, Estilo.brusco_frenar, AHORA)
    assert (subio.estilo, subio.estilo_anterior, subio.fecha_alerta) == (
        Estilo.brusco_frenar, Estilo.intermedio, AHORA,
    )
    # Bajar de riesgo o quedarse sin datos limpia la alerta
    assert e.transicion(subio, Estilo.prudente, AHORA).estilo_anterior is None
    assert e.transicion(subio, Estilo.sin_datos, AHORA).fecha_alerta is None


def test_explicacion_frente_al_promedio_de_la_flota():
    brusco = variables(1, frenadas=3.0, aceleraciones=0.5, drivescore=60)
    flota = [brusco, variables(2, frenadas=1.0, aceleraciones=0.5, drivescore=90), variables(3, frenadas=0.5, aceleraciones=0.5, drivescore=90)]
    frases = e.explicar_estilo(Estilo.brusco_frenar, brusco, flota)
    # Promedio de la flota (él incluido): 1,5 frenadas cada 10 km
    assert frases[0] == "Frena fuerte el doble que el promedio de tu flota"
    assert frases[-1] == "Su DriveScore (60) está 20 puntos bajo el promedio de tu flota"
    assert len(frases) <= e.MAXIMO_FRASES


def test_explicacion_de_prudente_y_porcentajes():
    prudente = variables(1, frenadas=0.2, drivescore=96)
    otro = variables(2, frenadas=1.8, drivescore=80)
    assert e.explicar_estilo(Estilo.prudente, prudente, [prudente, otro]) == [
        "Tiene un 80 % menos de eventos que el promedio de tu flota",
        "Su DriveScore (96) está 8 puntos sobre el promedio de tu flota",
    ]
    assert e._cuanto_mas(1.6) == "un 60 % más"
    assert e._cuanto_mas(3.1) == "el triple"
    assert e._cuanto_mas(4.6) == "5 veces más"


def test_explicacion_sin_flota_y_sin_datos():
    solo = variables(1, giros=2.14, drivescore=70)
    assert e.explicar_estilo(Estilo.brusco_curvas, solo, [solo]) == [
        "2,1 giros agresivos cada 10 km",
        "DriveScore promedio de 70 en los últimos 30 días",
    ]
    assert e.explicar_estilo(Estilo.sin_datos, variables(viajes=1), []) == [
        "Necesita al menos 3 viajes y 10 km en los últimos 30 días (lleva 1 viaje)"
    ]


def test_tendencia_con_regresion_lineal():
    baja = e.tendencia_riesgo([90, 88, 85, 80, 76, 70])
    assert baja.a_la_baja and baja.pendiente == -4.0
    assert baja.proyectado == 67
    sube = e.tendencia_riesgo([60, 65, 70, 75, 80])
    assert sube.pendiente == 5.0 and not sube.a_la_baja and sube.proyectado == 85
    plana = e.tendencia_riesgo([80, 81, 80, 79, 80])
    assert not plana.a_la_baja


def test_tendencia_usa_los_ultimos_10_y_exige_5():
    assert e.tendencia_riesgo([90, 80, 70, 60]) == e.Tendencia(4, None, None, None)
    # Los 5 primeros (malos) quedan fuera: solo cuentan los 10 últimos, que suben
    t = e.tendencia_riesgo([10] * 5 + list(range(50, 100, 5)))
    assert t.viajes == 10 and t.pendiente == 5.0


def test_tendencia_recorta_el_proyectado_a_0_100():
    assert e.tendencia_riesgo([100, 100, 100, 100, 100]).proyectado == 100
    assert e.tendencia_riesgo([40, 30, 20, 10, 0]).proyectado == 0


# --- Con base de datos ----------------------------------------------------------


async def viajes_con(db, usuario_id, drivescore, eventos, cantidad=4, distancia_m=10_000):
    for i in range(cantidad):
        await crear_viaje(db, usuario_id, drivescore, dias_atras=i + 1, distancia_m=distancia_m, eventos=eventos)


async def flota_de_ejemplo(cliente, db, sesion, desde: int) -> dict[str, int]:
    """Cuatro conductores prudentes, uno que frena fuerte y uno sin viajes suficientes."""
    ids = {}
    for i in range(4):
        c = await registrar_conductor(cliente, sesion, desde + i, f"Prudente {i}")
        ids[f"prudente{i}"] = c["id"]
        await viajes_con(db, c["id"], 95 - i, [])
    c = await registrar_conductor(cliente, sesion, desde + 4, "Brusco")
    ids["brusco"] = c["id"]
    await viajes_con(db, c["id"], 55, [FRENADA] * 3)
    c = await registrar_conductor(cliente, sesion, desde + 5, "Nuevo")
    ids["nuevo"] = c["id"]
    await viajes_con(db, c["id"], 80, [], cantidad=2)
    await db.commit()
    return ids


async def test_perfil_listado_y_tablero_con_estilo(cliente, db, registrar_empresa):
    sesion = await registrar_empresa(1)
    ids = await flota_de_ejemplo(cliente, db, sesion, 10)
    e.reiniciar_cache()

    perfil = (await cliente.get(f"{CONDUCTORES}/{ids['brusco']}", headers=cabecera(sesion))).json()
    estilo = perfil["estilo"]
    assert estilo["estilo"] == "brusco_frenar" and estilo["nombre"] == "Brusco al frenar"
    assert estilo["orden_riesgo"] == 4 and estilo["viajes"] == 4
    assert estilo["explicacion"][0].startswith("Frena fuerte")
    assert estilo["alerta"] is None
    assert estilo["viajes_tendencia"] == 4 and estilo["riesgo"] is None  # menos de 5 viajes

    prudente = (await cliente.get(f"{CONDUCTORES}/{ids['prudente0']}", headers=cabecera(sesion))).json()
    assert prudente["estilo"]["estilo"] == "prudente"
    nuevo = (await cliente.get(f"{CONDUCTORES}/{ids['nuevo']}", headers=cabecera(sesion))).json()
    assert nuevo["estilo"]["estilo"] == "sin_datos" and nuevo["estilo"]["orden_riesgo"] is None

    listado = (await cliente.get(CONDUCTORES, headers=cabecera(sesion))).json()["conductores"]
    por_nombre = {c["nombre"]: c for c in listado}
    assert por_nombre["Brusco"]["estilo"] == "brusco_frenar"
    assert por_nombre["Nuevo"]["estilo"] == "sin_datos"
    assert por_nombre["Brusco"]["alerta_estilo"] is None

    tablero = (await cliente.get(TABLERO, headers=cabecera(sesion))).json()
    assert tablero["por_estilo"]["prudente"] == 4
    assert tablero["por_estilo"]["brusco_frenar"] == 1
    assert tablero["por_estilo"]["sin_datos"] == 1
    assert sum(tablero["por_estilo"].values()) == 6


async def test_alerta_al_pasar_a_mayor_riesgo(cliente, db, registrar_empresa):
    sesion = await registrar_empresa(1)
    ids = await flota_de_ejemplo(cliente, db, sesion, 10)
    # Estaba como intermedio desde hace una semana
    db.add(
        EstiloConductor(
            usuario_id=ids["brusco"],
            estilo=Estilo.intermedio,
            fecha_estilo=datetime.now(UTC) - timedelta(days=7),
            fecha_actualizacion=datetime.now(UTC) - timedelta(days=7),
        )
    )
    await db.commit()
    await e.actualizar_estilos(db, forzar=True)

    fila = next(
        c
        for c in (await cliente.get(CONDUCTORES, headers=cabecera(sesion))).json()["conductores"]
        if c["id"] == ids["brusco"]
    )
    assert fila["alerta_estilo"]["anterior"] == "intermedio"
    assert fila["alerta_estilo"]["actual"] == "brusco_frenar"
    perfil = (await cliente.get(f"{CONDUCTORES}/{ids['brusco']}", headers=cabecera(sesion))).json()
    assert perfil["estilo"]["alerta"]["anterior"] == "intermedio"


async def test_tendencia_a_la_baja_en_el_perfil(cliente, db, registrar_empresa):
    sesion = await registrar_empresa(1)
    c = await registrar_conductor(cliente, sesion, 10)
    for i, puntaje in enumerate([90, 85, 80, 70, 60, 50]):
        await crear_viaje(db, c["id"], puntaje, dias_atras=10 - i)
    await db.commit()
    estilo = (await cliente.get(f"{CONDUCTORES}/{c['id']}", headers=cabecera(sesion))).json()["estilo"]
    assert estilo["tendencia_a_la_baja"] is True
    assert estilo["pendiente"] < 0
    assert estilo["proyectado"] < 50 and estilo["riesgo"] == "alto"


async def test_la_explicacion_solo_usa_la_propia_flota(cliente, db, registrar_empresa):
    sesion1 = await registrar_empresa(1)
    sesion2 = await registrar_empresa(2)
    ids = await flota_de_ejemplo(cliente, db, sesion1, 10)
    # La otra empresa tiene conductores que frenan muchísimo: si se mezclaran, el
    # promedio subiría y el brusco ya no frenaría "el doble"
    for i in range(3):
        otro = await registrar_conductor(cliente, sesion2, 30 + i)
        await viajes_con(db, otro["id"], 30, [FRENADA] * 8)
    await db.commit()
    e.reiniciar_cache()

    perfil = (await cliente.get(f"{CONDUCTORES}/{ids['brusco']}", headers=cabecera(sesion1))).json()
    # 3 frenadas cada 10 km frente a 0,6 de promedio de su flota (5 con datos)
    assert perfil["estilo"]["explicacion"][0] == "Frena fuerte 5 veces más que el promedio de tu flota"
    # La otra empresa no ve a los conductores de esta (404) ni los cuenta
    assert (await cliente.get(f"{CONDUCTORES}/{ids['brusco']}", headers=cabecera(sesion2))).status_code == 404
    tablero2 = (await cliente.get(TABLERO, headers=cabecera(sesion2))).json()
    assert sum(tablero2["por_estilo"].values()) == 3


async def test_gratis_no_trae_el_conteo_por_estilo(cliente, db, registrar_empresa):
    sesion = await registrar_empresa(1, plan="gratis")
    c = await registrar_conductor(cliente, sesion, 10)
    await viajes_con(db, c["id"], 90, [])
    await db.commit()
    tablero = (await cliente.get(TABLERO, headers=cabecera(sesion))).json()
    assert tablero["completo"] is False
    assert set(tablero["por_estilo"].values()) == {0}
    # El perfil sí trae el estilo (con un solo conductor no hay modelo: sin datos)
    perfil = (await cliente.get(f"{CONDUCTORES}/{c['id']}", headers=cabecera(sesion))).json()
    assert perfil["estilo"]["estilo"] == "sin_datos"


async def test_inactivo_no_cuenta_y_acceso(cliente, db, registrar_empresa):
    sesion = await registrar_empresa(1)
    ids = await flota_de_ejemplo(cliente, db, sesion, 10)
    await db.execute(update(Usuario).where(Usuario.id == ids["nuevo"]).values(activo=False))
    await db.commit()
    tablero = (await cliente.get(TABLERO, headers=cabecera(sesion))).json()
    assert tablero["por_estilo"]["sin_datos"] == 0
    assert (await cliente.get(f"{CONDUCTORES}/{ids['nuevo']}", headers=cabecera(sesion))).status_code == 404
    assert (await cliente.get(TABLERO)).status_code == 401
