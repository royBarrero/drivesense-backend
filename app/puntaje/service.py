"""DriveScore del viaje (HU-15), su histórico (HU-17) y el comparativo entre periodos (HU-20).

La fórmula, el histórico y el comparativo son puros; `obtener_historico` y
`obtener_comparativo` hacen las consultas.
"""

import enum
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Usuario
from app.core.errores import error_de_campo
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento


class ParametrosPuntaje:
    """Valores de la fórmula, juntos para ajustarlos sin tocar la lógica.

    Con ellos, un viaje de 7,4 km con 1 frenada y 1 giro, sin exceso, da 86.
    """

    # Puntos que resta cada evento por cada 10 km recorridos
    RESTA_FRENADA = 22.0
    RESTA_ACELERACION = 18.0
    RESTA_GIRO = 18.0
    # Los viajes cortos cuentan como de esta distancia: un evento en 1 km no
    # debe hundir el puntaje
    DISTANCIA_MINIMA_KM = 5.0
    KM_REFERENCIA = 10.0

    # Puntos que resta cada 1 % del tiempo del viaje en exceso de velocidad
    RESTA_POR_PORCENTAJE_EXCESO = 2.0

    # Pesos del DriveScore (suman 1)
    PESO_VELOCIDAD = 0.30
    PESO_FRENADAS = 0.30
    PESO_ACELERACIONES = 0.20
    PESO_GIROS = 0.20


@dataclass(frozen=True)
class EventoPuntaje:
    """Lo que la fórmula necesita de cada evento."""

    tipo: TipoEvento
    duracion_s: float | None = None


@dataclass(frozen=True)
class Puntaje:
    drivescore: int
    frenadas: int
    aceleraciones: int
    giros: int
    velocidad: int


def _limitar(valor: float) -> float:
    return min(100.0, max(0.0, valor))


def _redondear(valor: float) -> int:
    """Entero más cercano, con 0,5 hacia arriba (`round` de Python redondea al par)."""
    return math.floor(valor + 0.5)


def calcular_puntaje(
    distancia_m: float, duracion_s: int, eventos: Iterable[EventoPuntaje]
) -> Puntaje:
    """Puntaje de cada categoría y DriveScore (0–100) de un viaje finalizado."""
    p = ParametrosPuntaje
    eventos = list(eventos)

    def cantidad(tipo: TipoEvento) -> int:
        return sum(1 for e in eventos if e.tipo == tipo)

    km = max(distancia_m / 1000, p.DISTANCIA_MINIMA_KM)
    por_10_km = p.KM_REFERENCIA / km

    def por_maniobras(tipo: TipoEvento, resta: float) -> float:
        return _limitar(100 - resta * cantidad(tipo) * por_10_km)

    frenadas = por_maniobras(TipoEvento.frenada_brusca, p.RESTA_FRENADA)
    aceleraciones = por_maniobras(TipoEvento.aceleracion_severa, p.RESTA_ACELERACION)
    giros = por_maniobras(TipoEvento.giro_agresivo, p.RESTA_GIRO)

    segundos_exceso = sum(
        e.duracion_s or 0 for e in eventos if e.tipo == TipoEvento.exceso_velocidad
    )
    if duracion_s > 0:
        porcentaje = min(segundos_exceso, duracion_s) / duracion_s * 100
    else:
        porcentaje = 100.0 if segundos_exceso > 0 else 0.0
    velocidad = _limitar(100 - p.RESTA_POR_PORCENTAJE_EXCESO * porcentaje)

    drivescore = (
        p.PESO_VELOCIDAD * velocidad
        + p.PESO_FRENADAS * frenadas
        + p.PESO_ACELERACIONES * aceleraciones
        + p.PESO_GIROS * giros
    )
    return Puntaje(
        drivescore=_redondear(_limitar(drivescore)),
        frenadas=_redondear(frenadas),
        aceleraciones=_redondear(aceleraciones),
        giros=_redondear(giros),
        velocidad=_redondear(velocidad),
    )


# --- Histórico del DriveScore (HU-17) ---------------------------------------


class Calificacion(str, enum.Enum):
    """Mismos rangos que la app: Excelente 90–100, Muy bueno 75–89, Regular 60–74,
    Riesgoso < 60."""

    excelente = "excelente"
    muy_bueno = "muy_bueno"
    regular = "regular"
    riesgoso = "riesgoso"


