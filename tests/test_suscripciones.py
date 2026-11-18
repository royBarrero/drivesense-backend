"""HU-28: plan de suscripción de la empresa y sus límites (con y sin base de datos)."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.suscripciones.planes import (
    PLANES,
    PlanSuscripcion,
    dias_restantes,
    inicio_historial,
    limitar_desde,
    plan_efectivo,
    sumar_meses,
)
from tests.conftest import activar_plan, cabecera
from app.telemetria.models import TipoEvento
from tests.test_flotas import registrar_conductor
from tests.test_flotas_perfil import crear_viaje

PLAN = "/api/v1/suscripciones/plan"
PLANES_URL = "/api/v1/suscripciones/planes"
CONDUCTORES = "/api/v1/flotas/conductores"


# --- Pruebas sin base de datos ----------------------------------------------


def test_catalogo_de_planes():
    gratis, premium, empresas = (PLANES[p] for p in PlanSuscripcion)
    assert (gratis.limite_conductores, gratis.historial_dias, gratis.precio_bs) == (3, 30, 0)
    assert not (gratis.exportar or gratis.eventos_mapa_patrones or gratis.tablero_completo)
    assert (premium.limite_conductores, premium.historial_meses, premium.precio_bs) == (15, 12, 200)
    assert premium.exportar and premium.eventos_mapa_patrones and premium.tablero_completo
    assert (empresas.limite_conductores, empresas.historial_meses, empresas.precio_bs) == (None, None, 500)
    assert empresas.historial_dias is None and empresas.exportar


def test_sumar_meses_ajusta_al_ultimo_dia():
    assert sumar_meses(datetime(2026, 1, 31, 10, tzinfo=UTC), 1) == datetime(2026, 2, 28, 10, tzinfo=UTC)
    assert sumar_meses(datetime(2026, 11, 15, tzinfo=UTC), 1) == datetime(2026, 12, 15, tzinfo=UTC)
    assert sumar_meses(datetime(2026, 12, 15, tzinfo=UTC), 1) == datetime(2027, 1, 15, tzinfo=UTC)
    assert sumar_meses(datetime(2026, 11, 15, tzinfo=UTC), -12) == datetime(2025, 11, 15, tzinfo=UTC)


def test_plan_vencido_vale_como_gratis_segun_el_dia_local_de_la_empresa():
    zona = "America/La_Paz"
    # 02:00 UTC del 16 = 22:00 del 15 en La Paz (UTC-4)
    ahora = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
    # Vence el 15 de noviembre a las 23:00 hora local: el plan rige todo ese día
    hoy_tarde = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
    assert plan_efectivo(PlanSuscripcion.premium, hoy_tarde, ahora, zona) == PlanSuscripcion.premium
    # Venció el 14: hoy (15 local) ya es Gratis
    ayer = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert plan_efectivo(PlanSuscripcion.premium, ayer, ahora, zona) == PlanSuscripcion.gratis
    # Al cruzar la medianoche local el plan que vencía el 15 deja de regir
    manana_local = datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
    assert plan_efectivo(PlanSuscripcion.premium, hoy_tarde, manana_local, zona) == PlanSuscripcion.gratis
    # En UTC la misma fecha ya es otro día
    assert plan_efectivo(PlanSuscripcion.premium, hoy_tarde, ahora, "UTC") == PlanSuscripcion.premium
    assert plan_efectivo(PlanSuscripcion.empresas, None, ahora, zona) == PlanSuscripcion.gratis
    assert plan_efectivo(PlanSuscripcion.gratis, None, ahora, zona) == PlanSuscripcion.gratis


def test_dias_restantes_cuenta_dias_de_calendario_en_la_zona_de_la_empresa():
    zona = "America/La_Paz"
    ahora = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)  # 15 de noviembre, 22:00 local
    assert dias_restantes(datetime(2026, 11, 22, 12, tzinfo=UTC), ahora, zona) == 7
    assert dias_restantes(datetime(2026, 11, 16, 3, tzinfo=UTC), ahora, zona) == 0
    assert dias_restantes(datetime(2026, 11, 16, 12, tzinfo=UTC), ahora, zona) == 1
    assert dias_restantes(datetime(2026, 11, 1, tzinfo=UTC), ahora, zona) == 0


def test_ventana_de_historial_por_plan():
    ahora = datetime(2026, 11, 15, 12, tzinfo=UTC)
    # 30 días = hoy y los 29 anteriores, desde la medianoche de La Paz (04:00 UTC)
    assert inicio_historial(PLANES[PlanSuscripcion.gratis], ahora, "America/La_Paz") == datetime(
        2026, 10, 17, 4, tzinfo=UTC
    )
    assert inicio_historial(PLANES[PlanSuscripcion.premium], ahora, "America/La_Paz") == datetime(
        2025, 11, 15, 4, tzinfo=UTC
    )
    assert inicio_historial(PLANES[PlanSuscripcion.empresas], ahora, "America/La_Paz") is None


def test_limitar_desde_toma_el_mas_reciente():
    a, b = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    assert limitar_desde(None, None) is None
    assert limitar_desde(a, None) == a
    assert limitar_desde(None, b) == b
    assert limitar_desde(a, b) == b and limitar_desde(b, a) == b


# --- Plan de la empresa ------------------------------------------------------


async def test_empresa_nueva_empieza_en_gratis(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    respuesta = await cliente.get(PLAN, headers=cabecera(admin))
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["detalle"]["plan"] == "gratis"
    assert cuerpo["detalle"]["limite_conductores"] == 3
    assert cuerpo["estado"] == "activo"
    assert cuerpo["fecha_vencimiento"] is None and cuerpo["dias_restantes"] is None
    assert cuerpo["conductores_usados"] == 0
    assert cuerpo["aviso_vencimiento"] is False


async def test_conductores_usados_cuenta_solo_activos(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    ana = await registrar_conductor(cliente, admin, 1)
    await registrar_conductor(cliente, admin, 2)
    await db.execute(text("UPDATE usuarios SET activo = false WHERE id = :id"), {"id": ana["id"]})
    await db.commit()
    assert (await cliente.get(PLAN, headers=cabecera(admin))).json()["conductores_usados"] == 1


async def test_catalogo_de_planes_para_comparar(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    respuesta = await cliente.get(PLANES_URL, headers=cabecera(admin))
    assert respuesta.status_code == 200
    assert [p["plan"] for p in respuesta.json()] == ["gratis", "premium", "empresas"]
    assert [p["precio_bs"] for p in respuesta.json()] == [0, 200, 500]


async def test_solo_el_admin_accede_al_plan(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    for metodo, ruta, cuerpo in (
        ("get", PLAN, None),
        ("get", PLANES_URL, None),
        ("post", PLAN, {"plan": "premium"}),
    ):
        assert (await cliente.request(metodo, ruta, json=cuerpo)).status_code == 401

    creado = await registrar_conductor(cliente, admin, 1)
    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": creado["email"], "contrasenia": creado["contrasenia_temporal"]},
    )
    conductor = login.json()
    individual = (
        await cliente.post(
            "/api/v1/auth/registro",
            json={
                "nombre": "Solo",
                "email": "solo@empresa.example.com",
                "telefono": "70000002",
                "contrasenia": "Clave1234",
            },
        )
    ).json()
    for sesion in (conductor, individual):
        assert (await cliente.get(PLAN, headers=cabecera(sesion))).status_code == 403
        assert (await cliente.post(PLAN, headers=cabecera(sesion), json={"plan": "premium"})).status_code == 403
    # y no cambió nada
    assert (await cliente.get(PLAN, headers=cabecera(admin))).json()["detalle"]["plan"] == "gratis"


async def test_cada_empresa_tiene_su_propio_plan(cliente, registrar_empresa):
    a = await registrar_empresa(1, plan="gratis")
    b = await registrar_empresa(2, plan="gratis")
    await cliente.post(PLAN, headers=cabecera(a), json={"plan": "premium"})
    assert (await cliente.get(PLAN, headers=cabecera(a))).json()["detalle"]["plan"] == "premium"
    assert (await cliente.get(PLAN, headers=cabecera(b))).json()["detalle"]["plan"] == "gratis"


async def test_cambiar_a_un_plan_de_pago_dura_un_mes(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    respuesta = await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "premium"})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["detalle"]["plan"] == "premium"
    assert cuerpo["detalle"]["precio_bs"] == 200 and cuerpo["detalle"]["duracion_meses"] == 1
    inicio = datetime.fromisoformat(cuerpo["fecha_inicio"])
    assert datetime.fromisoformat(cuerpo["fecha_vencimiento"]) == sumar_meses(inicio, 1)
    assert cuerpo["dias_restantes"] >= 28
    assert cuerpo["aviso_vencimiento"] is False and cuerpo["estado"] == "activo"


async def test_plan_invalido_responde_422(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    assert (await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "oro"})).status_code == 422


async def test_elegir_el_plan_vigente_lo_renueva_un_mes_mas(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    primero = (await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "premium"})).json()
    renovado = (await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "premium"})).json()
    assert renovado["fecha_inicio"] == primero["fecha_inicio"]
    assert datetime.fromisoformat(renovado["fecha_vencimiento"]) == sumar_meses(
        datetime.fromisoformat(primero["fecha_vencimiento"]), 1
    )


async def test_pasar_a_otro_plan_de_pago_empieza_un_mes_nuevo(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "premium"})
    nuevo = (await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "empresas"})).json()
    assert nuevo["detalle"]["plan"] == "empresas"
    assert datetime.fromisoformat(nuevo["fecha_vencimiento"]) == sumar_meses(
        datetime.fromisoformat(nuevo["fecha_inicio"]), 1
    )


async def test_bajar_a_gratis_es_inmediato_y_repetirlo_responde_409(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "premium"})
    bajado = (await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "gratis"})).json()
    assert bajado["detalle"]["plan"] == "gratis" and bajado["fecha_vencimiento"] is None
    repetido = await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "gratis"})
    assert repetido.status_code == 409


async def test_aviso_cuando_faltan_siete_dias_o_menos(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    empresa_id = admin["usuario"]["empresa"]["id"]
    await activar_plan(db, empresa_id, "premium", dias=8)
    assert (await cliente.get(PLAN, headers=cabecera(admin))).json()["aviso_vencimiento"] is False
    await activar_plan(db, empresa_id, "premium", dias=5)
    cuerpo = (await cliente.get(PLAN, headers=cabecera(admin))).json()
    assert cuerpo["aviso_vencimiento"] is True and cuerpo["estado"] == "por_vencer"
    assert cuerpo["dias_restantes"] == 5


async def test_al_vencer_la_empresa_pasa_a_gratis(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    empresa_id = admin["usuario"]["empresa"]["id"]
    await activar_plan(db, empresa_id, "empresas", dias=-1)
    cuerpo = (await cliente.get(PLAN, headers=cabecera(admin))).json()
    assert cuerpo["detalle"]["plan"] == "gratis" and cuerpo["fecha_vencimiento"] is None
    fila = (await db.execute(text("SELECT plan, plan_vencimiento FROM empresas WHERE id = :i"), {"i": empresa_id})).one()
    assert fila.plan == "gratis" and fila.plan_vencimiento is None


async def test_plan_vencido_limita_aunque_nadie_consulte_el_plan(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    await activar_plan(db, admin["usuario"]["empresa"]["id"], "empresas", dias=-1)
    # Sin pasar por /suscripciones/plan: el reporte ya está bloqueado
    respuesta = await cliente.get("/api/v1/flotas/reporte", headers=cabecera(admin))
    assert respuesta.status_code == 403


# --- Límite de conductores ---------------------------------------------------


@pytest.mark.parametrize("plan, limite", [("gratis", 3), ("premium", 15)])
async def test_no_se_registran_mas_conductores_que_el_limite(cliente, db, registrar_empresa, plan, limite):
    admin = await registrar_empresa(1, plan=plan)
    for numero in range(1, limite + 1):
        await registrar_conductor(cliente, admin, numero)
    respuesta = await cliente.post(
        CONDUCTORES,
        headers=cabecera(admin),
        json={"nombre": "Uno más", "email": "extra@empresa.example.com", "telefono": "71000000"},
    )
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "plan_limite"
    usados = (await cliente.get(PLAN, headers=cabecera(admin))).json()["conductores_usados"]
    assert usados == limite


async def test_empresas_no_tiene_tope_de_conductores(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="empresas")
    for numero in range(1, 19):
        await registrar_conductor(cliente, admin, numero)
    assert (await cliente.get(PLAN, headers=cabecera(admin))).json()["conductores_usados"] == 18


async def test_mejorar_el_plan_permite_registrar_mas(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    for numero in range(1, 4):
        await registrar_conductor(cliente, admin, numero)
    cuerpo = {"nombre": "Cuarto", "email": "cuarto@empresa.example.com", "telefono": "71000000"}
    assert (await cliente.post(CONDUCTORES, headers=cabecera(admin), json=cuerpo)).status_code == 403
    await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "premium"})
    assert (await cliente.post(CONDUCTORES, headers=cabecera(admin), json=cuerpo)).status_code == 201


async def test_un_conductor_dado_de_baja_libera_un_lugar(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    creados = [await registrar_conductor(cliente, admin, n) for n in range(1, 4)]
    await db.execute(text("UPDATE usuarios SET activo = false WHERE id = :id"), {"id": creados[0]["id"]})
    await db.commit()
    cuerpo = {"nombre": "Nuevo", "email": "nuevo@empresa.example.com", "telefono": "71000000"}
    assert (await cliente.post(CONDUCTORES, headers=cabecera(admin), json=cuerpo)).status_code == 201


async def test_el_limite_de_una_empresa_no_afecta_a_otra(cliente, registrar_empresa):
    a = await registrar_empresa(1, plan="gratis")
    b = await registrar_empresa(2, plan="gratis")
    for numero in range(1, 4):
        await registrar_conductor(cliente, a, numero)
    # B tiene su propio cupo
    assert (await registrar_conductor(cliente, b, 10))["id"]


# --- Bajar de plan nunca borra datos ----------------------------------------


async def test_bajar_de_plan_conserva_conductores_y_viajes_pero_no_deja_agregar(
    cliente, db, registrar_empresa
):
    admin = await registrar_empresa(1, plan="premium")
    creados = [await registrar_conductor(cliente, admin, n) for n in range(1, 6)]
    for c in creados:
        await crear_viaje(db, c["id"], 80, dias_atras=2)
    await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "gratis"})

    # Los 5 conductores siguen (aunque el límite sea 3) y sus viajes también
    listado = (await cliente.get(CONDUCTORES, headers=cabecera(admin))).json()
    assert listado["total"] == 5 and all(c["viajes"] == 1 for c in listado["conductores"])
    plan = (await cliente.get(PLAN, headers=cabecera(admin))).json()
    assert plan["conductores_usados"] == 5 and plan["detalle"]["limite_conductores"] == 3
    cuerpo = {"nombre": "Otro", "email": "otro@empresa.example.com", "telefono": "71000000"}
    assert (await cliente.post(CONDUCTORES, headers=cabecera(admin), json=cuerpo)).status_code == 403
    total_viajes = await db.scalar(text("SELECT count(*) FROM recorridos"))
    assert total_viajes == 5


# --- Historial por plan ------------------------------------------------------


async def test_gratis_ve_solo_los_ultimos_30_dias_tambien_en_todo(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    ana = await registrar_conductor(cliente, admin, 1)
    await crear_viaje(db, ana["id"], 90, dias_atras=2)
    viejo = await crear_viaje(db, ana["id"], 50, dias_atras=45)

    fila = (await cliente.get(CONDUCTORES, headers=cabecera(admin), params={"periodo": "todo"})).json()
    assert fila["conductores"][0]["viajes"] == 1
    perfil = (await cliente.get(f"{CONDUCTORES}/{ana['id']}", headers=cabecera(admin))).json()
    assert perfil["viajes"] == 1 and perfil["drivescore"] == 90
    viajes = (await cliente.get(f"{CONDUCTORES}/{ana['id']}/recorridos", headers=cabecera(admin))).json()
    assert len(viajes) == 1
    detalle = await cliente.get(f"{CONDUCTORES}/{ana['id']}/recorridos/{viejo.id}", headers=cabecera(admin))
    assert detalle.status_code == 404
    # el dato sigue guardado
    assert await db.scalar(text("SELECT count(*) FROM recorridos WHERE id = :i"), {"i": viejo.id}) == 1


async def test_premium_ve_12_meses_y_empresas_todo(cliente, db, registrar_empresa):
    premium = await registrar_empresa(1, plan="premium")
    completo = await registrar_empresa(2, plan="empresas")
    resultados = {}
    for numero, admin in ((1, premium), (2, completo)):
        c = await registrar_conductor(cliente, admin, numero)
        await crear_viaje(db, c["id"], 90, dias_atras=100)
        await crear_viaje(db, c["id"], 80, dias_atras=500)
        fila = (await cliente.get(CONDUCTORES, headers=cabecera(admin), params={"periodo": "todo"})).json()
        resultados[numero] = fila["conductores"][0]["viajes"]
    assert resultados == {1: 1, 2: 2}


async def test_el_tablero_y_el_reporte_respetan_la_ventana(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="premium")
    ana = await registrar_conductor(cliente, admin, 1)
    await crear_viaje(db, ana["id"], 90, dias_atras=10)
    await crear_viaje(db, ana["id"], 50, dias_atras=400)
    tablero = (await cliente.get("/api/v1/flotas/tablero", headers=cabecera(admin), params={"periodo": "todo"})).json()
    assert tablero["viajes"] == 1
    reporte = await cliente.get(
        f"{CONDUCTORES}/{ana['id']}/reporte", headers=cabecera(admin), params={"formato": "csv", "periodo": "todo"}
    )
    assert reporte.status_code == 200


# --- Funciones por plan ------------------------------------------------------

RUTAS_EVENTOS_BLOQUEADAS = ("eventos/mapa", "eventos/resumen")


async def test_gratis_bloquea_exportar_mapa_y_patrones_pero_deja_la_lista(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    ana = await registrar_conductor(cliente, admin, 1)
    await crear_viaje(db, ana["id"], 80, dias_atras=1, eventos=[TipoEvento.frenada_brusca] * 3)
    h = cabecera(admin)

    for ruta in (
        "/api/v1/flotas/reporte",
        f"{CONDUCTORES}/{ana['id']}/reporte",
        *(f"{CONDUCTORES}/{ana['id']}/{r}" for r in RUTAS_EVENTOS_BLOQUEADAS),
    ):
        respuesta = await cliente.get(ruta, headers=h)
        assert respuesta.status_code == 403, ruta
        assert respuesta.json()["codigo"] == "plan_funcion", ruta
    lista = await cliente.get(f"{CONDUCTORES}/{ana['id']}/eventos", headers=h)
    assert lista.status_code == 200 and lista.json()["total"] == 3


async def test_gratis_en_el_tablero_solo_tiene_las_tarjetas(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    ana = await registrar_conductor(cliente, admin, 1)
    await crear_viaje(db, ana["id"], 40, distancia_m=30_000, dias_atras=1, eventos=[TipoEvento.frenada_brusca] * 2)
    tablero = (await cliente.get("/api/v1/flotas/tablero", headers=cabecera(admin))).json()
    assert tablero["completo"] is False
    assert tablero["viajes"] == 1 and tablero["drivescore"] == 40 and tablero["eventos"] == 2
    assert tablero["conductores_en_riesgo"] == 1
    assert tablero["mejores"] == [] and tablero["a_vigilar"] == []
    assert tablero["evolucion"]["puntos"] == []
    assert all(v == 0 for v in tablero["por_calificacion"].values())
    assert all(v == 0 for v in tablero["eventos_por_tipo"].values())


@pytest.mark.parametrize("plan", ["premium", "empresas"])
async def test_los_planes_de_pago_tienen_todas_las_funciones(cliente, db, registrar_empresa, plan):
    admin = await registrar_empresa(1, plan=plan)
    ana = await registrar_conductor(cliente, admin, 1)
    await crear_viaje(db, ana["id"], 40, distancia_m=30_000, dias_atras=1, eventos=[TipoEvento.frenada_brusca] * 2)
    h = cabecera(admin)
    assert (await cliente.get("/api/v1/flotas/reporte", headers=h)).status_code == 200
    assert (await cliente.get(f"{CONDUCTORES}/{ana['id']}/reporte", headers=h)).status_code == 200
    for r in RUTAS_EVENTOS_BLOQUEADAS:
        assert (await cliente.get(f"{CONDUCTORES}/{ana['id']}/{r}", headers=h)).status_code == 200
    tablero = (await cliente.get("/api/v1/flotas/tablero", headers=h)).json()
    assert tablero["completo"] is True
    assert len(tablero["mejores"] + tablero["a_vigilar"]) > 0
    assert sum(tablero["eventos_por_tipo"].values()) == 2


# --- Los conductores no dependen del plan -----------------------------------


async def test_el_conductor_usa_la_app_completa_con_la_empresa_en_gratis(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="premium")
    creados = [await registrar_conductor(cliente, admin, n) for n in range(1, 6)]
    await cliente.post(PLAN, headers=cabecera(admin), json={"plan": "gratis"})
    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": creados[4]["email"], "contrasenia": creados[4]["contrasenia_temporal"]},
    )
    assert login.status_code == 200
    h = cabecera(login.json())
    iniciado = await cliente.post("/api/v1/recorridos", headers=h, json={"lat_inicio": -17.78, "lon_inicio": -63.18})
    assert iniciado.status_code == 201, iniciado.text
    assert (await cliente.get("/api/v1/recorridos", headers=h)).status_code == 200
    assert (await cliente.get("/api/v1/gamificacion/progreso", headers=h)).status_code == 200
    # y un viaje antiguo sigue en su historial aunque la empresa vea solo 30 días
    await crear_viaje(db, creados[4]["id"], 70, dias_atras=90)
    historial = (await cliente.get("/api/v1/recorridos", headers=h)).json()
    assert len(historial["recorridos"]) == 1


async def test_los_403_por_permisos_no_llevan_codigo_de_plan(cliente, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    creado = await registrar_conductor(cliente, admin, 1)
    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": creado["email"], "contrasenia": creado["contrasenia_temporal"]},
    )
    respuesta = await cliente.get(PLAN, headers=cabecera(login.json()))
    assert respuesta.status_code == 403 and "codigo" not in respuesta.json()


async def test_el_vencimiento_se_evalua_por_el_dia_local_de_la_empresa(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    empresa_id = admin["usuario"]["empresa"]["id"]
    # Vence hoy (hora local): sigue rigiendo hasta el final del día
    ahora_local = datetime.now(ZoneInfo("America/La_Paz"))
    fin_de_hoy = datetime.combine(ahora_local.date(), time(23, 59), tzinfo=ahora_local.tzinfo)
    await db.execute(
        text(
            "UPDATE empresas SET plan = 'premium', plan_inicio = now(), "
            "plan_vencimiento = :v WHERE id = :id"
        ),
        {"v": fin_de_hoy, "id": empresa_id},
    )
    await db.commit()
    cuerpo = (await cliente.get(PLAN, headers=cabecera(admin))).json()
    assert cuerpo["detalle"]["plan"] == "premium" and cuerpo["dias_restantes"] == 0
    assert cuerpo["aviso_vencimiento"] is True


async def test_gratis_no_ve_mas_alla_de_30_dias_en_ningun_periodo(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1, plan="gratis")
    ana = await registrar_conductor(cliente, admin, 1)
    await crear_viaje(db, ana["id"], 90, dias_atras=3)
    await crear_viaje(db, ana["id"], 10, dias_atras=40)
    resumen = (await cliente.get("/api/v1/flotas/resumen", headers=cabecera(admin), params={"periodo": "todo"})).json()
    # El promedio de la empresa solo cuenta el viaje de 90
    assert resumen["drivescore_promedio"] == 90
    tablero = (await cliente.get("/api/v1/flotas/tablero", headers=cabecera(admin), params={"periodo": "todo"})).json()
    assert tablero["viajes"] == 1 and tablero["drivescore"] == 90
