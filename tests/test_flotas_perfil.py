"""HU-25: perfil de desempeño de un conductor de la flota (con base de datos)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.auth.models import Usuario
from app.flotas.schemas import NivelRiesgo
from app.flotas.service import nivel_de_riesgo, punto_debil, tendencia_por_mitades
from app.puntaje.service import Calificacion, ViajePuntuado
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento
from tests.conftest import cabecera
from tests.test_flotas import registrar_conductor

BASE = "/api/v1/flotas/conductores"


# --- Pruebas sin base de datos ----------------------------------------------


def test_nivel_de_riesgo_segun_la_calificacion():
    assert nivel_de_riesgo(Calificacion.riesgoso) == NivelRiesgo.alto
    assert nivel_de_riesgo(Calificacion.regular) == NivelRiesgo.medio
    assert nivel_de_riesgo(Calificacion.muy_bueno) == NivelRiesgo.bajo
    assert nivel_de_riesgo(Calificacion.excelente) == NivelRiesgo.bajo
    assert nivel_de_riesgo(None) is None


def test_punto_debil_es_la_categoria_con_menor_puntaje_entre_las_que_tuvieron_eventos():
    promedios = {"velocidad": 90, "frenadas": 60, "giros": 80, "aceleraciones": 100}
    conteo = dict.fromkeys(TipoEvento, 0) | {
        TipoEvento.frenada_brusca: 2,
        TipoEvento.giro_agresivo: 1,
    }
    assert punto_debil(promedios, conteo) == TipoEvento.frenada_brusca
    # Una categoria sin eventos no cuenta, aunque tenga menos puntaje
    assert punto_debil(promedios, conteo | {TipoEvento.frenada_brusca: 0}) == TipoEvento.giro_agresivo


def test_punto_debil_en_empate_gana_el_de_mayor_riesgo():
    promedios = {"velocidad": 70, "frenadas": 70, "giros": 70, "aceleraciones": 70}
    conteo = dict.fromkeys(TipoEvento, 1)
    assert punto_debil(promedios, conteo) == TipoEvento.exceso_velocidad
    sin_exceso = conteo | {TipoEvento.exceso_velocidad: 0}
    assert punto_debil(promedios, sin_exceso) == TipoEvento.frenada_brusca


def test_sin_eventos_no_hay_punto_debil():
    promedios = {"velocidad": 100, "frenadas": 100, "giros": 100, "aceleraciones": 100}
    assert punto_debil(promedios, dict.fromkeys(TipoEvento, 0)) is None


def _viaje(dias_atras: float, drivescore: int, distancia_m: float = 10_000) -> ViajePuntuado:
    inicio = AHORA - timedelta(days=dias_atras)
    return ViajePuntuado(1, inicio, distancia_m, drivescore, drivescore, drivescore, drivescore, drivescore)


AHORA = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def test_tendencia_por_mitades_compara_la_segunda_mitad_con_la_primera():
    # El periodo va de hace 40 días a ahora: la mitad es hace 20 días
    viajes = [_viaje(40, 60), _viaje(30, 70), _viaje(10, 90), _viaje(2, 80)]
    # Primera mitad: (60 + 70) / 2 = 65; segunda: (90 + 80) / 2 = 85
    assert tendencia_por_mitades(viajes, AHORA) == 20


def test_tendencia_por_mitades_pondera_por_distancia():
    viajes = [_viaje(40, 50, 30_000), _viaje(35, 90, 10_000), _viaje(5, 80, 10_000)]
    # Primera: (50*30 + 90*10) / 40 = 60; segunda: 80
    assert tendencia_por_mitades(viajes, AHORA) == 20


def test_tendencia_por_mitades_puede_ser_negativa():
    assert tendencia_por_mitades([_viaje(40, 90), _viaje(2, 70)], AHORA) == -20


def test_tendencia_por_mitades_es_nula_si_una_mitad_no_tiene_viajes_o_hay_menos_de_dos():
    assert tendencia_por_mitades([], AHORA) is None
    assert tendencia_por_mitades([_viaje(10, 80)], AHORA) is None
    # Todos los viajes en la misma mitad no puede pasar por construcción (el primero marca el
    # inicio), pero dos viajes pegados al inicio dejan la segunda mitad vacía
    juntos = [_viaje(40, 80), _viaje(39.9, 70)]
    assert tendencia_por_mitades(juntos, AHORA) is None


# --- Utilidades --------------------------------------------------------------


async def crear_viaje(
    db,
    usuario_id: int,
    drivescore: int,
    dias_atras: float,
    distancia_m: float = 10_000,
    categorias: dict | None = None,
    eventos: list[TipoEvento] | None = None,
    estado: EstadoRecorrido = EstadoRecorrido.finalizado,
) -> Recorrido:
    cat = {"frenadas": drivescore, "aceleraciones": drivescore, "giros": drivescore, "velocidad": drivescore}
    cat |= categorias or {}
    inicio = datetime.now(UTC) - timedelta(days=dias_atras)
    viaje = Recorrido(
        usuario_id=usuario_id,
        estado=estado,
        fecha_inicio=inicio,
        fecha_fin=inicio + timedelta(minutes=20),
        distancia_m=distancia_m,
        duracion_s=1200,
        velocidad_maxima_kmh=60,
        velocidad_promedio_kmh=30,
        lat_inicio=-17.78,
        lon_inicio=-63.18,
        lat_fin=-17.79,
        lon_fin=-63.19,
        ruta=[{"lat": -17.78, "lon": -63.18, "fecha": inicio.isoformat(), "velocidad_kmh": 30}],
        drivescore=drivescore,
        puntaje_frenadas=cat["frenadas"],
        puntaje_aceleraciones=cat["aceleraciones"],
        puntaje_giros=cat["giros"],
        puntaje_velocidad=cat["velocidad"],
    )
    db.add(viaje)
    await db.flush()
    for tipo in eventos or []:
        extra = (
            {"duracion_s": 10, "velocidad_maxima_kmh": 80} if tipo == TipoEvento.exceso_velocidad else {}
        )
        db.add(
            Evento(
                recorrido_id=viaje.id,
                tipo=tipo,
                fecha=inicio + timedelta(minutes=5),
                lat=-17.78,
                lon=-63.18,
                velocidad_kmh=40,
                intensidad=4.0,
                **extra,
            )
        )
    await db.commit()
    return viaje


async def perfil(cliente, sesion, conductor_id: int, **parametros) -> dict:
    respuesta = await cliente.get(f"{BASE}/{conductor_id}", headers=cabecera(sesion), params=parametros)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def escenario(cliente, db, registrar_empresa):
    """Un admin con un conductor con tres viajes (dos recientes y uno de hace 40 días)."""
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    # 10 km con 90 y 30 km con 70 → 75 ponderado (no el 80 del promedio simple)
    await crear_viaje(
        db, ana["id"], 90, dias_atras=2,
        categorias={"frenadas": 80},
        eventos=[TipoEvento.frenada_brusca, TipoEvento.frenada_brusca],
    )
    await crear_viaje(
        db, ana["id"], 70, dias_atras=5, distancia_m=30_000,
        categorias={"frenadas": 60, "giros": 90, "velocidad": 80, "aceleraciones": 100},
        eventos=[TipoEvento.giro_agresivo],
    )
    # Fuera de los 30 días: es el periodo anterior (tendencia) y parte de "todo"
    await crear_viaje(db, ana["id"], 60, dias_atras=40)
    return admin, ana


# --- Acceso ------------------------------------------------------------------


def _rutas(conductor_id: int = 1, viaje_id: int = 1) -> list[str]:
    return [
        f"{BASE}/{conductor_id}",
        f"{BASE}/{conductor_id}/recorridos",
        f"{BASE}/{conductor_id}/recorridos/{viaje_id}",
    ]


async def test_sin_token_responde_401(cliente):
    for ruta in _rutas():
        assert (await cliente.get(ruta)).status_code == 401


async def test_conductor_de_flota_y_conductor_individual_responden_403(cliente, registrar_empresa):
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
        for ruta in _rutas(creado["id"]):
            assert (await cliente.get(ruta, headers=cabecera(sesion))).status_code == 403


# --- Aislamiento entre empresas ----------------------------------------------


async def test_una_empresa_no_ve_conductores_de_otra(cliente, db, registrar_empresa):
    admin_a = await registrar_empresa(1)
    admin_b = await registrar_empresa(2)
    de_b = await registrar_conductor(cliente, admin_b, 2, "Beto de B")
    viaje = await crear_viaje(db, de_b["id"], 80, dias_atras=1, eventos=[TipoEvento.giro_agresivo])

    for ruta in _rutas(de_b["id"], viaje.id):
        respuesta = await cliente.get(ruta, headers=cabecera(admin_a))
        assert respuesta.status_code == 404, ruta
        # No se filtra ningún dato del conductor ajeno
        assert "Beto" not in respuesta.text
    # Su propio admin sí lo ve
    assert (await perfil(cliente, admin_b, de_b["id"]))["conductor"]["nombre"] == "Beto de B"
    assert (
        await cliente.get(_rutas(de_b["id"], viaje.id)[2], headers=cabecera(admin_b))
    ).status_code == 200


async def test_un_viaje_ajeno_no_se_abre_con_el_id_de_otro_conductor(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    beto = await registrar_conductor(cliente, admin, 2, "Beto")
    viaje_de_beto = await crear_viaje(db, beto["id"], 80, dias_atras=1)
    # Mismo admin, pero el viaje no es de Ana
    respuesta = await cliente.get(f"{BASE}/{ana['id']}/recorridos/{viaje_de_beto.id}", headers=cabecera(admin))
    assert respuesta.status_code == 404
    ok = await cliente.get(f"{BASE}/{beto['id']}/recorridos/{viaje_de_beto.id}", headers=cabecera(admin))
    assert ok.status_code == 200


async def test_inactivos_admins_e_inexistentes_responden_404(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    usuario = await db.get(Usuario, ana["id"])
    usuario.activo = False
    await db.commit()
    for conductor_id in (ana["id"], admin["usuario"]["id"], 9999):
        assert (await cliente.get(f"{BASE}/{conductor_id}", headers=cabecera(admin))).status_code == 404


# --- Perfil ------------------------------------------------------------------


async def test_perfil_calcula_promedios_ponderados_riesgo_y_eventos(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    datos = await perfil(cliente, admin, ana["id"], periodo="30d")

    assert datos["conductor"] == {"id": ana["id"], "nombre": "Ana", "email": ana["email"]}
    assert datos["viajes"] == 2
    assert datos["distancia_m"] == 40_000
    assert datos["drivescore"] == 75
    assert datos["calificacion"] == "muy_bueno"
    assert datos["nivel_riesgo"] == "bajo"
    assert datos["tendencia"] == 15  # 75 frente a los 60 del periodo anterior
    # Ponderadas por distancia: frenadas (80*10 + 60*30) / 40 = 65; velocidad (90*10 + 80*30) / 40 = 82,5 → 83
    assert datos["categorias"]["frenadas"]["promedio"] == 65
    assert datos["categorias"]["giros"]["promedio"] == 90
    assert datos["categorias"]["velocidad"]["promedio"] == 83
    assert datos["categorias"]["aceleraciones"]["promedio"] == 98
    assert datos["categorias"]["frenadas"]["diferencia"] == 5  # 65 frente a 60
    assert datos["punto_debil"] == "frenada_brusca"
    assert datos["eventos_por_tipo"] == {
        "frenada_brusca": 2,
        "aceleracion_severa": 0,
        "giro_agresivo": 1,
        "exceso_velocidad": 0,
    }
    assert datos["eventos"] == 3
    assert datos["eventos_por_10km"] == 0.8  # 3 eventos en 40 km


async def test_el_periodo_recalcula_todo(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)

    siete = await perfil(cliente, admin, ana["id"], periodo="7d")
    assert siete["viajes"] == 2 and siete["drivescore"] == 75
    assert siete["tendencia"] is None  # nada en los 7 días anteriores
    assert siete["tendencia_base"] == "periodo_anterior"
    assert siete["agrupacion"] == "viaje"
    assert [p["drivescore"] for p in siete["evolucion"]] == [70, 90]  # cronológico

    treinta = await perfil(cliente, admin, ana["id"], periodo="30d")
    assert treinta["agrupacion"] == "dia"
    assert treinta["tendencia_base"] == "periodo_anterior"
    assert [p["drivescore"] for p in treinta["evolucion"]] == [70, 90]

    todo = await perfil(cliente, admin, ana["id"], periodo="todo")
    assert todo["viajes"] == 3
    assert todo["distancia_m"] == 50_000
    assert todo["drivescore"] == 72  # (90*10 + 70*30 + 60*10) / 50 = 72
    # "todo" compara mitades: hace 40 días a hoy se parte en hace 20 días; la primera mitad es el
    # viaje de 60 y la segunda, el 75 ponderado de los otros dos
    assert todo["tendencia_base"] == "mitades"
    assert todo["tendencia"] == 15
    assert todo["agrupacion"] == "semana"
    assert sum(p["viajes"] for p in todo["evolucion"]) == 3


async def test_perfil_por_defecto_es_todo_y_valida_el_periodo(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    assert (await perfil(cliente, admin, ana["id"]))["viajes"] == 3
    malo = await cliente.get(f"{BASE}/{ana['id']}", headers=cabecera(admin), params={"periodo": "1h"})
    assert malo.status_code == 422


async def test_perfil_sin_viajes_en_el_periodo(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    sin = await registrar_conductor(cliente, admin, 1, "Sin viajes")
    for periodo in ("7d", "30d", "todo"):
        datos = await perfil(cliente, admin, sin["id"], periodo=periodo)
        assert datos["viajes"] == 0
        assert datos["drivescore"] is None
        assert datos["calificacion"] is None
        assert datos["nivel_riesgo"] is None
        assert datos["tendencia"] is None
        assert datos["tendencia_base"] == ("mitades" if periodo == "todo" else "periodo_anterior")
        assert datos["categorias"] is None
        assert datos["punto_debil"] is None
        assert datos["eventos"] == 0 and datos["eventos_por_10km"] is None
        assert datos["evolucion"] == []

    # Con viajes antiguos, los 7 días quedan vacíos pero "todo" no
    await crear_viaje(db, sin["id"], 80, dias_atras=20)
    assert (await perfil(cliente, admin, sin["id"], periodo="7d"))["viajes"] == 0
    assert (await perfil(cliente, admin, sin["id"], periodo="todo"))["viajes"] == 1


async def test_riesgo_segun_el_drivescore(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    esperado = {95: "bajo", 80: "bajo", 65: "medio", 40: "alto"}
    for numero, (puntaje, riesgo) in enumerate(esperado.items(), start=1):
        conductor = await registrar_conductor(cliente, admin, numero)
        await crear_viaje(db, conductor["id"], puntaje, dias_atras=1)
        assert (await perfil(cliente, admin, conductor["id"]))["nivel_riesgo"] == riesgo


async def test_solo_cuentan_viajes_finalizados_con_puntaje(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], 80, dias_atras=1)
    descartado = await crear_viaje(db, ana["id"], 10, dias_atras=1, estado=EstadoRecorrido.descartado)
    # Finalizado antes de HU-15: sin DriveScore
    anterior = await crear_viaje(db, ana["id"], 10, dias_atras=2)
    anterior.drivescore = None
    anterior.puntaje_frenadas = anterior.puntaje_giros = None
    anterior.puntaje_aceleraciones = anterior.puntaje_velocidad = None
    await db.commit()

    datos = await perfil(cliente, admin, ana["id"])
    assert datos["viajes"] == 1 and datos["drivescore"] == 80
    viajes = (await cliente.get(f"{BASE}/{ana['id']}/recorridos", headers=cabecera(admin))).json()
    assert [v["id"] for v in viajes] != [descartado.id]
    assert len(viajes) == 1


# --- Viajes recientes --------------------------------------------------------


async def test_viajes_recientes_ordenados_con_limite_y_periodo(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    ids = {}
    for dias, puntaje in ((1, 95), (3, 80), (6, 62), (20, 40)):
        ids[dias] = (await crear_viaje(db, ana["id"], puntaje, dias_atras=dias)).id

    todos = (await cliente.get(f"{BASE}/{ana['id']}/recorridos", headers=cabecera(admin))).json()
    assert [v["id"] for v in todos] == [ids[1], ids[3], ids[6], ids[20]]
    assert [v["calificacion"] for v in todos] == ["excelente", "muy_bueno", "regular", "riesgoso"]
    assert {"fecha_inicio", "distancia_m", "duracion_s", "drivescore"} <= set(todos[0])

    siete = (
        await cliente.get(f"{BASE}/{ana['id']}/recorridos", headers=cabecera(admin), params={"periodo": "7d"})
    ).json()
    assert [v["id"] for v in siete] == [ids[1], ids[3], ids[6]]

    dos = (
        await cliente.get(f"{BASE}/{ana['id']}/recorridos", headers=cabecera(admin), params={"limite": 2})
    ).json()
    assert [v["id"] for v in dos] == [ids[1], ids[3]]
    assert (
        await cliente.get(f"{BASE}/{ana['id']}/recorridos", headers=cabecera(admin), params={"limite": 0})
    ).status_code == 422


async def test_detalle_del_viaje_trae_ruta_eventos_y_desglose(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    viaje = await crear_viaje(
        db, ana["id"], 82, dias_atras=1, categorias={"frenadas": 70},
        eventos=[TipoEvento.frenada_brusca, TipoEvento.exceso_velocidad],
    )
    respuesta = await cliente.get(f"{BASE}/{ana['id']}/recorridos/{viaje.id}", headers=cabecera(admin))
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["drivescore"] == 82
    assert datos["puntaje_frenadas"] == 70
    assert len(datos["ruta"]) == 1
    assert len(datos["eventos"]) == 2
    assert datos["eventos_por_tipo"]["frenada_brusca"] == 1
    assert datos["eventos_por_tipo"]["exceso_velocidad"] == 1

    # Descartado o inexistente: 404
    descartado = await crear_viaje(db, ana["id"], 10, dias_atras=1, estado=EstadoRecorrido.descartado)
    for viaje_id in (descartado.id, 9999):
        assert (
            await cliente.get(f"{BASE}/{ana['id']}/recorridos/{viaje_id}", headers=cabecera(admin))
        ).status_code == 404


@pytest.mark.parametrize("periodo", ["7d", "30d", "todo"])
async def test_el_resumen_de_flota_coincide_con_el_perfil_de_un_solo_conductor(
    cliente, db, registrar_empresa, periodo
):
    """Con un único conductor, el promedio de la flota (HU-24) y el de su perfil son el mismo."""
    admin, ana = await escenario(cliente, db, registrar_empresa)
    resumen = (await cliente.get("/api/v1/flotas/resumen", headers=cabecera(admin), params={"periodo": periodo})).json()
    assert resumen["drivescore_promedio"] == (await perfil(cliente, admin, ana["id"], periodo=periodo))["drivescore"]
