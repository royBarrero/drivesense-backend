from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import StringConstraints

from app.auth.dependencias import ConductorActual
from app.navegacion import service
from app.navegacion.schemas import Coordenada, LugaresSalida, RutaSalida

router = APIRouter(prefix="/navegacion", tags=["Navegación"])

Latitud = Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)]
Longitud = Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)]
TextoBusqueda = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100), Query()
]


@router.get("/ruta", response_model=RutaSalida)
async def ruta(
    usuario: ConductorActual,
    origen_lat: Latitud,
    origen_lon: Longitud,
    destino_lat: Latitud,
    destino_lon: Longitud,
):
    """Ruta en auto hasta el destino, con el trazado y los pasos en español (HU-30)."""
    return await service.obtener_ruta(
        Coordenada(lat=origen_lat, lon=origen_lon), Coordenada(lat=destino_lat, lon=destino_lon)
    )


@router.get("/lugares", response_model=LugaresSalida)
async def lugares(usuario: ConductorActual, q: TextoBusqueda):
    """Lugares de Bolivia que coinciden con el texto, primero los de Santa Cruz (HU-30).

    Sin autocompletar: la app lo llama solo cuando el conductor envía la búsqueda.
    """
    return await service.buscar_lugares(q)
