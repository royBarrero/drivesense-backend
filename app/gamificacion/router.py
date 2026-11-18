from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import ConductorActual
from app.core.database import get_db
from app.gamificacion import service
from app.gamificacion.schemas import DesafiosSemanaSalida, ProgresoSalida

router = APIRouter(prefix="/gamificacion", tags=["Gamificación"])


@router.get("/progreso", response_model=ProgresoSalida)
async def progreso(usuario: ConductorActual, db: AsyncSession = Depends(get_db)):
    """Nivel, puntos y logros del conductor (HU-21)."""
    return await service.obtener_progreso(db, usuario)


@router.get("/desafios", response_model=DesafiosSemanaSalida)
async def desafios(
    usuario: ConductorActual,
    # Lunes 00:00 de la semana en curso con la zona del teléfono
    desde: Annotated[AwareDatetime, Query()],
    db: AsyncSession = Depends(get_db),
):
    """Los 3 desafíos de la semana con su avance; se generan en la primera consulta (HU-22)."""
    return await service.obtener_desafios(db, usuario, desde)
