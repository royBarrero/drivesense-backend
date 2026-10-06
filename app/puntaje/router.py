from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import ConductorActual
from app.core.database import get_db
from app.puntaje import service
from app.puntaje.schemas import HistoricoSalida

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
