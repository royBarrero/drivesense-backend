from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import ConductorActual
from app.core.database import get_db
from app.puntaje import service
from app.puntaje.schemas import ComparativoSalida, HistoricoSalida

router = APIRouter(prefix="/puntaje", tags=["Puntaje"])


@router.get("/historico", response_model=HistoricoSalida)
async def historico(
    usuario: ConductorActual,
    # Inicio del periodo con zona horaria: la app manda la medianoche local
    desde: Annotated[AwareDatetime, Query()],
    db: AsyncSession = Depends(get_db),
):
    """Histórico del DriveScore del conductor desde `desde` hasta ahora (HU-17)."""
    return await service.obtener_historico(db, usuario, desde)


@router.get("/comparativo", response_model=ComparativoSalida)
async def comparativo(
    usuario: ConductorActual,
    periodo: Annotated[service.PeriodoComparativo, Query()],
    # Inicio del periodo actual con la zona del teléfono: lunes o día 1 a medianoche
    desde: Annotated[AwareDatetime, Query()],
    db: AsyncSession = Depends(get_db),
):
    """Periodo actual del conductor frente al anterior (HU-20)."""
    return await service.obtener_comparativo(db, usuario, periodo, desde)
