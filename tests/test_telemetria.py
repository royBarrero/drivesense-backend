"""Conteo de eventos por tipo del detalle del recorrido (HU-16): sin base de datos."""

from app.telemetria.models import TipoEvento
from app.telemetria.service import contar_por_tipo


def test_sin_eventos_los_cuatro_tipos_en_0():
    assert contar_por_tipo([]) == {
        TipoEvento.frenada_brusca: 0,
        TipoEvento.aceleracion_severa: 0,
        TipoEvento.giro_agresivo: 0,
        TipoEvento.exceso_velocidad: 0,
    }


def test_completa_los_tipos_que_faltan():
    conteo = contar_por_tipo([(TipoEvento.frenada_brusca, 2), (TipoEvento.giro_agresivo, 1)])
    assert conteo == {
        TipoEvento.frenada_brusca: 2,
        TipoEvento.aceleracion_severa: 0,
        TipoEvento.giro_agresivo: 1,
        TipoEvento.exceso_velocidad: 0,
    }


def test_con_todos_los_tipos():
    filas = [(tipo, i + 1) for i, tipo in enumerate(TipoEvento)]
    assert contar_por_tipo(filas) == dict(filas)
