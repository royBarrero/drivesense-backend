"""Tablero de la flota (pantalla Reportes del panel), con y sin base de datos."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import update

from app.auth.models import Usuario
from app.flotas.schemas import Periodo
from app.flotas.tablero import (
    DISTANCIA_MINIMA_RANKING_M,
    ConductorTablero,
    armar_evolucion,
    top_a_vigilar,
    top_mejores,
)
from app.puntaje.service import Agrupacion, ViajePuntuado
from app.recorridos.models import EstadoRecorrido
from app.telemetria.models import TipoEvento
from tests.conftest import cabecera
from tests.test_flotas import registrar_conductor
from tests.test_flotas_perfil import crear_viaje

TABLERO = "/api/v1/flotas/tablero"
FRENADA = TipoEvento.frenada_brusca


async def pedir(cliente, sesion, **parametros) -> dict:
    respuesta = await cliente.get(TABLERO, headers=cabecera(sesion), params=parametros)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


# --- Pruebas sin base de datos ----------------------------------------------


def cond(id, nombre, drivescore, distancia_m, eventos=0) -> ConductorTablero:
    return ConductorTablero(id, nombre, drivescore, distancia_m, eventos)


def test_la_distancia_minima_es_20_km():
    assert DISTANCIA_MINIMA_RANKING_M == 20_000


def test_los_mejores_exigen_la_distancia_minima_y_ordenan_por_drivescore():
    conductores = [
        cond(1, "Ana", 80, 30_000),
        cond(2, "Bruno", 99, 19_999),  # no llega al mínimo
        cond(3, "Carla", 90, 20_000),  # justo en el mínimo
        cond(4, "Diego", 80, 50_000),  # empata con Ana: gana quien recorrió más
    ]
    assert [i.nombre for i in top_mejores(conductores)] == ["Carla", "Diego", "Ana"]


def test_los_rankings_se_limitan_a_cinco():
    conductores = [cond(i, f"C{i}", 50 + i, 30_000, eventos=i) for i in range(1, 9)]
    assert len(top_mejores(conductores)) == 5
    assert len(top_a_vigilar(conductores)) == 5


def test_a_vigilar_mide_por_cada_10_km_y_no_en_total():
    conductores = [
        cond(1, "Muchos km", 70, 100_000, eventos=10),  # 1,0 por 10 km
        cond(2, "Pocos km", 60, 25_000, eventos=5),  # 2,0 por 10 km
        cond(3, "Limpio", 95, 40_000, eventos=0),  # sin eventos: no sale
        cond(4, "Corto", 20, 5_000, eventos=9),  # no llega al mínimo
    ]
    ranking = top_a_vigilar(conductores)
    assert [i.nombre for i in ranking] == ["Pocos km", "Muchos km"]
    assert [i.eventos_por_10km for i in ranking] == [2.0, 1.0]


def test_a_vigilar_desempata_por_menor_drivescore():
    conductores = [cond(1, "Ana", 80, 20_000, 2), cond(2, "Bruno", 60, 20_000, 2)]
    assert [i.nombre for i in top_a_vigilar(conductores)] == ["Bruno", "Ana"]


def test_evolucion_vacia_sin_viajes():
    evolucion = armar_evolucion([], None, ZoneInfo("America/La_Paz"), Periodo.todo)
    assert evolucion.puntos == []
    assert evolucion.agrupacion == Agrupacion.semana


def test_evolucion_por_dia_y_por_semana():
    zona = ZoneInfo("America/La_Paz")
    # 02:00 UTC del 11 = 22:00 del 10 en La Paz: el mismo día local que el viaje de las 12:00 UTC
    viajes = [
        ViajePuntuado(1, datetime(2026, 11, 11, 2, 0, tzinfo=UTC), 10_000, 80, 80, 80, 80, 80),
        ViajePuntuado(2, datetime(2026, 11, 10, 12, 0, tzinfo=UTC), 10_000, 60, 60, 60, 60, 60),
        ViajePuntuado(3, datetime(2026, 11, 13, 12, 0, tzinfo=UTC), 10_000, 90, 90, 90, 90, 90),
    ]
    por_dia = armar_evolucion(viajes, datetime(2026, 11, 8, 4, 0, tzinfo=UTC), zona, Periodo.siete_dias)
    assert por_dia.agrupacion == Agrupacion.dia
    assert [(p.drivescore, p.viajes) for p in por_dia.puntos] == [(70, 2), (90, 1)]
    por_semana = armar_evolucion(viajes, None, zona, Periodo.todo)
    assert por_semana.agrupacion == Agrupacion.semana
    assert [(p.drivescore, p.viajes) for p in por_semana.puntos] == [(77, 3)]


# --- Utilidades de las pruebas con BD ---------------------------------------


async def flota(cliente, db, registrar_empresa):
    """Ana (25 km, 95), Bruno (24 km, 50, 6 eventos), Carla (15 km, 99), Diego (100 km, 80,
    10 eventos) y Elena (sin viajes; solo un viaje descartado)."""
    admin = await registrar_empresa(1)
    ids = {}
    for n, nombre in enumerate(("Ana", "Bruno", "Carla", "Diego", "Elena"), start=1):
        ids[nombre] = (await registrar_conductor(cliente, admin, n, nombre))["id"]
    await crear_viaje(db, ids["Ana"], 95, 2, distancia_m=25_000, eventos=[FRENADA])
    for dias in (3, 4):
        await crear_viaje(db, ids["Bruno"], 50, dias, distancia_m=12_000, eventos=[FRENADA] * 3)
    await crear_viaje(db, ids["Carla"], 99, 2, distancia_m=15_000)
    await crear_viaje(
        db, ids["Diego"], 80, 5, distancia_m=100_000, eventos=[TipoEvento.giro_agresivo] * 10
    )
    await crear_viaje(
        db, ids["Elena"], 10, 1, estado=EstadoRecorrido.descartado, eventos=[FRENADA]
    )
    return admin, ids


# --- Acceso ------------------------------------------------------------------


async def test_sin_token_responde_401(cliente):
    assert (await cliente.get(TABLERO)).status_code == 401


async def test_conductor_de_flota_e_individual_responden_403(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    creado = await registrar_conductor(cliente, admin, 1)
    de_flota = (
        await cliente.post(
            "/api/v1/auth/login",
            json={"email": creado["email"], "contrasenia": creado["contrasenia_temporal"]},
        )
    ).json()
    individual = (
        await cliente.post(
            "/api/v1/auth/registro",
            json={
                "nombre": "Solo",
                "email": "solo@empresa.example.com",
                "telefono": "72000000",
                "contrasenia": "Clave1234",
            },
        )
    ).json()
    for sesion in (de_flota, individual):
        assert (await cliente.get(TABLERO, headers=cabecera(sesion))).status_code == 403


async def test_una_empresa_no_ve_datos_de_otra(cliente, db, registrar_empresa):
    admin_a, _ = await flota(cliente, db, registrar_empresa)
    admin_b = await registrar_empresa(2)
    zoe = await registrar_conductor(cliente, admin_b, 10, "Zoe")
    await crear_viaje(db, zoe["id"], 70, 1, distancia_m=30_000, eventos=[FRENADA])

    tablero_b = await pedir(cliente, admin_b, periodo="todo")
    assert tablero_b["conductores"] == 1
    assert tablero_b["viajes"] == 1
    assert tablero_b["distancia_m"] == 30_000
    assert tablero_b["eventos"] == 1
    assert [i["nombre"] for i in tablero_b["mejores"]] == ["Zoe"]
    assert not any(n in str(tablero_b) for n in ("Ana", "Bruno", "Diego"))

    tablero_a = await pedir(cliente, admin_a, periodo="todo")
    assert "Zoe" not in str(tablero_a)
    assert tablero_a["conductores"] == 5


# --- Contenido ---------------------------------------------------------------


async def test_tarjetas_ponderadas_por_distancia_y_solo_finalizados(cliente, db, registrar_empresa):
    admin, _ = await flota(cliente, db, registrar_empresa)
    t = await pedir(cliente, admin, periodo="30d")

    assert t["conductores"] == 5
    assert t["conductores_con_viajes"] == 4
    assert t["viajes"] == 5  # el descartado no cuenta
    assert t["distancia_m"] == 164_000
    # (95·25 + 50·24 + 99·15 + 80·100) / 164 = 79,6 → 80 (no el 81 del promedio simple)
    assert (t["drivescore"], t["calificacion"]) == (80, "muy_bueno")
    assert t["eventos"] == 17
    assert t["eventos_por_10km"] == 1.0
    assert t["eventos_por_tipo"] == {
        "frenada_brusca": 7,
        "aceleracion_severa": 0,
        "giro_agresivo": 10,
        "exceso_velocidad": 0,
    }


async def test_rankings_con_distancia_minima_y_eventos_por_10_km(cliente, db, registrar_empresa):
    admin, _ = await flota(cliente, db, registrar_empresa)
    t = await pedir(cliente, admin, periodo="30d")

    assert t["distancia_minima_ranking_m"] == 20_000
    # Carla (15 km, 99) no entra en el ranking aunque tiene el mejor DriveScore
    assert [(i["nombre"], i["drivescore"]) for i in t["mejores"]] == [
        ("Ana", 95),
        ("Diego", 80),
        ("Bruno", 50),
    ]
    # Diego tiene más eventos en total (10 vs 6) pero menos por cada 10 km
    assert [(i["nombre"], i["eventos"], i["eventos_por_10km"]) for i in t["a_vigilar"]] == [
        ("Bruno", 6, 2.5),
        ("Diego", 10, 1.0),
        ("Ana", 1, 0.4),
    ]


async def test_distribucion_por_calificacion_incluye_a_quien_no_llega_al_minimo(
    cliente, db, registrar_empresa
):
    admin, _ = await flota(cliente, db, registrar_empresa)
    t = await pedir(cliente, admin, periodo="30d")
    assert t["por_calificacion"] == {
        "excelente": 2,  # Ana y Carla
        "muy_bueno": 1,
        "regular": 0,
        "riesgoso": 1,
        "sin_viajes": 1,
    }
    assert t["conductores_en_riesgo"] == 1


async def test_la_distancia_minima_es_inclusiva(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    justo = await registrar_conductor(cliente, admin, 1, "Justo")
    corto = await registrar_conductor(cliente, admin, 2, "Corto")
    await crear_viaje(db, justo["id"], 70, 1, distancia_m=20_000, eventos=[FRENADA])
    await crear_viaje(db, corto["id"], 99, 1, distancia_m=19_999, eventos=[FRENADA])
    t = await pedir(cliente, admin)
    assert [i["nombre"] for i in t["mejores"]] == ["Justo"]
    assert [i["nombre"] for i in t["a_vigilar"]] == ["Justo"]


async def test_conductor_dado_de_baja_no_cuenta(cliente, db, registrar_empresa):
    admin, ids = await flota(cliente, db, registrar_empresa)
    await db.execute(update(Usuario).where(Usuario.id == ids["Diego"]).values(activo=False))
    await db.commit()
    t = await pedir(cliente, admin, periodo="todo")
    assert t["conductores"] == 4
    assert t["distancia_m"] == 64_000
    assert "Diego" not in str(t)


async def test_sin_eventos_no_hay_conductores_a_vigilar(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], 90, 1, distancia_m=30_000)
    t = await pedir(cliente, admin)
    assert t["a_vigilar"] == []
    assert [i["nombre"] for i in t["mejores"]] == ["Ana"]
    assert t["eventos"] == 0
    assert t["eventos_por_10km"] == 0.0


async def test_periodos(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], 90, 2, distancia_m=10_000)
    await crear_viaje(db, ana["id"], 70, 20, distancia_m=10_000)
    await crear_viaje(db, ana["id"], 40, 60, distancia_m=10_000)

    assert (await pedir(cliente, admin, periodo="7d"))["viajes"] == 1
    treinta = await pedir(cliente, admin, periodo="30d")
    assert (treinta["viajes"], treinta["drivescore"]) == (2, 80)
    todo = await pedir(cliente, admin, periodo="todo")
    assert (todo["viajes"], todo["drivescore"]) == (3, 67)
    # Por defecto, 30 días
    assert (await pedir(cliente, admin))["viajes"] == 2


async def test_evolucion_por_dia_y_por_semana(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    bruno = await registrar_conductor(cliente, admin, 2, "Bruno")
    # Dos conductores el mismo día se promedian en un solo punto (ponderado por distancia)
    await crear_viaje(db, ana["id"], 90, 4, distancia_m=10_000)
    await crear_viaje(db, bruno["id"], 60, 4, distancia_m=10_000)
    await crear_viaje(db, ana["id"], 80, 2, distancia_m=10_000)

    semana = await pedir(cliente, admin, periodo="7d")
    assert semana["evolucion"]["agrupacion"] == "dia"
    assert [(p["drivescore"], p["viajes"]) for p in semana["evolucion"]["puntos"]] == [(75, 2), (80, 1)]

    todo = await pedir(cliente, admin, periodo="todo")
    assert todo["evolucion"]["agrupacion"] == "semana"
    assert sum(p["viajes"] for p in todo["evolucion"]["puntos"]) == 3


async def test_sin_conductores_responde_vacio(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    for periodo in ("7d", "30d", "todo"):
        t = await pedir(cliente, admin, periodo=periodo)
        assert (t["conductores"], t["viajes"], t["distancia_m"], t["eventos"]) == (0, 0, 0, 0)
        assert (t["drivescore"], t["calificacion"], t["eventos_por_10km"]) == (None, None, None)
        assert (t["mejores"], t["a_vigilar"], t["evolucion"]["puntos"]) == ([], [], [])
        assert sum(t["eventos_por_tipo"].values()) == 0
        assert t["por_calificacion"]["sin_viajes"] == 0


async def test_conductores_sin_viajes_responde_vacio(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    await registrar_conductor(cliente, admin, 1, "Elena")
    t = await pedir(cliente, admin)
    assert (t["conductores"], t["conductores_con_viajes"], t["viajes"]) == (1, 0, 0)
    assert t["drivescore"] is None
    assert t["por_calificacion"]["sin_viajes"] == 1
    assert t["evolucion"]["puntos"] == []


async def test_periodo_invalido_responde_422(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    respuesta = await cliente.get(TABLERO, headers=cabecera(admin), params={"periodo": "1y"})
    assert respuesta.status_code == 422