def calificar(drivescore: int) -> Calificacion:
    if drivescore >= 90:
        return Calificacion.excelente
    if drivescore >= 75:
        return Calificacion.muy_bueno
    if drivescore >= 60:
        return Calificacion.regular
    return Calificacion.riesgoso


class Agrupacion(str, enum.Enum):
    """Qué representa cada punto del gráfico."""

    viaje = "viaje"
    dia = "dia"
    semana = "semana"


# Hasta 8 días, un punto por viaje; hasta 31, uno por día con viajes; si no, por
# semana (~13 en 3 meses): así el gráfico no recibe cientos de puntos
MAXIMO_DIAS_POR_VIAJE = 8
MAXIMO_DIAS_POR_DIA = 31

CATEGORIAS = ("frenadas", "aceleraciones", "giros", "velocidad")


@dataclass(frozen=True)
class ViajePuntuado:
    """Lo que el histórico necesita de cada recorrido finalizado con puntaje."""

    id: int
    fecha_inicio: datetime
    distancia_m: float
    drivescore: int
    frenadas: int
    aceleraciones: int
    giros: int
    velocidad: int
    # Cantidad de eventos del viaje (solo la usa el comparativo, HU-20)
    eventos: int = 0


@dataclass(frozen=True)
class PuntoHistorico:
    # En los grupos, el inicio del día o de la semana (contados desde `desde`)
    fecha: datetime
    drivescore: int
    viajes: int


@dataclass(frozen=True)
class PromedioCategoria:
    promedio: int
    # Frente al periodo anterior; nula si ese periodo no tiene viajes
    diferencia: int | None


@dataclass(frozen=True)
class Historico:
    desde: datetime
    hasta: datetime
    viajes: int
    viajes_totales: int
    promedio_total: int | None
    promedio: int | None
    calificacion: Calificacion | None
    tendencia: int | None
    agrupacion: Agrupacion
    puntos: list[PuntoHistorico]
    categorias: dict[str, PromedioCategoria] | None
    mejor: ViajePuntuado | None
    peor: ViajePuntuado | None


def promedio_desde_sumas(suma_ponderada: float | None, suma_distancia: float | None) -> int | None:
    """Promedio ponderado por distancia a partir de `sum(puntaje × distancia)` y
    `sum(distancia)`; nulo sin viajes."""
    if not suma_distancia:
        return None
    return _redondear(_limitar(suma_ponderada / suma_distancia))


def promedio_ponderado(viajes: list[ViajePuntuado], campo: str = "drivescore") -> int | None:
    """Promedio de `campo` ponderado por distancia: un viaje largo pesa más que uno corto."""
    return promedio_desde_sumas(
        sum(getattr(v, campo) * v.distancia_m for v in viajes),
        sum(v.distancia_m for v in viajes),
    )


def agrupacion_para(desde: datetime, hasta: datetime) -> Agrupacion:
    dias = (hasta - desde) / timedelta(days=1)
    if dias <= MAXIMO_DIAS_POR_VIAJE:
        return Agrupacion.viaje
    if dias <= MAXIMO_DIAS_POR_DIA:
        return Agrupacion.dia
    return Agrupacion.semana


