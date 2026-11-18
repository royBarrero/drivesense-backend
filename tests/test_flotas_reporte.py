"""HU-27: exportar el reporte de desempeño de un conductor y de la flota (PDF y CSV)."""

import csv
import io
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.exceptions import RequestValidationError

from app.flotas.reporte import (
    DatosEmpresa,
    PeriodoReporte,
    ReporteConductor,
    ViajeReporte,
    nombre_archivo,
    resolver_rango,
)
from app.flotas.reporte_pdf import pdf_conductor
from app.flotas.schemas import NivelRiesgo
from app.puntaje.service import Calificacion
from app.recorridos.models import EstadoRecorrido
from app.telemetria.models import TipoEvento
from tests.conftest import cabecera
from tests.test_flotas import registrar_conductor
from tests.test_flotas_perfil import crear_viaje

BASE = "/api/v1/flotas"
REPORTE_FLOTA = f"{BASE}/reporte"
LA_PAZ = ZoneInfo("America/La_Paz")


def reporte_de(conductor_id: int) -> str:
    return f"{BASE}/conductores/{conductor_id}/reporte"


# --- Pruebas sin base de datos ----------------------------------------------

AHORA = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)  # 14/11 22:00 en La Paz


def test_rango_de_siete_dias_usa_la_zona_de_la_empresa():
    rango = resolver_rango(PeriodoReporte.siete_dias, None, None, AHORA, "America/La_Paz")
    assert rango.desde == datetime(2026, 11, 8, 4, 0, tzinfo=UTC)
    assert rango.hasta is None
    assert (rango.dia_inicio, rango.dia_fin) == (date(2026, 11, 8), date(2026, 11, 14))


def test_rango_de_fechas_es_inclusivo_en_la_zona_de_la_empresa():
    rango = resolver_rango(
        PeriodoReporte.rango, date(2026, 11, 10), date(2026, 11, 14), AHORA, "America/La_Paz"
    )
    assert rango.desde == datetime(2026, 11, 10, 4, 0, tzinfo=UTC)
    # Hasta el final del 14 (medianoche local del 15)
    assert rango.hasta == datetime(2026, 11, 15, 4, 0, tzinfo=UTC)


def test_todo_no_tiene_limites():
    rango = resolver_rango(PeriodoReporte.todo, None, None, AHORA, "America/La_Paz")
    assert (rango.desde, rango.hasta, rango.dia_inicio) == (None, None, None)


@pytest.mark.parametrize(
    ("periodo", "desde", "hasta", "campo"),
    [
        (PeriodoReporte.rango, None, date(2026, 11, 14), "desde"),
        (PeriodoReporte.rango, date(2026, 11, 1), None, "hasta"),
        (PeriodoReporte.rango, date(2026, 11, 14), date(2026, 11, 1), "hasta"),
        (PeriodoReporte.rango, date(2026, 11, 1), date(2026, 11, 15), "hasta"),  # mañana en La Paz
        (PeriodoReporte.siete_dias, date(2026, 11, 1), None, "desde"),
        (PeriodoReporte.todo, None, date(2026, 11, 1), "hasta"),
    ],
)
def test_rango_invalido(periodo, desde, hasta, campo):
    with pytest.raises(RequestValidationError) as error:
        resolver_rango(periodo, desde, hasta, AHORA, "America/La_Paz")
    assert error.value.errors()[0]["loc"][-1] == campo


def test_nombre_de_archivo_con_conductor_y_periodo():
    rango = resolver_rango(PeriodoReporte.treinta_dias, None, None, AHORA, "America/La_Paz")
    assert (
        nombre_archivo("conductor", "José Ñandú / Pérez", rango, date(2026, 11, 14))
        == "reporte-conductor-jose-nandu-perez-ultimos-30-dias"
    )
    rango = resolver_rango(
        PeriodoReporte.rango, date(2026, 11, 1), date(2026, 11, 10), AHORA, "America/La_Paz"
    )
    assert (
        nombre_archivo("empresa", "Flota Sur", rango, date(2026, 11, 14))
        == "reporte-empresa-flota-sur-2026-11-01_a_2026-11-10"
    )


