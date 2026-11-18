from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import AdminEmpresaActual
from app.core.database import get_db
from app.suscripciones import service
from app.suscripciones.schemas import CambioPlanEntrada, DetallePlan, PlanActual

router = APIRouter(prefix="/suscripciones", tags=["Suscripciones"])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/plan", response_model=PlanActual)
async def obtener_plan(admin: AdminEmpresaActual, db: DB):
    """Plan actual de la empresa, su vencimiento y los conductores usados (HU-28)."""
    return await service.obtener_plan_actual(db, admin)


@router.get("/planes", response_model=list[DetallePlan])
async def listar_planes(admin: AdminEmpresaActual):
    """Catálogo de planes para compararlos."""
    return service.listar_planes()


@router.post("/plan", response_model=PlanActual)
async def cambiar_plan(datos: CambioPlanEntrada, admin: AdminEmpresaActual, db: DB):
    """Cambia el plan de la empresa. El pago es simulado: no hay cobro real."""
    return await service.cambiar_plan(db, admin, datos.plan)
