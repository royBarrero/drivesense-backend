from collections.abc import Iterable

from app.telemetria.models import TipoEvento


def contar_por_tipo(filas: Iterable[tuple[TipoEvento, int]]) -> dict[TipoEvento, int]:
    """Cantidad de eventos de cada tipo (HU-16), con los cuatro tipos siempre
    presentes: `filas` (tipo, cantidad) viene de un GROUP BY, que omite los que no hubo."""
    conteo = dict.fromkeys(TipoEvento, 0)
    for tipo, cantidad in filas:
        conteo[tipo] = cantidad
    return conteo
