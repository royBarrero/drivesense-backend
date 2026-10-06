"""Esquemas de recorridos (inicio, historial y detalle): sin base de datos."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.recorridos.schemas import IniciarRecorridoEntrada, RecorridoDetalle, RecorridoHistorial
from app.telemetria.models import TipoEvento
from app.telemetria.schemas import EventoSalida

INICIO = datetime(2026, 10, 4, 12, tzinfo=UTC)


def recorrido(**cambios) -> SimpleNamespace:
    """Como lo devuelve la consulta: un objeto con atributos (`from_attributes`)."""
    datos = dict(
        id=7,
        fecha_inicio=INICIO,
        fecha_fin=INICIO + timedelta(minutes=18),
        distancia_m=7400.0,
        duracion_s=1112,
        velocidad_maxima_kmh=62.0,
        velocidad_promedio_kmh=24.0,
        drivescore=86,
        puntaje_frenadas=70,
        puntaje_aceleraciones=100,
        puntaje_giros=76,
        puntaje_velocidad=100,
        ruta=None,
    )
    return SimpleNamespace(**(datos | cambios))


def test_el_listado_trae_el_drivescore_sin_las_categorias():
    salida = RecorridoHistorial.model_validate(recorrido()).model_dump()
    assert salida["drivescore"] == 86
    assert "puntaje_frenadas" not in salida


def test_un_viaje_sin_puntaje_lo_trae_nulo():
    salida = RecorridoHistorial.model_validate(recorrido(drivescore=None)).model_dump()
    assert salida["drivescore"] is None


def test_el_detalle_sigue_con_el_drivescore_y_las_categorias():
    salida = RecorridoDetalle.model_validate(recorrido()).model_dump()
    assert (salida["drivescore"], salida["puntaje_frenadas"]) == (86, 70)
    assert salida["eventos_por_tipo"] is None
    assert salida["eventos"] is None


def evento(**cambios) -> SimpleNamespace:
    datos = dict(
        tipo=TipoEvento.frenada_brusca,
        fecha=INICIO + timedelta(minutes=2),
        lat=-12.05,
        lon=-77.04,
        velocidad_kmh=38.0,
        intensidad=3.4,
        duracion_s=None,
        velocidad_maxima_kmh=None,
    )
    return SimpleNamespace(**(datos | cambios))


def test_el_detalle_trae_los_eventos_con_su_posicion():
    exceso = evento(
        tipo=TipoEvento.exceso_velocidad,
        fecha=INICIO + timedelta(minutes=10),
        intensidad=2.2,
        duracion_s=9.0,
        velocidad_maxima_kmh=68.0,
    )
    detalle = RecorridoDetalle.model_validate(recorrido())
    detalle.eventos = [EventoSalida.model_validate(e) for e in (evento(), exceso)]
    frenada, salida_exceso = detalle.model_dump(mode="json")["eventos"]
    assert (frenada["tipo"], frenada["lat"], frenada["lon"]) == ("frenada_brusca", -12.05, -77.04)
    assert (frenada["duracion_s"], frenada["velocidad_maxima_kmh"]) == (None, None)
    assert (salida_exceso["duracion_s"], salida_exceso["velocidad_maxima_kmh"]) == (9.0, 68.0)


def test_iniciar_sin_dispositivo_sigue_valido():
    # Una versión anterior de la app solo manda la posición
    datos = IniciarRecorridoEntrada(lat_inicio=-16.5, lon_inicio=-68.1)
    assert datos.dispositivo_modelo is None and datos.version_app is None


def test_iniciar_con_dispositivo_recorta_espacios():
    datos = IniciarRecorridoEntrada(
        lat_inicio=-16.5,
        lon_inicio=-68.1,
        dispositivo_modelo=" samsung SM-A346M ",
        dispositivo_android="14",
        version_app="1.0.3",
    )
    assert datos.dispositivo_modelo == "samsung SM-A346M"


def test_iniciar_rechaza_version_demasiado_larga():
    with pytest.raises(ValidationError):
        IniciarRecorridoEntrada(lat_inicio=-16.5, lon_inicio=-68.1, version_app="1" * 21)
