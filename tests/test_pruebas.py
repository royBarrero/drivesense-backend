"""Modo pruebas (temporal): acceso, eventos por 10 km y esquema del listado (sin base de
datos) y, con base de datos, que oculta las cuentas del seed. Se retira junto con
`app/pruebas/`."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.auth.models import RolUsuario
from app.core.config import settings
from app.pruebas.models import ResultadoValidacion
from app.pruebas.schemas import ConductorPruebas, DetallePruebas, RecorridoPruebas
from app.pruebas.service import COLUMNAS_CSV, es_cuenta_pruebas, eventos_por_10km, fila_csv
from app.recorridos.models import EstadoRecorrido
from app.telemetria.models import TipoEvento


@pytest.fixture
def correo_pruebas(monkeypatch):
    monkeypatch.setattr(settings, "pruebas_correo", " Pruebas@DriveSense.app ")


def usuario(email: str, rol: RolUsuario = RolUsuario.admin_empresa) -> SimpleNamespace:
    return SimpleNamespace(email=email, rol=rol)


def test_cuenta_pruebas_admin_con_el_correo(correo_pruebas):
    # El correo se guarda en minúsculas; el de la configuración se normaliza
    assert es_cuenta_pruebas(usuario("pruebas@drivesense.app"))


def test_otro_admin_no_es_cuenta_pruebas(correo_pruebas):
    assert not es_cuenta_pruebas(usuario("otro@drivesense.app"))


def test_conductor_con_el_correo_no_es_cuenta_pruebas(correo_pruebas):
    assert not es_cuenta_pruebas(usuario("pruebas@drivesense.app", RolUsuario.conductor))


def test_sin_correo_configurado_nadie_es_cuenta_pruebas(monkeypatch):
    monkeypatch.setattr(settings, "pruebas_correo", "")
    assert not es_cuenta_pruebas(usuario(""))


def test_eventos_por_10km():
    # 57 eventos en 412 km → 1,38 → 1,4
    assert eventos_por_10km(57, 412_000) == 1.4
    assert eventos_por_10km(0, 5_000) == 0


def test_eventos_por_10km_redondea_05_hacia_arriba():
    # 1 evento en 40 km → 0,25 → 0,3 (round de Python daría 0,2)
    assert eventos_por_10km(1, 40_000) == 0.3


def test_eventos_por_10km_sin_distancia_es_nulo():
    assert eventos_por_10km(0, 0) is None


def recorrido(drivescore: int | None, estado=EstadoRecorrido.finalizado) -> RecorridoPruebas:
    fecha = datetime(2026, 10, 6, 8, 10, tzinfo=UTC)
    return RecorridoPruebas(
        id=1,
        estado=estado,
        conductor=ConductorPruebas(id=7, nombre="Luis Quispe", email="luis@correo.com"),
        fecha_inicio=fecha,
        fecha_fin=fecha,
        distancia_m=16_200,
        duracion_s=1_500,
        velocidad_maxima_kmh=72,
        velocidad_promedio_kmh=39,
        drivescore=drivescore,
    )


def test_recorrido_trae_su_calificacion():
    salida = recorrido(78).model_dump(mode="json")
    assert salida["calificacion"] == "muy_bueno"
    assert salida["conductor"]["nombre"] == "Luis Quispe"


def test_descartado_sin_puntaje_ni_calificacion():
    salida = recorrido(None, EstadoRecorrido.descartado).model_dump(mode="json")
    assert salida["estado"] == "descartado"
    assert salida["drivescore"] is None
    assert salida["calificacion"] is None
    assert salida["eventos_por_tipo"] is None
    # Viaje de una app que no envía el dispositivo
    assert salida["dispositivo_modelo"] is None


def test_fila_csv_con_eventos_y_dispositivo():
    viaje = recorrido(63).model_copy(
        update={
            "eventos_por_tipo": {
                TipoEvento.frenada_brusca: 3,
                TipoEvento.aceleracion_severa: 2,
                TipoEvento.giro_agresivo: 1,
                TipoEvento.exceso_velocidad: 4,
            },
            "dispositivo_modelo": "motorola moto g54",
            "dispositivo_android": "14",
            "version_app": "1.0.2",
        }
    )
    fila = fila_csv(viaje)
    assert len(fila) == len(COLUMNAS_CSV)
    columnas = dict(zip(COLUMNAS_CSV, fila))
    assert columnas["dispositivo"] == "motorola moto g54"
    assert columnas["duracion_min"] == 25.0
    assert columnas["distancia_km"] == 16.2
    assert [columnas[c] for c in ("frenadas", "aceleraciones", "giros", "excesos")] == [3, 2, 1, 4]
    assert columnas["drivescore"] == 63
    assert columnas["calificacion"] == "regular"


def test_fila_csv_de_descartado_deja_vacios_los_conteos():
    columnas = dict(zip(COLUMNAS_CSV, fila_csv(recorrido(None, EstadoRecorrido.descartado))))
    assert columnas["estado"] == "descartado"
    assert columnas["frenadas"] == ""
    assert columnas["drivescore"] == ""
    assert columnas["calificacion"] == ""


def test_detalle_con_eventos_marcados():
    fecha = datetime(2026, 10, 6, 19, 5, tzinfo=UTC)
    evento = {
        "id": 10,
        "tipo": "frenada_brusca",
        "fecha": fecha,
        "lat": -16.5,
        "lon": -68.1,
        "velocidad_kmh": 41,
        "intensidad": 3.6,
        "duracion_s": None,
        "velocidad_maxima_kmh": None,
    }
    detalle = DetallePruebas.model_validate(
        {
            **recorrido(63).model_dump(exclude={"calificacion", "eventos_por_tipo"}),
            "ruta": None,
            "puntaje_frenadas": 62,
            "puntaje_aceleraciones": 70,
            "puntaje_giros": 85,
            "puntaje_velocidad": 48,
            "eventos": [{**evento, "validacion": "falso"}, {**evento, "id": 11}],
        }
    ).model_dump(mode="json")
    assert detalle["calificacion"] == "regular"
    assert detalle["conductor"]["id"] == 7
    assert [e["validacion"] for e in detalle["eventos"]] == ["falso", None]


# --- Con base de datos: las cuentas del seed de presentación no aparecen -----


async def _conductor_con_viaje(db, email: str):
    from app.auth.models import Usuario
    from tests.test_flotas_perfil import crear_viaje

    conductor = Usuario(
        nombre=email.split("@")[0],
        email=email,
        telefono="70000000",
        contrasenia_hash="x",
        rol=RolUsuario.conductor,
        activo=True,
        debe_cambiar_contrasenia=False,
    )
    db.add(conductor)
    await db.flush()
    viaje = await crear_viaje(db, conductor.id, 80, dias_atras=1, eventos=[TipoEvento.frenada_brusca])
    await db.commit()
    return viaje


async def test_modo_pruebas_oculta_los_viajes_del_seed(db):
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.pruebas import service
    from app.telemetria.models import Evento

    tester = await _conductor_con_viaje(db, "tester@empresa.example.com")
    ejemplo = await _conductor_con_viaje(db, "ana@ejemplo.com")

    resumen = await service.obtener_resumen(db, None, None)
    assert (resumen.testers, resumen.testers_registrados, resumen.viajes, resumen.eventos) == (1, 1, 1, 1)
    assert (await service.obtener_resumen(db, None, ejemplo.usuario_id)).viajes == 0

    pagina = await service.listar_recorridos(db, None, None, None, 1, 20)
    assert pagina.total == 1 and [r.id for r in pagina.recorridos] == [tester.id]
    assert (await service.exportar_recorridos(db, None, None, None)).count("\n") == 2
    assert [t.email for t in await service.listar_testers(db, None)] == ["tester@empresa.example.com"]

    # El detalle del tester funciona (antes fallaba por las recomendaciones) y el del seed es 404
    detalle = await service.obtener_detalle(db, tester.id)
    assert detalle.eventos_por_tipo[TipoEvento.frenada_brusca] == 1
    with pytest.raises(HTTPException) as error:
        await service.obtener_detalle(db, ejemplo.id)
    assert error.value.status_code == 404

    evento_ejemplo = await db.scalar(select(Evento.id).where(Evento.recorrido_id == ejemplo.id))
    with pytest.raises(HTTPException) as error:
        await service.marcar_evento(db, evento_ejemplo, ResultadoValidacion.correcto)
    assert error.value.status_code == 404
