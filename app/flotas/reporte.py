"""Reporte de desempeño exportable (HU-27): arma los datos del conductor y de la flota.

Los formatos viven en `reporte_pdf.py` y `reporte_csv.py`. Aquí solo hay consultas y
cálculo (promedios ponderados por distancia, como HU-24/25). El reporte nunca incluye
rutas ni ubicaciones.
"""

import enum
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Usuario
from app.core.errores import error_de_campo
from app.flotas.service import (
    _conductor_de_la_empresa,
    _de_la_empresa,
    inicio_periodo,
    nivel_de_riesgo,
)
from app.flotas.schemas import NivelRiesgo, Periodo
from app.puntaje.service import (
    Calificacion,
    ViajePuntuado,
    calcular_eventos_por_10km,
    calificar,
    promedio_ponderado,
)
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.suscripciones.planes import Funcion, limitar_desde
from app.suscripciones.service import exigir_funcion, limite_historial
from app.telemetria.models import Evento, TipoEvento

SIN_VIAJES = "No hay viajes en el periodo seleccionado"

# Categorías del puntaje, en el orden del perfil (HU-25): (campo de ViajePuntuado, nombre)
CATEGORIAS_REPORTE = (
    ("velocidad", "Velocidad"),
    ("frenadas", "Frenadas"),
    ("aceleraciones", "Aceleraciones"),
    ("giros", "Giros"),
)
NOMBRE_TIPO_EVENTO = {
    TipoEvento.frenada_brusca: "Frenada brusca",
    TipoEvento.aceleracion_severa: "Aceleración severa",
    TipoEvento.giro_agresivo: "Giro agresivo",
    TipoEvento.exceso_velocidad: "Exceso de velocidad",
}
NOMBRE_CALIFICACION = {
    Calificacion.excelente: "Excelente",
    Calificacion.muy_bueno: "Muy bueno",
    Calificacion.regular: "Regular",
    Calificacion.riesgoso: "Riesgoso",
}
NOMBRE_NIVEL = {NivelRiesgo.alto: "Alto", NivelRiesgo.medio: "Medio", NivelRiesgo.bajo: "Bajo"}
NOMBRE_TIPO_EMPRESA = {"flota": "Flota", "aseguradora": "Aseguradora"}


class PeriodoReporte(str, enum.Enum):
    siete_dias = "7d"
    treinta_dias = "30d"
    todo = "todo"
    rango = "rango"


class FormatoReporte(str, enum.Enum):
    pdf = "pdf"
    csv = "csv"


@dataclass(frozen=True)
class RangoReporte:
    periodo: PeriodoReporte
    # [desde, hasta) en UTC; nulo = sin tope
    desde: datetime | None
    hasta: datetime | None
    # Días locales de la empresa; nulos en "todo" (salen del primer y último viaje)
    dia_inicio: date | None
    dia_fin: date | None


def resolver_rango(
    periodo: PeriodoReporte,
    desde: date | None,
    hasta: date | None,
    ahora: datetime,
    zona_horaria: str,
) -> RangoReporte:
    """Periodo del reporte según la zona de la empresa. Los días de un rango son
    inclusivos (de la medianoche de `desde` a la de `hasta` + 1). Pura."""
    zona = ZoneInfo(zona_horaria)
    hoy = ahora.astimezone(zona).date()

    if periodo != PeriodoReporte.rango:
        if desde is not None or hasta is not None:
            raise error_de_campo(
                "desde" if desde is not None else "hasta",
                "Solo se usa con el periodo de rango de fechas",
            )
        if periodo == PeriodoReporte.todo:
            return RangoReporte(periodo, None, None, None, None)
        inicio = inicio_periodo(Periodo(periodo.value), ahora, zona_horaria)
        return RangoReporte(periodo, inicio, None, inicio.astimezone(zona).date(), hoy)

    if desde is None:
        raise error_de_campo("desde", "Indica la fecha de inicio")
    if hasta is None:
        raise error_de_campo("hasta", "Indica la fecha de fin")
    if hasta < desde:
        raise error_de_campo("hasta", "La fecha de fin no puede ser anterior a la de inicio")
    if hasta > hoy:
        raise error_de_campo("hasta", "La fecha de fin no puede ser futura")
    return RangoReporte(
        periodo,
        datetime.combine(desde, time.min, tzinfo=zona).astimezone(UTC),
        datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=zona).astimezone(UTC),
        desde,
        hasta,
    )


