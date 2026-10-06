"""Modo pruebas (temporal): esquemas. Se retira junto con todo el módulo."""

import enum
from datetime import datetime

from pydantic import BaseModel, computed_field

from app.pruebas.models import ResultadoValidacion
from app.puntaje.service import Calificacion, calificar
from app.recorridos.models import EstadoRecorrido
from app.recorridos.schemas import RecorridoDetalle
from app.telemetria.models import TipoEvento
from app.telemetria.schemas import EventoSalida


class FiltroEventos(str, enum.Enum):
    """Recorridos con al menos un evento, o con puntaje y ninguno."""

    con = "con"
    sin = "sin"


class ResumenPruebas(BaseModel):
    """KPIs del periodo: los totales y el DriveScore cuentan solo los finalizados."""

    # Conductores distintos con viajes finalizados en el periodo
    testers: int
    # Conductores activos registrados, tengan o no viajes
    testers_registrados: int
    viajes: int
    # Viajes finalizados del periodo anterior de igual duración; nulo sin `desde`
    viajes_anterior: int | None
    descartados: int
    distancia_m: float
    # Ponderado por distancia entre los viajes con puntaje; nulo si no hay
    drivescore_promedio: int | None
    calificacion: Calificacion | None
    eventos: int
    # Sobre la distancia de los viajes con puntaje (de los anteriores a HU-15 no se
    # guardaron eventos); nulo si no hay ninguno
    eventos_por_10km: float | None


class ConductorPruebas(BaseModel):
    id: int
    nombre: str
    email: str


class Dispositivo(BaseModel):
    """Teléfono y versión de la app; nulos en los viajes de una app que no los envía."""

    dispositivo_modelo: str | None = None
    dispositivo_android: str | None = None
    version_app: str | None = None


class RecorridoPruebas(Dispositivo):
    """Recorrido finalizado o descartado de cualquier conductor."""

    id: int
    estado: EstadoRecorrido
    conductor: ConductorPruebas
    fecha_inicio: datetime
    fecha_fin: datetime
    distancia_m: float
    duracion_s: int
    velocidad_maxima_kmh: float
    velocidad_promedio_kmh: float
    # Nulo en los descartados y en los finalizados antes de HU-15
    drivescore: int | None
    # Los cuatro tipos; nulo si el recorrido no tiene puntaje (no se guardaron eventos)
    eventos_por_tipo: dict[TipoEvento, int] | None = None

    @computed_field
    @property
    def calificacion(self) -> Calificacion | None:
        return calificar(self.drivescore) if self.drivescore is not None else None


class PaginaRecorridosPruebas(BaseModel):
    recorridos: list[RecorridoPruebas]
    # Recorridos que cumplen los filtros, en todas las páginas
    total: int


class EventoPruebas(EventoSalida):
    id: int
    validacion: ResultadoValidacion | None = None


class DetallePruebas(RecorridoDetalle, Dispositivo):
    """Detalle de un recorrido de cualquier conductor, con la marca de cada evento."""

    estado: EstadoRecorrido
    conductor: ConductorPruebas
    eventos: list[EventoPruebas] | None = None

    @computed_field
    @property
    def calificacion(self) -> Calificacion | None:
        return calificar(self.drivescore) if self.drivescore is not None else None


class ValidacionEntrada(BaseModel):
    resultado: ResultadoValidacion


class ValidacionSalida(BaseModel):
    evento_id: int
    resultado: ResultadoValidacion


class TesterPruebas(BaseModel):
    """Totales de un conductor en el periodo (solo sus viajes finalizados)."""

    id: int
    nombre: str
    email: str
    viajes: int
    distancia_m: float
    drivescore_promedio: int | None
    eventos: int
    # Como en el resumen: solo la distancia de los viajes con puntaje
    eventos_por_10km: float | None
    # Inicio de su último viaje finalizado del periodo
    ultimo_viaje: datetime | None

    @computed_field
    @property
    def calificacion(self) -> Calificacion | None:
        if self.drivescore_promedio is None:
            return None
        return calificar(self.drivescore_promedio)
