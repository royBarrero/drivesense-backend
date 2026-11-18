"""HU-26: historial de eventos de riesgo de un conductor de la flota (con base de datos)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.flotas import service
from app.flotas.schemas import FranjaHoraria, PuntoEvento
from app.flotas.service import (
    MINIMO_EVENTOS_PATRON,
    agrupar_zonas,
    distancia_m,
    franja_de,
    resumir_patrones,
)
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento
from tests.conftest import cabecera
from tests.test_flotas import registrar_conductor

BASE = "/api/v1/flotas/conductores"
LA_PAZ = ZoneInfo("America/La_Paz")
FR, AC, GI, EX = (
    TipoEvento.frenada_brusca,
    TipoEvento.aceleracion_severa,
    TipoEvento.giro_agresivo,
    TipoEvento.exceso_velocidad,
)
# Un grado de latitud son ~111,2 km: 0,00018 grados son ~20 m
GRADO_20M = 0.00018


# --- Pruebas sin base de datos ----------------------------------------------


@pytest.mark.parametrize(
    ("hora", "franja"),
    [
        (0, FranjaHoraria.noche),
        (5, FranjaHoraria.noche),
        (6, FranjaHoraria.manana),
        (11, FranjaHoraria.manana),
        (12, FranjaHoraria.tarde),
        (18, FranjaHoraria.tarde),
        (19, FranjaHoraria.noche),
        (23, FranjaHoraria.noche),
    ],
)
def test_franjas_horarias(hora, franja):
    assert franja_de(hora) == franja


def _utc(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Hora de La Paz (UTC-4) de un dia de noviembre de 2026, expresada en UTC."""
    return datetime(2026, 11, dia, hora, minuto, tzinfo=LA_PAZ).astimezone(UTC)


def test_sin_suficientes_eventos_no_hay_patron_pero_si_conteos():
    resumen = resumir_patrones([(FR, _utc(13, 17)), (GI, _utc(13, 18))], LA_PAZ)
    assert resumen.total == 2
    assert resumen.por_tipo[FR] == 1 and resumen.por_tipo[EX] == 0
    patrones = resumen.patrones
    assert not patrones.hay_patron
    assert patrones.minimo == MINIMO_EVENTOS_PATRON == 10
    assert patrones.tipo_mas_frecuente is None
    assert patrones.dia_mas_frecuente is None
    assert patrones.franja_mas_frecuente is None
    assert sum(patrones.por_dia) == 2


def test_patron_con_el_minimo_de_eventos():
    # 13 de noviembre de 2026 = viernes; 10 de noviembre = martes
    eventos = [(FR, _utc(13, 17))] * 4 + [(AC, _utc(13, 12))] * 3 + [(EX, _utc(10, 8))] * 3
    patrones = resumir_patrones(eventos, LA_PAZ).patrones
    assert patrones.hay_patron
    assert patrones.tipo_mas_frecuente == FR
    assert patrones.dia_mas_frecuente == 5  # viernes
    assert patrones.franja_mas_frecuente == FranjaHoraria.tarde
    assert patrones.por_dia == [0, 3, 0, 0, 7, 0, 0]
    assert patrones.por_franja == {
        FranjaHoraria.manana: 3,
        FranjaHoraria.tarde: 7,
        FranjaHoraria.noche: 0,
    }
    # Con 9 eventos todavia no
    assert not resumir_patrones(eventos[:9], LA_PAZ).patrones.hay_patron


def test_empates_gana_el_de_mayor_riesgo_el_dia_mas_cercano_al_lunes_y_la_franja_mas_temprana():
    # 5 excesos el martes por la tarde y 5 frenadas el jueves por la manana
    eventos = [(EX, _utc(10, 15))] * 5 + [(FR, _utc(12, 9))] * 5
    patrones = resumir_patrones(eventos, LA_PAZ).patrones
    assert patrones.tipo_mas_frecuente == EX  # el exceso va primero en ORDEN_RIESGO
    assert patrones.dia_mas_frecuente == 2  # martes antes que jueves
    assert patrones.franja_mas_frecuente == FranjaHoraria.manana  # 5 y 5: la mas temprana


