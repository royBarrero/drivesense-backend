"""Tablero de indicadores de la flota (pantalla Reportes del panel).

Reutiliza la carga de viajes y eventos del reporte exportable (`reporte.py`, mismo periodo y
zona horaria de la empresa) y los cálculos del histórico: promedios ponderados por
distancia, solo viajes finalizados con puntaje.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Usuario
from app.flotas.reporte import PeriodoReporte, _cargar, limitar_rango, _condiciones, _sumar_eventos, resolver_rango
from app.flotas.schemas import (
    ConteoCalificaciones,
    EvolucionFlota,
    ItemRanking,
    Periodo,
    PuntoEvolucion,
    TableroFlota,
)
from app.flotas.service import _de_la_empresa
from app.puntaje.service import (
    Agrupacion,
    Calificacion,
    ViajePuntuado,
    agrupar_puntos,
    calcular_eventos_por_10km,
    calificar,
    promedio_ponderado,
)
from app.suscripciones.service import definicion_de, limite_historial
from app.telemetria.models import TipoEvento

# Un conductor entra en los rankings solo si recorrió al menos esto en el periodo: con menos
# distancia un viaje corto lo pondría primero (o "a vigilar") sin que signifique nada.
DISTANCIA_MINIMA_RANKING_M = 20_000.0
MAXIMO_RANKING = 5


@dataclass(frozen=True)
class ConductorTablero:
    """Lo que el tablero necesita de un conductor con viajes en el periodo."""

    id: int
    nombre: str
    drivescore: int
    distancia_m: float
    eventos: int


def _item(c: ConductorTablero) -> ItemRanking:
    return ItemRanking(
        id=c.id,
        nombre=c.nombre,
        drivescore=c.drivescore,
        calificacion=calificar(c.drivescore),
        distancia_m=c.distancia_m,
        eventos=c.eventos,
        eventos_por_10km=calcular_eventos_por_10km(c.eventos, c.distancia_m),
    )


def _con_distancia_minima(conductores: list[ConductorTablero]) -> list[ConductorTablero]:
    return [c for c in conductores if c.distancia_m >= DISTANCIA_MINIMA_RANKING_M]


def top_mejores(conductores: list[ConductorTablero]) -> list[ItemRanking]:
    """Los de mayor DriveScore entre los que superan la distancia mínima; en empate, el que
    recorrió más y luego por nombre."""
    elegibles = _con_distancia_minima(conductores)
    elegibles.sort(key=lambda c: (-c.drivescore, -c.distancia_m, c.nombre.lower(), c.id))
    return [_item(c) for c in elegibles[:MAXIMO_RANKING]]


def top_a_vigilar(conductores: list[ConductorTablero]) -> list[ItemRanking]:
    """Los de más eventos **por cada 10 km** (no en total) entre los que superan la distancia
    mínima y tuvieron al menos un evento; en empate, el de menor DriveScore y luego nombre."""
    elegibles = [c for c in _con_distancia_minima(conductores) if c.eventos > 0]
    elegibles.sort(
        key=lambda c: (-c.eventos / c.distancia_m, c.drivescore, c.nombre.lower(), c.id)
    )
    return [_item(c) for c in elegibles[:MAXIMO_RANKING]]


def contar_calificaciones(
    conductores: list[ConductorTablero], total_activos: int
) -> ConteoCalificaciones:
    cuenta = dict.fromkeys(Calificacion, 0)
    for c in conductores:
        cuenta[calificar(c.drivescore)] += 1
    return ConteoCalificaciones(
        excelente=cuenta[Calificacion.excelente],
        muy_bueno=cuenta[Calificacion.muy_bueno],
        regular=cuenta[Calificacion.regular],
        riesgoso=cuenta[Calificacion.riesgoso],
        sin_viajes=total_activos - len(conductores),
    )


def armar_evolucion(
    viajes: list[ViajePuntuado], inicio: datetime | None, zona: ZoneInfo, periodo: Periodo
) -> EvolucionFlota:
    """Un punto por día (7d y 30d) o por semana ("todo"). Los tramos se cuentan desde la
    medianoche local del primer día: la del periodo, o la del primer viaje en "todo"."""
    agrupacion = Agrupacion.semana if periodo == Periodo.todo else Agrupacion.dia
    if not viajes:
        return EvolucionFlota(agrupacion=agrupacion, puntos=[])
    if inicio is None:
        primero = min(v.fecha_inicio for v in viajes).astimezone(zona).date()
        inicio = datetime.combine(primero, time.min, tzinfo=zona).astimezone(UTC)
    return EvolucionFlota(
        agrupacion=agrupacion,
        puntos=[
            PuntoEvolucion(fecha=p.fecha, drivescore=p.drivescore, viajes=p.viajes)
            for p in agrupar_puntos(viajes, inicio, agrupacion)
        ],
    )


async def obtener_tablero(db: AsyncSession, admin: Usuario, periodo: Periodo) -> TableroFlota:
    ahora = datetime.now(UTC)
    zona = ZoneInfo(admin.empresa.zona_horaria)
    rango = limitar_rango(
        resolver_rango(PeriodoReporte(periodo.value), None, None, ahora, admin.empresa.zona_horaria),
        limite_historial(admin.empresa, ahora),
    )
    completo = definicion_de(admin.empresa, ahora).tablero_completo

    activos = (
        await db.execute(select(Usuario.id, Usuario.nombre).where(_de_la_empresa(admin.empresa_id)))
    ).all()
    viajes, eventos = (
        await _cargar(db, _condiciones(rango, [c.id for c in activos])) if activos else ([], {})
    )

    por_conductor: dict[int, list[ViajePuntuado]] = {}
    for usuario_id, _, viaje in viajes:
        por_conductor.setdefault(usuario_id, []).append(viaje)
    nombres = {c.id: c.nombre for c in activos}
    conductores = [
        ConductorTablero(
            id=usuario_id,
            nombre=nombres[usuario_id],
            drivescore=promedio_ponderado(suyos),
            distancia_m=sum(v.distancia_m for v in suyos),
            eventos=sum(eventos.get(usuario_id, {}).values()),
        )
        for usuario_id, suyos in por_conductor.items()
    ]

    puntuados = [v for _, _, v in viajes]
    drivescore = promedio_ponderado(puntuados) if puntuados else None
    distancia_m = sum(v.distancia_m for v in puntuados)
    por_tipo = _sumar_eventos(eventos)
    total_eventos = sum(por_tipo.values())
    calificaciones = contar_calificaciones(conductores, len(activos))

    return TableroFlota(
        completo=completo,
        conductores=len(activos),
        conductores_con_viajes=len(conductores),
        viajes=len(puntuados),
        distancia_m=distancia_m,
        drivescore=drivescore,
        calificacion=calificar(drivescore) if drivescore is not None else None,
        eventos=total_eventos,
        eventos_por_10km=calcular_eventos_por_10km(total_eventos, distancia_m),
        conductores_en_riesgo=calificaciones.riesgoso,
        distancia_minima_ranking_m=DISTANCIA_MINIMA_RANKING_M,
        # Sin el plan completo solo van las tarjetas: lo demás sale vacío desde el backend
        mejores=top_mejores(conductores) if completo else [],
        a_vigilar=top_a_vigilar(conductores) if completo else [],
        por_calificacion=calificaciones if completo else ConteoCalificaciones(
            excelente=0, muy_bueno=0, regular=0, riesgoso=0, sin_viajes=0
        ),
        eventos_por_tipo=por_tipo if completo else dict.fromkeys(TipoEvento, 0),
        evolucion=(
            armar_evolucion(puntuados, rango.desde, zona, periodo)
            if completo
            else EvolucionFlota(
                agrupacion=Agrupacion.semana if periodo == Periodo.todo else Agrupacion.dia,
                puntos=[],
            )
        ),
    )
