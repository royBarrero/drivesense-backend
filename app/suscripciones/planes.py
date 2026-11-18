"""Catálogo de planes de suscripción y reglas puras (sin base de datos).

Los precios son ilustrativos: cambiarlos es cambiar `precio_bs` aquí.
"""

import calendar
import enum
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


class PlanSuscripcion(str, enum.Enum):
    gratis = "gratis"
    premium = "premium"
    empresas = "empresas"


class Funcion(str, enum.Enum):
    exportar = "exportar"
    eventos_mapa_patrones = "eventos_mapa_patrones"
    tablero_completo = "tablero_completo"


@dataclass(frozen=True)
class DefinicionPlan:
    plan: PlanSuscripcion
    nombre: str
    # Nulo = sin tope
    limite_conductores: int | None
    # Ventana de historial visible; ambos nulos = historial completo
    historial_dias: int | None
    historial_meses: int | None
    exportar: bool
    eventos_mapa_patrones: bool
    tablero_completo: bool
    precio_bs: int
    # Nulo = no vence (Gratis)
    duracion_meses: int | None


PLANES: dict[PlanSuscripcion, DefinicionPlan] = {
    PlanSuscripcion.gratis: DefinicionPlan(
        plan=PlanSuscripcion.gratis,
        nombre="Gratis",
        limite_conductores=3,
        historial_dias=30,
        historial_meses=None,
        exportar=False,
        eventos_mapa_patrones=False,
        tablero_completo=False,
        precio_bs=0,
        duracion_meses=None,
    ),
    PlanSuscripcion.premium: DefinicionPlan(
        plan=PlanSuscripcion.premium,
        nombre="Premium",
        limite_conductores=15,
        historial_dias=None,
        historial_meses=12,
        exportar=True,
        eventos_mapa_patrones=True,
        tablero_completo=True,
        precio_bs=200,
        duracion_meses=1,
    ),
    PlanSuscripcion.empresas: DefinicionPlan(
        plan=PlanSuscripcion.empresas,
        nombre="Empresas",
        limite_conductores=None,
        historial_dias=None,
        historial_meses=None,
        exportar=True,
        eventos_mapa_patrones=True,
        tablero_completo=True,
        precio_bs=500,
        duracion_meses=1,
    ),
}

# Días antes del vencimiento desde los que se avisa
DIAS_AVISO_VENCIMIENTO = 7


def sumar_meses(fecha: datetime, meses: int) -> datetime:
    """Mismo día y hora `meses` después; si el mes destino es más corto, su último día."""
    indice = fecha.month - 1 + meses
    anio, mes = fecha.year + indice // 12, indice % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


def dias_restantes(vencimiento: datetime, ahora: datetime, zona_horaria: str) -> int:
    """Días de calendario (en la zona de la empresa) de hoy a la fecha de vencimiento; 0 el
    mismo día del vencimiento y nunca negativo."""
    zona = ZoneInfo(zona_horaria)
    return max(0, (vencimiento.astimezone(zona).date() - ahora.astimezone(zona).date()).days)


def plan_efectivo(
    plan: PlanSuscripcion, vencimiento: datetime | None, ahora: datetime, zona_horaria: str
) -> PlanSuscripcion:
    """El plan que rige ahora, sin tareas programadas: se compara la fecha de vencimiento con
    la de hoy en la zona de la empresa. El plan rige hasta el final del día del vencimiento;
    un día después, o sin vencimiento, un plan de pago vale como Gratis."""
    if plan == PlanSuscripcion.gratis:
        return plan
    if vencimiento is None:
        return PlanSuscripcion.gratis
    zona = ZoneInfo(zona_horaria)
    if ahora.astimezone(zona).date() > vencimiento.astimezone(zona).date():
        return PlanSuscripcion.gratis
    return plan


def inicio_historial(definicion: DefinicionPlan, ahora: datetime, zona_horaria: str) -> datetime | None:
    """Desde cuándo se ve el historial (medianoche local); nulo si es completo. Los días
    cuentan hoy (30 días = igual que el periodo "30 días"); los meses son de calendario."""
    zona = ZoneInfo(zona_horaria)
    hoy = ahora.astimezone(zona)
    if definicion.historial_dias is not None:
        primer_dia = hoy.date() - timedelta(days=definicion.historial_dias - 1)
    elif definicion.historial_meses is not None:
        primer_dia = sumar_meses(
            datetime.combine(hoy.date(), time.min), -definicion.historial_meses
        ).date()
    else:
        return None
    return datetime.combine(primer_dia, time.min, tzinfo=zona).astimezone(UTC)


def limitar_desde(desde: datetime | None, limite: datetime | None) -> datetime | None:
    """El inicio más reciente entre el del periodo y el del historial del plan."""
    if limite is None:
        return desde
    if desde is None:
        return limite
    return max(desde, limite)
