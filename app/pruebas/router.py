"""Modo pruebas (temporal): solo se registra si `PRUEBAS_CORREO` está configurado.

Excepción deliberada a la regla de filtrar por `empresa_id`: la cuenta de pruebas ve
los viajes de todos los conductores. Se retira antes de pasar a producción.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import UsuarioActual
from app.auth.models import Usuario
from app.core.database import get_db
from app.pruebas import service
from app.pruebas.schemas import (
    DetallePruebas,
    FiltroEventos,
    PaginaRecorridosPruebas,
    ResumenPruebas,
    TesterPruebas,
    ValidacionEntrada,
    ValidacionSalida,
)


def _obtener_cuenta_pruebas(usuario: UsuarioActual) -> Usuario:
    return service.exigir_cuenta_pruebas(usuario)


# Todas las rutas exigen la cuenta de pruebas; cualquier otra recibe 403
router = APIRouter(
    prefix="/pruebas",
    tags=["Pruebas (temporal)"],
    dependencies=[Depends(_obtener_cuenta_pruebas)],
)

# Inicio del periodo con zona horaria; sin él, todos los viajes
Desde = Annotated[AwareDatetime | None, Query()]
UsuarioId = Annotated[int | None, Query(ge=1)]


@router.get("/acceso", status_code=status.HTTP_204_NO_CONTENT)
async def acceso():
    """204 si la sesión es la cuenta de pruebas; el panel lo usa para mostrar el modo pruebas."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/resumen", response_model=ResumenPruebas)
async def resumen(
    db: AsyncSession = Depends(get_db), desde: Desde = None, usuario_id: UsuarioId = None
):
    """KPIs de los viajes del periodo, de todos los conductores o de uno."""
    return await service.obtener_resumen(db, desde, usuario_id)


@router.get("/recorridos", response_model=PaginaRecorridosPruebas)
async def recorridos(
    db: AsyncSession = Depends(get_db),
    desde: Desde = None,
    usuario_id: UsuarioId = None,
    eventos: Annotated[FiltroEventos | None, Query()] = None,
    pagina: Annotated[int, Query(ge=1)] = 1,
    limite: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Recorridos finalizados y descartados, del más reciente al más antiguo, por páginas."""
    return await service.listar_recorridos(db, desde, usuario_id, eventos, pagina, limite)


@router.get(
    "/recorridos/exportar",
    response_class=Response,
    responses={status.HTTP_200_OK: {"content": {"text/csv": {}}}},
)
async def exportar(
    db: AsyncSession = Depends(get_db),
    desde: Desde = None,
    usuario_id: UsuarioId = None,
    eventos: Annotated[FiltroEventos | None, Query()] = None,
):
    """CSV con los recorridos que cumplen los filtros, sin paginar."""
    contenido = await service.exportar_recorridos(db, desde, usuario_id, eventos)
    return Response(
        content=contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="viajes-pruebas.csv"'},
    )


@router.get("/recorridos/{recorrido_id}", response_model=DetallePruebas)
async def detalle(recorrido_id: int, db: AsyncSession = Depends(get_db)):
    """Detalle de un recorrido finalizado o descartado, con la marca de cada evento."""
    return await service.obtener_detalle(db, recorrido_id)


@router.put("/eventos/{evento_id}/validacion", response_model=ValidacionSalida)
async def marcar_evento(
    evento_id: int, datos: ValidacionEntrada, db: AsyncSession = Depends(get_db)
):
    """Marca un evento como correcto o falso según lo que cuenta el tester."""
    return await service.marcar_evento(db, evento_id, datos.resultado)


@router.delete("/eventos/{evento_id}/validacion", status_code=status.HTTP_204_NO_CONTENT)
async def desmarcar_evento(evento_id: int, db: AsyncSession = Depends(get_db)):
    """Quita la marca de un evento."""
    await service.desmarcar_evento(db, evento_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/testers", response_model=list[TesterPruebas])
async def testers(db: AsyncSession = Depends(get_db), desde: Desde = None):
    """Conductores activos con los totales de sus viajes del periodo."""
    return await service.listar_testers(db, desde)