def test_el_dia_y_la_franja_usan_la_zona_horaria_de_la_empresa():
    # 02:00 UTC del domingo 15 son las 22:00 del sabado 14 en La Paz
    evento = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
    en_la_paz = resumir_patrones([(FR, evento)] * 10, LA_PAZ).patrones
    assert en_la_paz.dia_mas_frecuente == 6  # sabado
    assert en_la_paz.franja_mas_frecuente == FranjaHoraria.noche
    en_utc = resumir_patrones([(FR, evento)] * 10, ZoneInfo("UTC")).patrones
    assert en_utc.dia_mas_frecuente == 7  # domingo
    assert en_utc.franja_mas_frecuente == FranjaHoraria.noche


def _punto(tipo, lat, lon, id_=1) -> PuntoEvento:
    return PuntoEvento(id=id_, recorrido_id=1, tipo=tipo, lat=lat, lon=lon, fecha=_utc(13, 10))


def test_distancia_en_metros():
    assert distancia_m(-17.78, -63.18, -17.78, -63.18) == 0
    assert 19 < distancia_m(-17.78, -63.18, -17.78 + GRADO_20M, -63.18) < 21


def test_zona_con_eventos_del_mismo_tipo_cercanos():
    puntos = [
        _punto(FR, -17.78, -63.18, 1),
        _punto(FR, -17.78 + GRADO_20M, -63.18, 2),
        _punto(FR, -17.78, -63.18 + GRADO_20M, 3),
    ]
    zonas = agrupar_zonas(puntos)
    assert len(zonas) == 1
    assert zonas[0].tipo == FR and zonas[0].eventos == 3
    assert zonas[0].radio_m == 50
    assert abs(zonas[0].lat - (-17.78 + GRADO_20M / 3)) < 1e-9


def test_no_hay_zona_si_estan_lejos_si_son_de_otro_tipo_o_si_es_uno_solo():
    lejos = [_punto(FR, -17.78, -63.18), _punto(FR, -17.78 + 4 * GRADO_20M, -63.18, 2)]  # ~80 m
    assert agrupar_zonas(lejos) == []
    mezclados = [_punto(FR, -17.78, -63.18), _punto(GI, -17.78, -63.18, 2)]
    assert agrupar_zonas(mezclados) == []
    assert agrupar_zonas([_punto(FR, -17.78, -63.18)]) == []
    assert agrupar_zonas([]) == []


def test_varias_zonas_por_tipo_y_un_evento_suelto_no_cuenta():
    puntos = [
        _punto(FR, -17.78, -63.18, 1),
        _punto(FR, -17.78 + GRADO_20M, -63.18, 2),
        _punto(FR, -17.70, -63.10, 3),  # suelto, lejos
        _punto(EX, -17.70, -63.10, 4),
        _punto(EX, -17.70 + GRADO_20M, -63.10, 5),
    ]
    zonas = agrupar_zonas(puntos)
    assert sorted((z.tipo.value, z.eventos) for z in zonas) == [
        ("exceso_velocidad", 2),
        ("frenada_brusca", 2),
    ]


# --- Utilidades --------------------------------------------------------------


async def crear_viaje(
    db,
    usuario_id: int,
    eventos: list[dict],
    estado: EstadoRecorrido = EstadoRecorrido.finalizado,
    drivescore: int | None = 80,
    inicio: datetime | None = None,
) -> Recorrido:
    """Viaje con sus eventos; cada evento dice `tipo`, `dias` atras y `hora` UTC (o `fecha`)."""
    primero = min(_fecha_de(e) for e in eventos) if eventos else datetime.now(UTC)
    inicio = inicio or primero - timedelta(minutes=10)
    viaje = Recorrido(
        usuario_id=usuario_id,
        estado=estado,
        fecha_inicio=inicio,
        fecha_fin=inicio + timedelta(minutes=30),
        distancia_m=10_000,
        duracion_s=1800,
        velocidad_maxima_kmh=60,
        velocidad_promedio_kmh=30,
        lat_inicio=-17.78,
        lon_inicio=-63.18,
        drivescore=drivescore,
        puntaje_frenadas=drivescore,
        puntaje_aceleraciones=drivescore,
        puntaje_giros=drivescore,
        puntaje_velocidad=drivescore,
    )
    db.add(viaje)
    await db.flush()
    for e in eventos:
        exceso = e["tipo"] == EX
        db.add(
            Evento(
                recorrido_id=viaje.id,
                tipo=e["tipo"],
                fecha=_fecha_de(e),
                lat=e.get("lat", -17.78),
                lon=e.get("lon", -63.18),
                velocidad_kmh=40,
                intensidad=3.6,
                duracion_s=12 if exceso else None,
                velocidad_maxima_kmh=71 if exceso else None,
            )
        )
    await db.commit()
    return viaje


