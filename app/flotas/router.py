from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencias import AdminEmpresaActual
from app.core.database import get_db
from app.flotas import service
from app.flotas.reporte import FormatoReporte, PeriodoReporte, reporte_conductor, reporte_flota
from app.flotas.reporte_csv import csv_conductor, csv_flota
from app.flotas.reporte_pdf import pdf_conductor, pdf_flota
from app.flotas.schemas import (
    ConductorCreado,
    ContraseniaTemporal,
    Direccion,
    MapaEventos,
    OrdenConductores,
    PaginaConductores,
    PaginaEventos,
    PerfilConductor,
    Periodo,
    RegistroConductorFlotaEntrada,
    ResumenEventos,
    ResumenFlota,
    TableroFlota,
    ViajeConductor,
)
from app.flotas.tablero import obtener_tablero
from app.telemetria.models import TipoEvento
from app.recorridos.schemas import RecorridoDetalle

router = APIRouter(prefix="/flotas", tags=["Flotas"])

DB = Annotated[AsyncSession, Depends(get_db)]


def _archivo(contenido: bytes | str, nombre: str, formato: FormatoReporte) -> Response:
    tipo = "application/pdf" if formato == FormatoReporte.pdf else "text/csv; charset=utf-8"
    return Response(
        content=contenido,
        media_type=tipo,
        headers={"Content-Disposition": f'attachment; filename="{nombre}.{formato.value}"'},
    )


@router.get(
    "/reporte",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "text/csv": {}}}},
)
async def exportar_reporte_flota(
    admin: AdminEmpresaActual,
    db: DB,
    periodo: PeriodoReporte = PeriodoReporte.todo,
    desde: date | None = None,
    hasta: date | None = None,
    formato: FormatoReporte = FormatoReporte.pdf,
):
    """Reporte de desempeño de toda la flota en PDF o CSV (HU-27). 409 sin viajes."""
    reporte = await reporte_flota(db, admin, periodo, desde, hasta)
    if formato == FormatoReporte.pdf:
        return _archivo(pdf_flota(reporte), reporte.archivo, formato)
    return _archivo(csv_flota(reporte), reporte.archivo, formato)


@router.get(
    "/conductores/{conductor_id}/reporte",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "text/csv": {}}}},
)
async def exportar_reporte_conductor(
    conductor_id: int,
    admin: AdminEmpresaActual,
    db: DB,
    periodo: PeriodoReporte = PeriodoReporte.todo,
    desde: date | None = None,
    hasta: date | None = None,
    formato: FormatoReporte = FormatoReporte.pdf,
):
    """Reporte de desempeño de un conductor de la empresa en PDF o CSV (HU-27)."""
    reporte = await reporte_conductor(db, admin, conductor_id, periodo, desde, hasta)
    if formato == FormatoReporte.pdf:
        return _archivo(pdf_conductor(reporte), reporte.archivo, formato)
    return _archivo(csv_conductor(reporte), reporte.archivo, formato)


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


@router.get("/tablero", response_model=TableroFlota)
async def obtener_tablero_flota(
    admin: AdminEmpresaActual, db: DB, periodo: Periodo = Periodo.treinta_dias
):
    """Indicadores de toda la flota para la pantalla Reportes del panel."""
    return await obtener_tablero(db, admin, periodo)


@router.post("/conductores", response_model=ConductorCreado, status_code=status.HTTP_201_CREATED)
async def registrar_conductor(
    datos: RegistroConductorFlotaEntrada, admin: AdminEmpresaActual, db: DB
):
    """Registra un conductor en la empresa; la contraseña temporal solo se devuelve aquí."""
    return await service.registrar_conductor(db, admin, datos)


@router.post("/conductores/{conductor_id}/contrasenia-temporal", response_model=ContraseniaTemporal)
async def regenerar_contrasenia_temporal(conductor_id: int, admin: AdminEmpresaActual, db: DB):
    return await service.regenerar_contrasenia_temporal(db, admin, conductor_id)


@router.get("/conductores/{conductor_id}", response_model=PerfilConductor)
async def obtener_perfil(
    conductor_id: int, admin: AdminEmpresaActual, db: DB, periodo: Periodo = Periodo.todo
):
    """Desempeno de un conductor de la empresa en el periodo (HU-25)."""
    return await service.obtener_perfil(db, admin, conductor_id, periodo)


@router.get("/conductores/{conductor_id}/recorridos", response_model=list[ViajeConductor])
async def listar_viajes_recientes(
    conductor_id: int,
    admin: AdminEmpresaActual,
    db: DB,
    periodo: Periodo = Periodo.todo,
    limite: Annotated[int, Query(ge=1, le=50)] = service.VIAJES_RECIENTES,
):
    return await service.listar_viajes_recientes(db, admin, conductor_id, periodo, limite)


@router.get(
    "/conductores/{conductor_id}/recorridos/{recorrido_id}", response_model=RecorridoDetalle
)
async def obtener_detalle_viaje(
    conductor_id: int, recorrido_id: int, admin: AdminEmpresaActual, db: DB
):
    return await service.obtener_detalle_viaje(db, admin, conductor_id, recorrido_id)


@router.get("/conductores/{conductor_id}/eventos", response_model=PaginaEventos)
async def listar_eventos(
    conductor_id: int,
    admin: AdminEmpresaActual,
    db: DB,
    periodo: Periodo = Periodo.todo,
    tipo: TipoEvento | None = None,
    pagina: Annotated[int, Query(ge=1)] = 1,
    limite: Annotated[int, Query(ge=1, le=50)] = service.EVENTOS_POR_PAGINA,
):
    """Historial de eventos de riesgo del conductor, del mas reciente al mas antiguo (HU-26)."""
    return await service.listar_eventos(db, admin, conductor_id, periodo, tipo, pagina, limite)


@router.get("/conductores/{conductor_id}/eventos/resumen", response_model=ResumenEventos)
async def resumen_eventos(
    conductor_id: int, admin: AdminEmpresaActual, db: DB, periodo: Periodo = Periodo.todo
):
    return await service.resumen_eventos(db, admin, conductor_id, periodo)


@router.get("/conductores/{conductor_id}/eventos/mapa", response_model=MapaEventos)
async def mapa_eventos(
    conductor_id: int,
    admin: AdminEmpresaActual,
    db: DB,
    periodo: Periodo = Periodo.todo,
    tipo: TipoEvento | None = None,
):
    return await service.mapa_eventos(db, admin, conductor_id, periodo, tipo)