def limitar_rango(rango: RangoReporte, limite: datetime | None) -> RangoReporte:
    """Recorta el inicio del periodo a la ventana de historial del plan (nulo = sin tope)."""
    return replace(rango, desde=limitar_desde(rango.desde, limite))


def _texto_periodo(rango: RangoReporte, dia_inicio: date | None, dia_fin: date | None) -> str:
    nombre = {
        PeriodoReporte.siete_dias: "Últimos 7 días",
        PeriodoReporte.treinta_dias: "Últimos 30 días",
        PeriodoReporte.todo: "Todo el tiempo",
        PeriodoReporte.rango: "Rango de fechas",
    }[rango.periodo]
    if dia_inicio is None or dia_fin is None:
        return nombre
    return f"{nombre} ({fecha_corta(dia_inicio)} al {fecha_corta(dia_fin)})"


def fecha_corta(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def _slug(texto: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-") or "sin-nombre"


def nombre_archivo(sujeto: str, nombre: str, rango: RangoReporte, hoy: date) -> str:
    """`reporte-conductor-ana-perez-ultimos-7-dias`, sin extensión."""
    if rango.periodo == PeriodoReporte.rango:
        sufijo = f"{rango.dia_inicio.isoformat()}_a_{rango.dia_fin.isoformat()}"
    else:
        sufijo = {
            PeriodoReporte.siete_dias: "ultimos-7-dias",
            PeriodoReporte.treinta_dias: "ultimos-30-dias",
            PeriodoReporte.todo: f"todo-el-tiempo-{hoy.isoformat()}",
        }[rango.periodo]
    return f"reporte-{sujeto}-{_slug(nombre)}-{sufijo}"


# --- Datos del reporte ----------------------------------------------------------


@dataclass(frozen=True)
class ViajeReporte:
    fecha: datetime  # hora local de la empresa
    distancia_m: float
    duracion_s: int
    drivescore: int
    calificacion: Calificacion


@dataclass(frozen=True)
class DatosEmpresa:
    nombre: str
    tipo: str
    email_contacto: str
    telefono: str


@dataclass(frozen=True)
class ReporteConductor:
    empresa: DatosEmpresa
    generado: datetime  # hora local de la empresa
    periodo: str
    archivo: str
    nombre: str
    email: str
    telefono: str
    viajes: list[ViajeReporte]  # del más reciente al más antiguo
    distancia_m: float
    drivescore: int
    calificacion: Calificacion
    nivel_riesgo: NivelRiesgo
    categorias: dict[str, int]  # nombre visible -> puntaje
    eventos_por_tipo: dict[TipoEvento, int]
    eventos: int
    eventos_por_10km: float | None


@dataclass(frozen=True)
class FilaConductorReporte:
    nombre: str
    email: str
    viajes: int
    distancia_m: float
    drivescore: int | None
    calificacion: Calificacion | None
    nivel_riesgo: NivelRiesgo | None
    eventos: int


@dataclass(frozen=True)
class ReporteFlota:
    empresa: DatosEmpresa
    generado: datetime
    periodo: str
    archivo: str
    conductores: int
    conductores_con_viajes: int
    viajes: int
    distancia_m: float
    drivescore: int
    calificacion: Calificacion
    nivel_riesgo: NivelRiesgo
    eventos_por_tipo: dict[TipoEvento, int]
    eventos: int
    eventos_por_10km: float | None
    filas: list[FilaConductorReporte]


def _condiciones(rango: RangoReporte, usuarios: list[int]) -> list:
    """Solo viajes finalizados con puntaje de esos conductores dentro del periodo."""
    condiciones = [
        Recorrido.usuario_id.in_(usuarios),
        Recorrido.estado == EstadoRecorrido.finalizado,
        Recorrido.drivescore.is_not(None),
    ]
    if rango.desde is not None:
        condiciones.append(Recorrido.fecha_inicio >= rango.desde)
    if rango.hasta is not None:
        condiciones.append(Recorrido.fecha_inicio < rango.hasta)
    return condiciones


async def _cargar(db: AsyncSession, condiciones: list):
    """Viajes (sin ruta) y conteo de eventos por conductor y tipo."""
    filas = await db.execute(
        select(
            Recorrido.usuario_id,
            Recorrido.id,
            Recorrido.fecha_inicio,
            Recorrido.distancia_m,
            Recorrido.duracion_s,
            Recorrido.drivescore,
            Recorrido.puntaje_frenadas,
            Recorrido.puntaje_aceleraciones,
            Recorrido.puntaje_giros,
            Recorrido.puntaje_velocidad,
        )
        .where(*condiciones)
        .order_by(Recorrido.fecha_inicio.desc(), Recorrido.id.desc())
    )
    viajes = [
        (
            f.usuario_id,
            f.duracion_s,
            ViajePuntuado(
                id=f.id,
                fecha_inicio=f.fecha_inicio,
                distancia_m=f.distancia_m,
                drivescore=f.drivescore,
                frenadas=f.puntaje_frenadas,
                aceleraciones=f.puntaje_aceleraciones,
                giros=f.puntaje_giros,
                velocidad=f.puntaje_velocidad,
            ),
        )
        for f in filas
    ]
    eventos: dict[int, dict[TipoEvento, int]] = {}
    conteo = await db.execute(
        select(Recorrido.usuario_id, Evento.tipo, func.count())
        .join(Recorrido, Recorrido.id == Evento.recorrido_id)
        .where(*condiciones)
        .group_by(Recorrido.usuario_id, Evento.tipo)
    )
    for usuario_id, tipo, cantidad in conteo:
        eventos.setdefault(usuario_id, dict.fromkeys(TipoEvento, 0))[tipo] = cantidad
    return viajes, eventos


def _empresa(admin: Usuario) -> DatosEmpresa:
    empresa = admin.empresa
    tipo = getattr(empresa.tipo, "value", str(empresa.tipo))
    return DatosEmpresa(
        nombre=empresa.nombre,
        tipo=NOMBRE_TIPO_EMPRESA.get(tipo, tipo),
        email_contacto=empresa.email_contacto,
        telefono=empresa.telefono,
    )


def _dias_de_los_viajes(
    rango: RangoReporte, viajes: list[ViajePuntuado], zona: ZoneInfo
) -> tuple[date | None, date | None]:
    if rango.dia_inicio is not None:
        return rango.dia_inicio, rango.dia_fin
    fechas = [v.fecha_inicio.astimezone(zona).date() for v in viajes]
    return min(fechas), max(fechas)


def _sin_viajes() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=SIN_VIAJES)


def _sumar_eventos(por_usuario: dict[int, dict[TipoEvento, int]]) -> dict[TipoEvento, int]:
    total = dict.fromkeys(TipoEvento, 0)
    for conteo in por_usuario.values():
        for tipo, cantidad in conteo.items():
            total[tipo] += cantidad
    return total


async def reporte_conductor(
    db: AsyncSession,
    admin: Usuario,
    conductor_id: int,
    periodo: PeriodoReporte,
    desde: date | None,
    hasta: date | None,
) -> ReporteConductor:
    exigir_funcion(admin.empresa, Funcion.exportar)
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    ahora = datetime.now(UTC)
    zona = ZoneInfo(admin.empresa.zona_horaria)
    rango = limitar_rango(
        resolver_rango(periodo, desde, hasta, ahora, admin.empresa.zona_horaria),
        limite_historial(admin.empresa, ahora),
    )

    viajes, eventos = await _cargar(db, _condiciones(rango, [conductor.id]))
    if not viajes:
        raise _sin_viajes()

    puntuados = [v for _, _, v in viajes]
    distancia_m = sum(v.distancia_m for v in puntuados)
    drivescore = promedio_ponderado(puntuados)
    calificacion = calificar(drivescore)
    por_tipo = eventos.get(conductor.id, dict.fromkeys(TipoEvento, 0))
    total_eventos = sum(por_tipo.values())
    dia_inicio, dia_fin = _dias_de_los_viajes(rango, puntuados, zona)
    hoy = ahora.astimezone(zona).date()

    return ReporteConductor(
        empresa=_empresa(admin),
        generado=ahora.astimezone(zona),
        periodo=_texto_periodo(rango, dia_inicio, dia_fin),
        archivo=nombre_archivo("conductor", conductor.nombre, rango, hoy),
        nombre=conductor.nombre,
        email=conductor.email,
        telefono=conductor.telefono,
        viajes=[
            ViajeReporte(
                fecha=v.fecha_inicio.astimezone(zona),
                distancia_m=v.distancia_m,
                duracion_s=duracion_s,
                drivescore=v.drivescore,
                calificacion=calificar(v.drivescore),
            )
            for _, duracion_s, v in viajes
        ],
        distancia_m=distancia_m,
        drivescore=drivescore,
        calificacion=calificacion,
        nivel_riesgo=nivel_de_riesgo(calificacion),
        categorias={
            nombre: promedio_ponderado(puntuados, campo) for campo, nombre in CATEGORIAS_REPORTE
        },
        eventos_por_tipo=por_tipo,
        eventos=total_eventos,
        eventos_por_10km=calcular_eventos_por_10km(total_eventos, distancia_m),
    )


async def reporte_flota(
    db: AsyncSession,
    admin: Usuario,
    periodo: PeriodoReporte,
    desde: date | None,
    hasta: date | None,
) -> ReporteFlota:
    exigir_funcion(admin.empresa, Funcion.exportar)
    ahora = datetime.now(UTC)
    zona = ZoneInfo(admin.empresa.zona_horaria)
    rango = limitar_rango(
        resolver_rango(periodo, desde, hasta, ahora, admin.empresa.zona_horaria),
        limite_historial(admin.empresa, ahora),
    )

    conductores = (
        await db.execute(
            select(Usuario.id, Usuario.nombre, Usuario.email)
            .where(_de_la_empresa(admin.empresa_id))
            .order_by(Usuario.nombre, Usuario.id)
        )
    ).all()
    viajes, eventos = await _cargar(db, _condiciones(rango, [c.id for c in conductores]))
    if not viajes:
        raise _sin_viajes()

    por_conductor: dict[int, list[ViajePuntuado]] = {}
    for usuario_id, _, viaje in viajes:
        por_conductor.setdefault(usuario_id, []).append(viaje)

    filas = []
    for c in conductores:
        suyos = por_conductor.get(c.id, [])
        drivescore = promedio_ponderado(suyos) if suyos else None
        calificacion = calificar(drivescore) if drivescore is not None else None
        filas.append(
            FilaConductorReporte(
                nombre=c.nombre,
                email=c.email,
                viajes=len(suyos),
                distancia_m=sum(v.distancia_m for v in suyos),
                drivescore=drivescore,
                calificacion=calificacion,
                nivel_riesgo=nivel_de_riesgo(calificacion),
                eventos=sum(eventos.get(c.id, {}).values()),
            )
        )
    # Mejor DriveScore primero; sin viajes al final, por nombre
    filas.sort(key=lambda f: (f.drivescore is None, -(f.drivescore or 0), f.nombre.lower()))

    puntuados = [v for _, _, v in viajes]
    distancia_m = sum(v.distancia_m for v in puntuados)
    drivescore = promedio_ponderado(puntuados)
    calificacion = calificar(drivescore)
    por_tipo = _sumar_eventos(eventos)
    total_eventos = sum(por_tipo.values())
    dia_inicio, dia_fin = _dias_de_los_viajes(rango, puntuados, zona)
    hoy = ahora.astimezone(zona).date()

    return ReporteFlota(
        empresa=_empresa(admin),
        generado=ahora.astimezone(zona),
        periodo=_texto_periodo(rango, dia_inicio, dia_fin),
        archivo=nombre_archivo("empresa", admin.empresa.nombre, rango, hoy),
        conductores=len(conductores),
        conductores_con_viajes=len(por_conductor),
        viajes=len(viajes),
        distancia_m=distancia_m,
        drivescore=drivescore,
        calificacion=calificacion,
        nivel_riesgo=nivel_de_riesgo(calificacion),
        eventos_por_tipo=por_tipo,
        eventos=total_eventos,
        eventos_por_10km=calcular_eventos_por_10km(total_eventos, distancia_m),
        filas=filas,
    )