def _fecha_de(evento: dict) -> datetime:
    if "fecha" in evento:
        return evento["fecha"]
    base = datetime.now(UTC) - timedelta(days=evento["dias"])
    return base.replace(hour=evento.get("hora", 15), minute=evento.get("minuto", 0), second=0, microsecond=0)


async def obtener(cliente, sesion, ruta: str, **parametros):
    respuesta = await cliente.get(ruta, headers=cabecera(sesion), params=parametros)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _rutas(conductor_id: int = 1) -> list[str]:
    return [
        f"{BASE}/{conductor_id}/eventos",
        f"{BASE}/{conductor_id}/eventos/resumen",
        f"{BASE}/{conductor_id}/eventos/mapa",
    ]


# --- Acceso ------------------------------------------------------------------


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


async def test_una_empresa_no_ve_eventos_de_conductores_de_otra(cliente, db, registrar_empresa):
    admin_a = await registrar_empresa(1)
    admin_b = await registrar_empresa(2)
    de_b = await registrar_conductor(cliente, admin_b, 2, "Beto de B")
    await crear_viaje(db, de_b["id"], [{"tipo": FR, "dias": 1}, {"tipo": FR, "dias": 1, "minuto": 5}])

    for ruta in _rutas(de_b["id"]):
        respuesta = await cliente.get(ruta, headers=cabecera(admin_a))
        assert respuesta.status_code == 404, ruta
        assert "frenada" not in respuesta.text and "Beto" not in respuesta.text
    # Su propio admin si los ve
    assert (await obtener(cliente, admin_b, _rutas(de_b["id"])[0]))["total"] == 2
    assert (await obtener(cliente, admin_b, _rutas(de_b["id"])[1]))["total"] == 2
    assert len((await obtener(cliente, admin_b, _rutas(de_b["id"])[2]))["puntos"]) == 2


async def test_inactivos_admins_e_inexistentes_responden_404(cliente, db, registrar_empresa):
    from app.auth.models import Usuario

    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    usuario = await db.get(Usuario, ana["id"])
    usuario.activo = False
    await db.commit()
    for conductor_id in (ana["id"], admin["usuario"]["id"], 9999):
        for ruta in _rutas(conductor_id):
            assert (await cliente.get(ruta, headers=cabecera(admin))).status_code == 404


# --- Historial ---------------------------------------------------------------


async def test_historial_del_mas_reciente_al_mas_antiguo_con_dia_y_hora_de_la_empresa(
    cliente, db, registrar_empresa
):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    # 02:00 UTC del 12 son las 22:00 del 11 en La Paz (la empresa usa America/La_Paz)
    tarde_utc = datetime(2026, 11, 12, 2, 0, tzinfo=UTC)
    viaje = await crear_viaje(
        db,
        ana["id"],
        [
            {"tipo": FR, "fecha": tarde_utc},
            {"tipo": EX, "fecha": tarde_utc + timedelta(hours=20)},  # 22:00 UTC = 18:00 del 12
            {"tipo": GI, "fecha": tarde_utc - timedelta(days=1)},
        ],
        inicio=tarde_utc - timedelta(days=1, minutes=10),
    )
    datos = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos")
    assert datos["total"] == 3
    assert [e["tipo"] for e in datos["eventos"]] == ["exceso_velocidad", "frenada_brusca", "giro_agresivo"]
    exceso, frenada, giro = datos["eventos"]
    assert (exceso["dia"], exceso["hora"]) == ("2026-11-12", "18:00")
    assert (frenada["dia"], frenada["hora"]) == ("2026-11-11", "22:00")  # el dia anterior en La Paz
    assert (giro["dia"], giro["hora"]) == ("2026-11-10", "22:00")
    assert exceso["recorrido_id"] == viaje.id
    assert exceso["duracion_s"] == 12 and exceso["velocidad_maxima_kmh"] == 71
    assert frenada["duracion_s"] is None
    assert {"intensidad", "velocidad_kmh", "lat", "lon", "fecha", "id"} <= set(frenada)


