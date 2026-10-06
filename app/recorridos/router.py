from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import UsuarioActual
from app.auth.models import Usuario
from app.core.database import get_db
from app.recorridos import service
from app.recorridos.schemas import (
    FinalizarRecorridoEntrada,
    IniciarRecorridoEntrada,
    PaginaHistorial,
    RecorridoDetalle,
    RecorridoSalida,
)


def _obtener_conductor(usuario: UsuarioActual) -> Usuario:
    return service.exigir_conductor(usuario)


# Usuario autenticado con rol conductor; 403 para cualquier otro rol
ConductorActual = Annotated[Usuario, Depends(_obtener_conductor)]

router = APIRouter(prefix="/recorridos", tags=["Recorridos"])


@router.post("", response_model=RecorridoSalida, status_code=status.HTTP_201_CREATED)
async def iniciar_recorrido(
    datos: IniciarRecorridoEntrada, usuario: ConductorActual, db: AsyncSession = Depends(get_db)
):
    """Inicia un recorrido del conductor autenticado (HU-04)."""
    return await service.iniciar_recorrido(db, usuario, datos)


@router.get(
    "/activo",
    response_model=RecorridoSalida,
    responses={status.HTTP_204_NO_CONTENT: {"description": "No hay un recorrido en curso"}},
)
async def recorrido_activo(usuario: ConductorActual, db: AsyncSession = Depends(get_db)):
    """Recorrido en curso del conductor; la app lo consulta al abrirse para retomarlo (HU-04)."""
    recorrido = await service.obtener_recorrido_activo(db, usuario)
    if recorrido is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return recorrido


@router.get("", response_model=PaginaHistorial)
async def historial(
    usuario: ConductorActual,
    db: AsyncSession = Depends(get_db),
    # Inicio del periodo con zona horaria: "esta semana" depende de la zona del teléfono
    desde: Annotated[AwareDatetime | None, Query()] = None,
    antes_de: Annotated[int | None, Query(ge=1)] = None,
    limite: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Recorridos finalizados del conductor, paginados por cursor (HU-06).

    La primera página (sin `antes_de`) incluye el resumen del periodo.
    """
    return await service.listar_historial(db, usuario, desde, antes_de, limite)


@router.get("/{recorrido_id}", response_model=RecorridoDetalle)
async def detalle_recorrido(
    recorrido_id: int, usuario: ConductorActual, db: AsyncSession = Depends(get_db)
):
    """Detalle de un recorrido finalizado del conductor, con su ruta (HU-06, HU-08)."""
    return await service.obtener_recorrido_finalizado(db, usuario, recorrido_id)


@router.patch("/{recorrido_id}/finalizar", response_model=RecorridoSalida)
async def finalizar_recorrido(
    recorrido_id: int,
    datos: FinalizarRecorridoEntrada,
    usuario: ConductorActual,
    db: AsyncSession = Depends(get_db),
):
    """Finaliza un recorrido con su resumen y su ruta; queda finalizado o descartado (HU-05, HU-08)."""
    return await service.finalizar_recorrido(db, usuario, recorrido_id, datos)