def test_pdf_largo_con_caracteres_raros_se_genera_en_varias_paginas():
    generado = datetime(2026, 11, 14, 22, 0, tzinfo=LA_PAZ)
    reporte = ReporteConductor(
        empresa=DatosEmpresa("Flota Ñ 🚚", "Flota", "a@empresa.example.com", "71000000"),
        generado=generado,
        periodo="Todo el tiempo",
        archivo="x",
        nombre="Ana 🚗 Pérez",
        email="ana@empresa.example.com",
        telefono="71000000",
        viajes=[
            ViajeReporte(generado - timedelta(days=i), 12_345, 3700, 80, Calificacion.muy_bueno)
            for i in range(120)
        ],
        distancia_m=1_000_000,
        drivescore=80,
        calificacion=Calificacion.muy_bueno,
        nivel_riesgo=NivelRiesgo.bajo,
        categorias={"Velocidad": 80, "Frenadas": 65, "Aceleraciones": 98, "Giros": 40},
        eventos_por_tipo=dict.fromkeys(TipoEvento, 3),
        eventos=12,
        eventos_por_10km=0.1,
    )
    contenido = pdf_conductor(reporte)
    assert contenido.startswith(b"%PDF")
    assert contenido.count(b"/Type /Page\n") > 2


# --- Utilidades de las pruebas con BD ---------------------------------------


def leer_csv(texto: str) -> list[list[str]]:
    assert texto.startswith("﻿")
    return list(csv.reader(io.StringIO(texto.removeprefix("﻿"))))


def fila_bajo(filas: list[list[str]], encabezado: str) -> list[str]:
    """La fila de datos que sigue al encabezado de columnas que empieza por `encabezado`."""
    indice = next(i for i, f in enumerate(filas) if f and f[0] == encabezado)
    return filas[indice + 1]


def valor(filas: list[list[str]], etiqueta: str) -> str:
    return next(f[1] for f in filas if f and f[0] == etiqueta)


async def escenario(cliente, db, registrar_empresa):
    """Ana, con tres viajes finalizados (10 km con 90, 30 km con 70, 10 km con 60 hace 40
    días), un viaje descartado y uno en curso."""
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(
        db, ana["id"], 90, dias_atras=2, categorias={"frenadas": 80},
        eventos=[TipoEvento.frenada_brusca, TipoEvento.frenada_brusca],
    )
    await crear_viaje(
        db, ana["id"], 70, dias_atras=5, distancia_m=30_000,
        categorias={"frenadas": 60}, eventos=[TipoEvento.giro_agresivo],
    )
    await crear_viaje(db, ana["id"], 60, dias_atras=40)
    await crear_viaje(
        db, ana["id"], 10, dias_atras=1, estado=EstadoRecorrido.descartado,
        eventos=[TipoEvento.frenada_brusca],
    )
    return admin, ana


async def pedir(cliente, sesion, ruta, **parametros):
    return await cliente.get(ruta, headers=cabecera(sesion), params=parametros)


# --- Acceso ------------------------------------------------------------------


async def test_sin_token_responde_401(cliente):
    assert (await cliente.get(reporte_de(1))).status_code == 401
    assert (await cliente.get(REPORTE_FLOTA)).status_code == 401


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
        for ruta in (reporte_de(creado["id"]), REPORTE_FLOTA):
            for formato in ("pdf", "csv"):
                respuesta = await pedir(cliente, sesion, ruta, formato=formato)
                assert respuesta.status_code == 403


async def test_una_empresa_no_exporta_conductores_de_otra(cliente, db, registrar_empresa):
    admin_a, ana = await escenario(cliente, db, registrar_empresa)
    admin_b = await registrar_empresa(2)
    carla = await registrar_conductor(cliente, admin_b, 2, "Carla")
    await crear_viaje(db, carla["id"], 85, dias_atras=1)

    # B no puede exportar a Ana: 404 igual que un inexistente, sin revelar nada
    for formato in ("pdf", "csv"):
        ajeno = await pedir(cliente, admin_b, reporte_de(ana["id"]), formato=formato)
        assert ajeno.status_code == 404
        assert "Ana" not in ajeno.text
    assert (await pedir(cliente, admin_b, reporte_de(999_999))).status_code == 404

    # El reporte de la flota de B solo trae sus conductores
    respuesta = await pedir(cliente, admin_b, REPORTE_FLOTA, formato="csv")
    assert respuesta.status_code == 200
    assert "Carla" in respuesta.text
    assert "Ana" not in respuesta.text
    assert "Ana" in (await pedir(cliente, admin_a, REPORTE_FLOTA, formato="csv")).text