async def test_solo_cuentan_eventos_de_viajes_finalizados_con_puntaje(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], [{"tipo": FR, "dias": 1}])
    await crear_viaje(db, ana["id"], [{"tipo": GI, "dias": 1}], estado=EstadoRecorrido.descartado)
    await crear_viaje(db, ana["id"], [{"tipo": AC, "dias": 1}], drivescore=None)

    datos = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos")
    assert [e["tipo"] for e in datos["eventos"]] == ["frenada_brusca"]
    assert (await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/resumen"))["total"] == 1
    assert len((await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/mapa"))["puntos"]) == 1


async def test_filtros_de_periodo_y_de_tipo(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], [{"tipo": FR, "dias": 2}, {"tipo": GI, "dias": 2, "minuto": 5}])
    await crear_viaje(db, ana["id"], [{"tipo": FR, "dias": 20}])
    await crear_viaje(db, ana["id"], [{"tipo": EX, "dias": 60}])
    ruta = f"{BASE}/{ana['id']}/eventos"

    assert (await obtener(cliente, admin, ruta, periodo="7d"))["total"] == 2
    assert (await obtener(cliente, admin, ruta, periodo="30d"))["total"] == 3
    assert (await obtener(cliente, admin, ruta, periodo="todo"))["total"] == 4
    assert (await obtener(cliente, admin, ruta))["total"] == 4  # por defecto, todo

    frenadas = await obtener(cliente, admin, ruta, periodo="30d", tipo="frenada_brusca")
    assert frenadas["total"] == 2 and {e["tipo"] for e in frenadas["eventos"]} == {"frenada_brusca"}
    assert (await obtener(cliente, admin, ruta, tipo="exceso_velocidad"))["total"] == 1
    assert (await obtener(cliente, admin, ruta, periodo="7d", tipo="exceso_velocidad"))["total"] == 0

    for malo in ({"periodo": "1h"}, {"tipo": "choque"}, {"limite": 0}, {"limite": 51}, {"pagina": 0}):
        assert (await cliente.get(ruta, headers=cabecera(admin), params=malo)).status_code == 422


async def test_paginacion(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(
        db, ana["id"], [{"tipo": FR, "dias": 1, "hora": 10 + i} for i in range(5)]
    )
    ruta = f"{BASE}/{ana['id']}/eventos"
    uno = await obtener(cliente, admin, ruta, limite=2, pagina=1)
    dos = await obtener(cliente, admin, ruta, limite=2, pagina=2)
    tres = await obtener(cliente, admin, ruta, limite=2, pagina=3)
    cuatro = await obtener(cliente, admin, ruta, limite=2, pagina=4)
    assert uno["total"] == dos["total"] == tres["total"] == 5
    horas = [e["fecha"] for p in (uno, dos, tres) for e in p["eventos"]]
    assert len(horas) == 5 and horas == sorted(horas, reverse=True)
    assert len(tres["eventos"]) == 1 and cuatro["eventos"] == []


async def test_el_total_de_eventos_coincide_con_el_del_perfil(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], [{"tipo": FR, "dias": 2}, {"tipo": EX, "dias": 3}])
    await crear_viaje(db, ana["id"], [{"tipo": GI, "dias": 25}])
    for periodo in ("7d", "30d", "todo"):
        perfil = await obtener(cliente, admin, f"{BASE}/{ana['id']}", periodo=periodo)
        resumen = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/resumen", periodo=periodo)
        historial = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos", periodo=periodo)
        assert resumen["total"] == historial["total"] == perfil["eventos"]
        assert resumen["por_tipo"] == perfil["eventos_por_tipo"]


async def test_sin_eventos(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    assert await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos") == {"eventos": [], "total": 0}
    resumen = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/resumen")
    assert resumen["total"] == 0 and set(resumen["por_tipo"].values()) == {0}
    assert resumen["patrones"]["hay_patron"] is False
    assert await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/mapa") == {
        "puntos": [],
        "zonas": [],
        "truncado": False,
    }


# --- Resumen y patrones ------------------------------------------------------


async def test_resumen_con_pocos_eventos_no_tiene_patron(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], [{"tipo": FR, "dias": 1}, {"tipo": GI, "dias": 1, "minuto": 5}])
    datos = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/resumen")
    assert datos["total"] == 2
    assert datos["por_tipo"] == {
        "frenada_brusca": 1,
        "aceleracion_severa": 0,
        "giro_agresivo": 1,
        "exceso_velocidad": 0,
    }
    patrones = datos["patrones"]
    assert patrones["hay_patron"] is False and patrones["minimo"] == 10
    assert patrones["tipo_mas_frecuente"] is None
    assert patrones["dia_mas_frecuente"] is None and patrones["franja_mas_frecuente"] is None


async def test_resumen_con_patron_en_la_zona_de_la_empresa(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    # Un mismo dia del que se pueda hablar de la franja: 20:00-21:00 UTC = 16:00-17:00 en La Paz
    base = datetime.now(UTC).replace(hour=20, minute=0, second=0, microsecond=0) - timedelta(days=2)
    await crear_viaje(
        db,
        ana["id"],
        [{"tipo": FR, "fecha": base + timedelta(minutes=i)} for i in range(6)]
        + [{"tipo": GI, "fecha": base + timedelta(minutes=10 + i)} for i in range(4)],
    )
    datos = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/resumen", periodo="7d")
    patrones = datos["patrones"]
    assert patrones["hay_patron"] is True
    assert patrones["tipo_mas_frecuente"] == "frenada_brusca"
    assert patrones["franja_mas_frecuente"] == "tarde"
    dia_local = (base - timedelta(hours=4)).isoweekday()
    assert patrones["dia_mas_frecuente"] == dia_local
    assert patrones["por_dia"][dia_local - 1] == 10 and sum(patrones["por_dia"]) == 10
    assert patrones["por_franja"] == {"manana": 0, "tarde": 10, "noche": 0}


# --- Mapa --------------------------------------------------------------------


async def test_mapa_con_puntos_y_zonas(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(
        db,
        ana["id"],
        [
            {"tipo": FR, "dias": 1, "minuto": 0, "lat": -17.78, "lon": -63.18},
            {"tipo": FR, "dias": 1, "minuto": 1, "lat": -17.78 + GRADO_20M, "lon": -63.18},
            {"tipo": GI, "dias": 1, "minuto": 2, "lat": -17.78, "lon": -63.18},  # mismo lugar, otro tipo
            {"tipo": EX, "dias": 1, "minuto": 3, "lat": -17.70, "lon": -63.10},  # suelto
        ],
    )
    datos = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/mapa")
    assert len(datos["puntos"]) == 4 and datos["truncado"] is False
    assert {"id", "recorrido_id", "tipo", "lat", "lon", "fecha"} <= set(datos["puntos"][0])
    assert len(datos["zonas"]) == 1
    zona = datos["zonas"][0]
    assert zona["tipo"] == "frenada_brusca" and zona["eventos"] == 2 and zona["radio_m"] == 50

    solo_giros = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/mapa", tipo="giro_agresivo")
    assert [p["tipo"] for p in solo_giros["puntos"]] == ["giro_agresivo"] and solo_giros["zonas"] == []


async def test_el_mapa_devuelve_como_maximo_los_eventos_mas_recientes(
    cliente, db, registrar_empresa, monkeypatch
):
    monkeypatch.setattr(service, "MAXIMO_PUNTOS_MAPA", 3)
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], [{"tipo": FR, "dias": 1, "hora": 8 + i} for i in range(5)])
    datos = await obtener(cliente, admin, f"{BASE}/{ana['id']}/eventos/mapa")
    assert len(datos["puntos"]) == 3 and datos["truncado"] is True
    fechas = [p["fecha"] for p in datos["puntos"]]
    assert fechas == sorted(fechas, reverse=True)
