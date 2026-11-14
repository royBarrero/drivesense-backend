import enum
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.auth.schemas import EmailNormalizado, Telefono
from app.puntaje.service import Calificacion


class Periodo(str, enum.Enum):
    """Periodo del listado: los últimos N días contando hoy, según la zona de la empresa."""

    siete_dias = "7d"
    treinta_dias = "30d"
    todo = "todo"


class OrdenConductores(str, enum.Enum):
    drivescore = "drivescore"
    viajes = "viajes"
    ultimo_viaje = "ultimo_viaje"


class Direccion(str, enum.Enum):
    asc = "asc"
    desc = "desc"


class ConductorFlota(BaseModel):
    id: int
    nombre: str
    email: str
    # Promedio ponderado por distancia del periodo; nulo si no tiene viajes con puntaje
    drivescore: int | None
    calificacion: Calificacion | None
    viajes: int
    ultimo_viaje: datetime | None


class PaginaConductores(BaseModel):
    conductores: list[ConductorFlota]
    total: int


class ResumenFlota(BaseModel):
    # Conductores activos de la empresa (no depende del periodo)
    conductores: int
    drivescore_promedio: int | None
    calificacion: Calificacion | None
    # Eventos de riesgo de los viajes finalizados del periodo
    eventos: int


class RegistroConductorFlotaEntrada(BaseModel):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: EmailNormalizado
    telefono: Telefono


class ConductorCreado(BaseModel):
    id: int
    nombre: str
    email: str
    telefono: str
    # Se devuelve una sola vez: en la base solo queda el hash
    contrasenia_temporal: str


class ContraseniaTemporal(BaseModel):
    contrasenia_temporal: str