async def test_conductor_inactivo_o_admin_responde_404(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    assert (await pedir(cliente, admin, reporte_de(admin["usuario"]["id"]))).status_code == 404


# --- Reporte del conductor ---------------------------------------------------


async def test_csv_del_conductor_con_los_datos_del_periodo(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)

    respuesta = await pedir(cliente, admin, reporte_de(ana["id"]), periodo="30d", formato="csv")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert 'filename="reporte-conductor-ana-ultimos-30-dias.csv"' in (
        respuesta.headers["content-disposition"]
    )
    filas = leer_csv(respuesta.text)

    assert valor(filas, "Conductor") == "Ana"
    assert valor(filas, "Correo del conductor") == ana["email"]
    assert "Últimos 30 días" in valor(filas, "Periodo")
    # 10 km con 90 y 30 km con 70 → 75 ponderado (no el 80 simple). Solo finalizados.
    assert fila_bajo(filas, "DriveScore promedio") == ["75", "Muy bueno", "Bajo", "2", "40.0"]
    assert [valor(filas, c) for c in ("Frenadas", "Giros")] == ["65", "75"]
    # 2 frenadas + 1 giro (el viaje descartado no cuenta) → 3 eventos en 40 km
    assert valor(filas, "Frenada brusca") == "2"
    assert valor(filas, "Giro agresivo") == "1"
    assert valor(filas, "Total") == "3"
    assert valor(filas, "Eventos por cada 10 km") == "0.8"
    # Dos viajes, del más reciente al más antiguo
    indice = next(i for i, f in enumerate(filas) if f and f[0] == "Fecha")
    viajes = filas[indice + 1 :]
    assert [(v[2], v[4], v[5]) for v in viajes] == [("10.0", "90", "Excelente"), ("30.0", "70", "Regular")]
    assert float(viajes[0][3]) == 20.0


async def test_csv_de_todo_el_tiempo_incluye_viajes_antiguos(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    filas = leer_csv((await pedir(cliente, admin, reporte_de(ana["id"]), periodo="todo", formato="csv")).text)
    # (900 + 2100 + 600) / 50 = 72 → regular, riesgo medio
    assert fila_bajo(filas, "DriveScore promedio") == ["72", "Regular", "Medio", "3", "50.0"]


async def test_reporte_no_incluye_ubicaciones_ni_rutas(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    for ruta in (reporte_de(ana["id"]), REPORTE_FLOTA):
        texto = (await pedir(cliente, admin, ruta, formato="csv")).text
        assert "-17.7" not in texto and "-63.1" not in texto
        assert "lat" not in texto.lower() and "ruta" not in texto.lower()


async def test_rango_de_fechas_incluye_los_dos_extremos(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    hoy = datetime.now(LA_PAZ).date()

    # Hace 3 días hasta hoy: solo el viaje de hace 2 días (el de hace 5 queda fuera)
    respuesta = await pedir(
        cliente, admin, reporte_de(ana["id"]), periodo="rango", formato="csv",
        desde=(hoy - timedelta(days=3)).isoformat(), hasta=hoy.isoformat(),
    )
    assert respuesta.status_code == 200
    filas = leer_csv(respuesta.text)
    assert fila_bajo(filas, "DriveScore promedio") == ["90", "Excelente", "Bajo", "1", "10.0"]
    assert f"{(hoy - timedelta(days=3)).isoformat()}_a_{hoy.isoformat()}" in (
        respuesta.headers["content-disposition"]
    )

    # Un rango sin viajes avisa en vez de generar un archivo vacío
    vacio = await pedir(
        cliente, admin, reporte_de(ana["id"]), periodo="rango",
        desde=(hoy - timedelta(days=20)).isoformat(), hasta=(hoy - timedelta(days=10)).isoformat(),
    )
    assert vacio.status_code == 409


@pytest.mark.parametrize(
    ("parametros", "campo"),
    [
        ({"periodo": "rango"}, "desde"),
        ({"periodo": "rango", "desde": "2026-01-10"}, "hasta"),
        ({"periodo": "rango", "desde": "2026-01-10", "hasta": "2026-01-01"}, "hasta"),
        ({"periodo": "rango", "desde": "2026-01-10", "hasta": "2999-01-01"}, "hasta"),
        ({"periodo": "7d", "desde": "2026-01-10"}, "desde"),
        ({"periodo": "rango", "desde": "no-es-fecha", "hasta": "2026-01-10"}, "desde"),
    ],
)
async def test_parametros_invalidos_responden_422(cliente, db, registrar_empresa, parametros, campo):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    for ruta in (reporte_de(ana["id"]), REPORTE_FLOTA):
        respuesta = await pedir(cliente, admin, ruta, **parametros)
        assert respuesta.status_code == 422
        assert respuesta.json()["errores"][0]["campo"] == campo


async def test_sin_viajes_en_el_periodo_responde_409(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    sin_viajes = await registrar_conductor(cliente, admin, 1, "Elena")
    for ruta in (reporte_de(sin_viajes["id"]), REPORTE_FLOTA):
        for formato in ("pdf", "csv"):
            respuesta = await pedir(cliente, admin, ruta, formato=formato)
            assert respuesta.status_code == 409
            assert respuesta.json()["detail"] == "No hay viajes en el periodo seleccionado"

    # Solo con viajes descartados o antiguos tampoco hay nada que exportar en 7 días
    await crear_viaje(db, sin_viajes["id"], 50, dias_atras=1, estado=EstadoRecorrido.descartado)
    await crear_viaje(db, sin_viajes["id"], 50, dias_atras=30)
    assert (await pedir(cliente, admin, reporte_de(sin_viajes["id"]), periodo="7d")).status_code == 409


async def test_pdf_del_conductor(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    respuesta = await pedir(cliente, admin, reporte_de(ana["id"]), periodo="30d", formato="pdf")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "application/pdf"
    assert 'filename="reporte-conductor-ana-ultimos-30-dias.pdf"' in (
        respuesta.headers["content-disposition"]
    )
    assert respuesta.content.startswith(b"%PDF")


# --- Reporte de la flota -----------------------------------------------------


async def test_csv_de_la_flota_con_una_fila_por_conductor(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    bruno = await registrar_conductor(cliente, admin, 2, "Bruno")
    await registrar_conductor(cliente, admin, 3, "Elena")  # sin viajes
    await crear_viaje(db, bruno["id"], 40, dias_atras=3, distancia_m=20_000, eventos=[TipoEvento.exceso_velocidad])

    respuesta = await pedir(cliente, admin, REPORTE_FLOTA, periodo="30d", formato="csv")
    assert respuesta.status_code == 200
    assert 'filename="reporte-empresa-' in respuesta.headers["content-disposition"]
    assert respuesta.headers["content-disposition"].endswith('-ultimos-30-dias.csv"')
    filas = leer_csv(respuesta.text)

    assert "Empresa 1" in valor(filas, "Empresa") or valor(filas, "Empresa")
    # Flota: (900 + 2100 + 800) / 60 km = 63,3 → 63 regular, riesgo medio
    assert fila_bajo(filas, "Conductores") [:7] == ["3", "2", "63", "Regular", "Medio", "3", "60.0"]
    indice = next(i for i, f in enumerate(filas) if f and f[0] == "Conductor")
    por_conductor = {f[0]: f for f in filas[indice + 1 :]}
    assert list(por_conductor) == ["Ana", "Bruno", "Elena"]  # mejor DriveScore primero, sin viajes al final
    assert por_conductor["Ana"][2:] == ["75", "Muy bueno", "Bajo", "2", "40.0", "3"]
    assert por_conductor["Bruno"][2:] == ["40", "Riesgoso", "Alto", "1", "20.0", "1"]
    assert por_conductor["Elena"][2:] == ["", "", "", "0", "0.0", "0"]


async def test_pdf_de_la_flota(cliente, db, registrar_empresa):
    admin, ana = await escenario(cliente, db, registrar_empresa)
    respuesta = await pedir(cliente, admin, REPORTE_FLOTA, periodo="todo", formato="pdf")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "application/pdf"
    assert respuesta.content.startswith(b"%PDF")
    assert 'filename="reporte-empresa-' in respuesta.headers["content-disposition"]


async def test_texto_de_usuario_no_se_interpreta_como_formula(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    raro = await registrar_conductor(cliente, admin, 1, "=SUMA(A1)")
    await crear_viaje(db, raro["id"], 80, dias_atras=1)
    filas = leer_csv((await pedir(cliente, admin, REPORTE_FLOTA, formato="csv")).text)
    assert "'=SUMA(A1)" in {f[0] for f in filas if f}
