from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import AdminEmpresaActual
from app.core.database import get_db
from app.flotas import service
from app.flotas.schemas import (
    ConductorCreado,
    ContraseniaTemporal,
    Direccion,
    OrdenConductores,
    PaginaConductores,
    Periodo,
    RegistroConductorFlotaEntrada,
    ResumenFlota,
)

router = APIRouter(prefix="/flotas", tags=["Flotas"])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/conductores", response_model=PaginaConductores)
async def listar_conductores(
    admin: AdminEmpresaActual,
    db: DB,
    periodo: Periodo = Periodo.todo,
    q: Annotated[str | None, Query(max_length=100)] = None,
    orden: OrdenConductores = OrdenConductores.ultimo_viaje,
    direccion: Direccion = Direccion.desc,
    pagina: Annotated[int, Query(ge=1)] = 1,
    limite: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Conductores activos de la empresa con DriveScore, viajes y último viaje del periodo."""
    return await service.listar_conductores(db, admin, periodo, q, orden, direccion, pagina, limite)


@router.get("/resumen", response_model=ResumenFlota)
async def obtener_resumen(admin: AdminEmpresaActual, db: DB, periodo: Periodo = Periodo.todo):
    return await service.obtener_resumen(db, admin, periodo)


@router.post("/conductores", response_model=ConductorCreado, status_code=status.HTTP_201_CREATED)
async def registrar_conductor(
    datos: RegistroConductorFlotaEntrada, admin: AdminEmpresaActual, db: DB
):
    """Registra un conductor en la empresa; la contraseña temporal solo se devuelve aquí."""
    return await service.registrar_conductor(db, admin, datos)


@router.post("/conductores/{conductor_id}/contrasenia-temporal", response_model=ContraseniaTemporal)
async def regenerar_contrasenia_temporal(conductor_id: int, admin: AdminEmpresaActual, db: DB):
    return await service.regenerar_contrasenia_temporal(db, admin, conductor_id)
