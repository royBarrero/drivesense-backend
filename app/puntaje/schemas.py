from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.puntaje.service import Agrupacion, Calificacion


class PuntoHistoricoSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # En los grupos (día, semana), el inicio del tramo
    fecha: datetime
    drivescore: int
    viajes: int


class PromedioCategoriaSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    promedio: int
    # Frente al periodo anterior; nula si ese periodo no tiene viajes
    diferencia: int | None


class ViajeDestacado(BaseModel):
    """Mejor o peor viaje del periodo; `id` abre su detalle."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fecha_inicio: datetime
    distancia_m: float
    drivescore: int


class HistoricoSalida(BaseModel):
    """Histórico del DriveScore del conductor (HU-17). Promedios ponderados por distancia."""

    model_config = ConfigDict(from_attributes=True)

    desde: datetime
    hasta: datetime
    viajes: int
    # Todos los viajes con puntaje del conductor, de cualquier fecha
    viajes_totales: int
    promedio_total: int | None
    # Del periodo; nulos si no tiene viajes
    promedio: int | None
    calificacion: Calificacion | None
    # Frente al periodo anterior de igual duración; nula si ese periodo no tiene viajes
    tendencia: int | None
    agrupacion: Agrupacion
    puntos: list[PuntoHistoricoSalida]
    # Claves: frenadas, aceleraciones, giros, velocidad
    categorias: dict[str, PromedioCategoriaSalida] | None
    mejor: ViajeDestacado | None
    # Nulo con menos de 2 viajes
    peor: ViajeDestacado | None