def agrupar_puntos(
    viajes: list[ViajePuntuado], desde: datetime, agrupacion: Agrupacion
) -> list[PuntoHistorico]:
    """Puntos del gráfico en orden cronológico. Los días y semanas se cuentan desde
    `desde`, que trae la zona del teléfono (medianoche local): no hace falta la zona."""
    ordenados = sorted(viajes, key=lambda v: v.fecha_inicio)
    if agrupacion == Agrupacion.viaje:
        return [PuntoHistorico(v.fecha_inicio, v.drivescore, 1) for v in ordenados]

    tramo = timedelta(days=1 if agrupacion == Agrupacion.dia else 7)
    grupos: dict[int, list[ViajePuntuado]] = {}
    for viaje in ordenados:
        grupos.setdefault((viaje.fecha_inicio - desde) // tramo, []).append(viaje)
    return [
        PuntoHistorico(desde + indice * tramo, promedio_ponderado(grupo), len(grupo))
        for indice, grupo in grupos.items()
    ]


def mejor_y_peor(viajes: list[ViajePuntuado]) -> tuple[ViajePuntuado | None, ViajePuntuado | None]:
    """Mayor y menor DriveScore; en empate, el más reciente. Con un solo viaje no hay peor."""
    if not viajes:
        return None, None
    mejor = max(viajes, key=lambda v: (v.drivescore, v.fecha_inicio))
    if len(viajes) == 1:
        return mejor, None
    peor = min(viajes, key=lambda v: (v.drivescore, -v.fecha_inicio.timestamp()))
    return mejor, peor


def _diferencia(actual: int | None, anterior: int | None) -> int | None:
    return None if actual is None or anterior is None else actual - anterior


def resumir_historico(
    actuales: list[ViajePuntuado],
    anteriores: list[ViajePuntuado],
    desde: datetime,
    hasta: datetime,
    viajes_totales: int,
    promedio_total: int | None,
) -> Historico:
    """Histórico del periodo `[desde, hasta)` frente al anterior de igual duración."""
    promedio = promedio_ponderado(actuales)
    agrupacion = agrupacion_para(desde, hasta)
    categorias = None
    if actuales:
        categorias = {}
        for campo in CATEGORIAS:
            actual = promedio_ponderado(actuales, campo)
            categorias[campo] = PromedioCategoria(
                actual, _diferencia(actual, promedio_ponderado(anteriores, campo))
            )
    mejor, peor = mejor_y_peor(actuales)
    return Historico(
        desde=desde,
        hasta=hasta,
        viajes=len(actuales),
        viajes_totales=viajes_totales,
        promedio_total=promedio_total,
        promedio=promedio,
        calificacion=None if promedio is None else calificar(promedio),
        tendencia=_diferencia(promedio, promedio_ponderado(anteriores)),
        agrupacion=agrupacion,
        puntos=agrupar_puntos(actuales, desde, agrupacion),
        categorias=categorias,
        mejor=mejor,
        peor=peor,
    )


# Un año y un día: alcanza para cualquier periodo de la app (el mayor es 3 meses)
MAXIMO_DIAS_HISTORICO = 366


async def obtener_historico(db: AsyncSession, usuario: Usuario, desde: datetime) -> Historico:
    """Histórico del DriveScore del conductor desde `desde` hasta ahora (HU-17).

    Solo cuentan sus recorridos finalizados con puntaje (los anteriores a HU-15 no).
    """
    hasta = datetime.now(UTC)
    if desde >= hasta:
        raise error_de_campo("desde", "El inicio del periodo no puede estar en el futuro")
    if hasta - desde > timedelta(days=MAXIMO_DIAS_HISTORICO):
        raise error_de_campo("desde", "El periodo no puede ser mayor a un año")

    con_puntaje = (
        Recorrido.usuario_id == usuario.id,
        Recorrido.estado == EstadoRecorrido.finalizado,
        Recorrido.drivescore.is_not(None),
    )
    # El periodo anterior, de igual duración, en la misma consulta
    inicio_anterior = desde - (hasta - desde)
    filas = await db.execute(
        select(
            Recorrido.id,
            Recorrido.fecha_inicio,
            Recorrido.distancia_m,
            Recorrido.drivescore,
            Recorrido.puntaje_frenadas,
            Recorrido.puntaje_aceleraciones,
            Recorrido.puntaje_giros,
            Recorrido.puntaje_velocidad,
        ).where(*con_puntaje, Recorrido.fecha_inicio >= inicio_anterior, Recorrido.fecha_inicio < hasta)
    )
    viajes = [ViajePuntuado(*fila) for fila in filas]

    viajes_totales, suma_ponderada, suma_distancia = (
        await db.execute(
            select(
                func.count(),
                func.sum(Recorrido.drivescore * Recorrido.distancia_m),
                func.sum(Recorrido.distancia_m),
            ).where(*con_puntaje)
        )
    ).one()

    return resumir_historico(
        actuales=[v for v in viajes if v.fecha_inicio >= desde],
        anteriores=[v for v in viajes if v.fecha_inicio < desde],
        desde=desde,
        hasta=hasta,
        viajes_totales=viajes_totales,
        promedio_total=promedio_desde_sumas(suma_ponderada, suma_distancia),
    )


# --- Comparativo entre periodos (HU-20) --------------------------------------


class PeriodoComparativo(str, enum.Enum):
    """Periodos calendario: el actual cuenta lo que va; el anterior, completo."""

    semana = "semana"
    mes = "mes"


class Resultado(str, enum.Enum):
    mejoro = "mejoro"
    empeoro = "empeoro"
    igual = "igual"


# Viajes con puntaje que necesita cada periodo para compararlos: con uno solo, el
# promedio sería ese viaje
MINIMO_VIAJES_COMPARATIVO = 2

# Desempate de "lo que más mejoró / empeoró": el mismo orden de riesgo de la
# recomendación (HU-18) y de "Lo que más restó" en la app
CATEGORIAS_POR_RIESGO = ("velocidad", "frenadas", "giros", "aceleraciones")


@dataclass(frozen=True)
class ResumenPeriodo:
    desde: datetime
    hasta: datetime
    viajes: int
    distancia_m: float
    eventos: int
    # Nulos si el periodo no tiene viajes
    drivescore: int | None
    calificacion: Calificacion | None
    categorias: dict[str, int] | None
    # Un decimal, sobre la distancia de los viajes
    eventos_por_10km: float | None


@dataclass(frozen=True)
class Cambio:
    # actual − anterior
    diferencia: int | float
    resultado: Resultado


@dataclass(frozen=True)
class Comparativo:
    periodo: PeriodoComparativo
    minimo_viajes: int
    actual: ResumenPeriodo
    anterior: ResumenPeriodo
    # Los dos periodos con `minimo_viajes` o más; si no, lo que sigue es nulo
    comparable: bool
    drivescore: Cambio | None = None
    categorias: dict[str, Cambio] | None = None
    eventos_por_10km: Cambio | None = None
    # Diferencias sin sentido de mejora: más viajes no es manejar mejor
    viajes: int | None = None
    distancia_m: float | None = None
    mas_mejoro: str | None = None
    mas_empeoro: str | None = None


def inicio_anterior(periodo: PeriodoComparativo, desde: datetime) -> datetime:
    """Inicio del periodo anterior: el lunes previo o el día 1 del mes previo, con la
    misma zona que `desde`."""
    if periodo == PeriodoComparativo.semana:
        return desde - timedelta(days=7)
    if desde.month == 1:
        return desde.replace(year=desde.year - 1, month=12)
    return desde.replace(month=desde.month - 1)


def validar_inicio(periodo: PeriodoComparativo, desde: datetime) -> None:
    """`desde` debe ser la medianoche de un lunes (semana) o de un día 1 (mes) en su
    propia zona, que es la del teléfono."""
    medianoche = (desde.hour, desde.minute, desde.second, desde.microsecond) == (0, 0, 0, 0)
    if periodo == PeriodoComparativo.semana and not (medianoche and desde.weekday() == 0):
        raise error_de_campo("desde", "El inicio de la semana debe ser un lunes a medianoche")
    if periodo == PeriodoComparativo.mes and not (medianoche and desde.day == 1):
        raise error_de_campo("desde", "El inicio del mes debe ser el día 1 a medianoche")


def calcular_eventos_por_10km(eventos: int, distancia_m: float) -> float | None:
    """Un decimal, con 0,5 hacia arriba; nulo sin distancia."""
    if distancia_m <= 0:
        return None
    return math.floor(eventos / (distancia_m / 10_000) * 10 + 0.5) / 10


def resumir_periodo(
    viajes: list[ViajePuntuado], desde: datetime, hasta: datetime
) -> ResumenPeriodo:
    distancia = sum(v.distancia_m for v in viajes)
    eventos = sum(v.eventos for v in viajes)
    drivescore = promedio_ponderado(viajes)
    return ResumenPeriodo(
        desde=desde,
        hasta=hasta,
        viajes=len(viajes),
        distancia_m=distancia,
        eventos=eventos,
        drivescore=drivescore,
        calificacion=None if drivescore is None else calificar(drivescore),
        categorias={c: promedio_ponderado(viajes, c) for c in CATEGORIAS} if viajes else None,
        eventos_por_10km=calcular_eventos_por_10km(eventos, distancia) if viajes else None,
    )


def comparar(actual: float, anterior: float, mayor_es_mejor: bool = True) -> Cambio:
    """actual − anterior y si eso es mejora. Se redondea a un decimal: los eventos
    por 10 km ya vienen así y la resta en float dejaría 0,30000000000000004."""
    diferencia = round(actual - anterior, 1)
    if diferencia == 0:
        return Cambio(0, Resultado.igual)
    mejoro = (diferencia > 0) == mayor_es_mejor
    return Cambio(diferencia, Resultado.mejoro if mejoro else Resultado.empeoro)


def destacadas(categorias: dict[str, Cambio]) -> tuple[str | None, str | None]:
    """Categoría que más subió y la que más bajó; en empate, la de mayor riesgo
    (`sorted` es estable). Nula si ninguna subió (o ninguna bajó)."""
    subieron = sorted(
        (c for c in CATEGORIAS_POR_RIESGO if categorias[c].diferencia > 0),
        key=lambda c: -categorias[c].diferencia,
    )
    bajaron = sorted(
        (c for c in CATEGORIAS_POR_RIESGO if categorias[c].diferencia < 0),
        key=lambda c: categorias[c].diferencia,
    )
    return (subieron[0] if subieron else None), (bajaron[0] if bajaron else None)


def comparar_periodos(
    periodo: PeriodoComparativo,
    actuales: list[ViajePuntuado],
    anteriores: list[ViajePuntuado],
    desde: datetime,
    hasta: datetime,
    desde_anterior: datetime,
) -> Comparativo:
    """Periodo `[desde, hasta)` frente a `[desde_anterior, desde)` (HU-20)."""
    actual = resumir_periodo(actuales, desde, hasta)
    anterior = resumir_periodo(anteriores, desde_anterior, desde)
    base = {
        "periodo": periodo,
        "minimo_viajes": MINIMO_VIAJES_COMPARATIVO,
        "actual": actual,
        "anterior": anterior,
    }
    if min(actual.viajes, anterior.viajes) < MINIMO_VIAJES_COMPARATIVO:
        return Comparativo(**base, comparable=False)

    categorias = {c: comparar(actual.categorias[c], anterior.categorias[c]) for c in CATEGORIAS}
    mas_mejoro, mas_empeoro = destacadas(categorias)
    return Comparativo(
        **base,
        comparable=True,
        drivescore=comparar(actual.drivescore, anterior.drivescore),
        categorias=categorias,
        eventos_por_10km=comparar(
            actual.eventos_por_10km, anterior.eventos_por_10km, mayor_es_mejor=False
        ),
        viajes=actual.viajes - anterior.viajes,
        distancia_m=actual.distancia_m - anterior.distancia_m,
        mas_mejoro=mas_mejoro,
        mas_empeoro=mas_empeoro,
    )


async def obtener_comparativo(
    db: AsyncSession, usuario: Usuario, periodo: PeriodoComparativo, desde: datetime
) -> Comparativo:
    """Periodo actual del conductor frente al anterior (HU-20).

    Solo cuentan sus recorridos finalizados con puntaje; una sola consulta trae los
    dos periodos con la cantidad de eventos de cada viaje.
    """
    hasta = datetime.now(UTC)
    if desde >= hasta:
        raise error_de_campo("desde", "El inicio del periodo no puede estar en el futuro")
    validar_inicio(periodo, desde)
    desde_anterior = inicio_anterior(periodo, desde)

    eventos = (
        select(func.count(Evento.id))
        .where(Evento.recorrido_id == Recorrido.id)
        .correlate(Recorrido)
        .scalar_subquery()
    )
    filas = await db.execute(
        select(
            Recorrido.id,
            Recorrido.fecha_inicio,
            Recorrido.distancia_m,
            Recorrido.drivescore,
            Recorrido.puntaje_frenadas,
            Recorrido.puntaje_aceleraciones,
            Recorrido.puntaje_giros,
            Recorrido.puntaje_velocidad,
            eventos,
        ).where(
            Recorrido.usuario_id == usuario.id,
            Recorrido.estado == EstadoRecorrido.finalizado,
            Recorrido.drivescore.is_not(None),
            Recorrido.fecha_inicio >= desde_anterior,
            Recorrido.fecha_inicio < hasta,
        )
    )
    viajes = [ViajePuntuado(*fila) for fila in filas]
    return comparar_periodos(
        periodo,
        actuales=[v for v in viajes if v.fecha_inicio >= desde],
        anteriores=[v for v in viajes if v.fecha_inicio < desde],
        desde=desde,
        hasta=hasta,
        desde_anterior=desde_anterior,
    )
