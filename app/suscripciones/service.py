from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.core.errores import ErrorDePlan
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RolUsuario, Usuario
from app.flotas.models import Empresa
from app.suscripciones.planes import (
    DIAS_AVISO_VENCIMIENTO,
    PLANES,
    DefinicionPlan,
    Funcion,
    PlanSuscripcion,
    dias_restantes,
    inicio_historial,
    plan_efectivo,
    sumar_meses,
)
from app.suscripciones.schemas import DetallePlan, FuncionesPlan, PlanActual

# Códigos de los 403 por plan (en `codigo`), para distinguirlos de los 403 por permisos
CODIGO_PLAN_LIMITE = "plan_limite"
CODIGO_PLAN_FUNCION = "plan_funcion"

_MENSAJES_FUNCION = {
    Funcion.exportar: "Exportar reportes no está incluido en tu plan {nombre}",
    Funcion.eventos_mapa_patrones: "El mapa y los patrones de eventos no están incluidos en tu plan {nombre}",
}


def definicion_de(empresa: Empresa, ahora: datetime | None = None) -> DefinicionPlan:
    """Definición del plan que rige hoy para la empresa (vencido = Gratis)."""
    ahora = ahora or datetime.now(UTC)
    return PLANES[plan_efectivo(empresa.plan, empresa.plan_vencimiento, ahora, empresa.zona_horaria)]


def limite_historial(empresa: Empresa, ahora: datetime) -> datetime | None:
    """Desde cuándo puede ver historial la empresa según su plan; nulo si es completo."""
    return inicio_historial(definicion_de(empresa, ahora), ahora, empresa.zona_horaria)


def exigir_funcion(empresa: Empresa, funcion: Funcion) -> None:
    """403 si el plan de la empresa no incluye la función."""
    definicion = definicion_de(empresa)
    if not getattr(definicion, funcion.value):
        raise ErrorDePlan(CODIGO_PLAN_FUNCION, _MENSAJES_FUNCION[funcion].format(nombre=definicion.nombre))


async def contar_conductores(db: AsyncSession, empresa_id: int) -> int:
    return (
        await db.scalar(
            select(func.count(Usuario.id)).where(
                Usuario.empresa_id == empresa_id,
                Usuario.rol == RolUsuario.conductor,
                Usuario.activo.is_(True),
            )
        )
    ) or 0


async def exigir_cupo_conductor(db: AsyncSession, empresa: Empresa) -> None:
    """403 si la empresa ya tiene tantos conductores activos como permite su plan. Bloquea
    la fila de la empresa para que dos altas simultáneas no pasen el límite."""
    await db.execute(select(Empresa.id).where(Empresa.id == empresa.id).with_for_update())
    definicion = definicion_de(empresa)
    limite = definicion.limite_conductores
    if limite is not None and await contar_conductores(db, empresa.id) >= limite:
        raise ErrorDePlan(
            CODIGO_PLAN_LIMITE,
            f"Llegaste al límite de {limite} conductores de tu plan {definicion.nombre}",
        )


async def _normalizar(db: AsyncSession, empresa: Empresa, ahora: datetime) -> None:
    """Un plan de pago vencido se guarda como Gratis (el efecto ya lo da `plan_efectivo`)."""
    if empresa.plan != plan_efectivo(empresa.plan, empresa.plan_vencimiento, ahora, empresa.zona_horaria):
        empresa.plan = PlanSuscripcion.gratis
        empresa.plan_inicio = None
        empresa.plan_vencimiento = None
        await db.commit()


def detalle_de(definicion: DefinicionPlan) -> DetallePlan:
    return DetallePlan(
        plan=definicion.plan,
        nombre=definicion.nombre,
        limite_conductores=definicion.limite_conductores,
        historial_dias=definicion.historial_dias,
        historial_meses=definicion.historial_meses,
        funciones=FuncionesPlan(
            exportar=definicion.exportar,
            eventos_mapa_patrones=definicion.eventos_mapa_patrones,
            tablero_completo=definicion.tablero_completo,
        ),
        precio_bs=definicion.precio_bs,
        duracion_meses=definicion.duracion_meses,
    )


def listar_planes() -> list[DetallePlan]:
    return [detalle_de(d) for d in PLANES.values()]


async def obtener_plan_actual(db: AsyncSession, admin: Usuario) -> PlanActual:
    empresa = admin.empresa
    ahora = datetime.now(UTC)
    await _normalizar(db, empresa, ahora)
    definicion = definicion_de(empresa, ahora)
    restantes = (
        dias_restantes(empresa.plan_vencimiento, ahora, empresa.zona_horaria)
        if empresa.plan_vencimiento is not None
        else None
    )
    por_vencer = restantes is not None and restantes <= DIAS_AVISO_VENCIMIENTO
    return PlanActual(
        detalle=detalle_de(definicion),
        estado="por_vencer" if por_vencer else "activo",
        fecha_inicio=empresa.plan_inicio,
        fecha_vencimiento=empresa.plan_vencimiento,
        dias_restantes=restantes,
        conductores_usados=await contar_conductores(db, empresa.id),
        aviso_vencimiento=por_vencer,
    )


async def cambiar_plan(db: AsyncSession, admin: Usuario, plan: PlanSuscripcion) -> PlanActual:
    """Cambio de plan con pago simulado (no hay cobro). Gratis es inmediato y sin
    reembolso; elegir el plan de pago vigente lo renueva un mes más desde su vencimiento;
    pasar a otro plan de pago empieza un mes nuevo desde hoy. Nunca borra datos."""
    empresa = admin.empresa
    ahora = datetime.now(UTC)
    await _normalizar(db, empresa, ahora)
    actual = empresa.plan

    if plan == PlanSuscripcion.gratis:
        if actual == PlanSuscripcion.gratis:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Tu empresa ya está en el plan Gratis")
        empresa.plan = plan
        empresa.plan_inicio = None
        empresa.plan_vencimiento = None
    else:
        meses = PLANES[plan].duracion_meses
        if plan == actual:
            empresa.plan_vencimiento = sumar_meses(empresa.plan_vencimiento, meses)
        else:
            empresa.plan = plan
            empresa.plan_inicio = ahora
            empresa.plan_vencimiento = sumar_meses(ahora, meses)
    await db.commit()
    return await obtener_plan_actual(db, admin)
