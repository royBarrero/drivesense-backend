from datetime import datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.telemetria.models import TipoEvento

Latitud = Annotated[float, Field(ge=-90, le=90)]
Longitud = Annotated[float, Field(ge=-180, le=180)]
Velocidad = Annotated[float, Field(ge=0, le=300)]


class EventoEntrada(BaseModel):
    """Evento de riesgo que envía la app al finalizar el recorrido (HU-15)."""

    model_config = ConfigDict(allow_inf_nan=False)

    tipo: TipoEvento
    fecha: AwareDatetime
    lat: Latitud
    lon: Longitud
    velocidad_kmh: Velocidad
    intensidad: Annotated[float, Field(ge=0)]
    # Solo en el exceso de velocidad
    duracion_s: Annotated[float | None, Field(ge=0)] = None
    velocidad_maxima_kmh: Velocidad | None = None

    @model_validator(mode="after")
    def validar_exceso(self) -> Self:
        con_datos = self.duracion_s is not None and self.velocidad_maxima_kmh is not None
        sin_datos = self.duracion_s is None and self.velocidad_maxima_kmh is None
        if self.tipo == TipoEvento.exceso_velocidad and not con_datos:
            raise ValueError("El exceso de velocidad requiere su duración y velocidad máxima")
        if self.tipo != TipoEvento.exceso_velocidad and not sin_datos:
            raise ValueError("Solo el exceso de velocidad lleva duración y velocidad máxima")
        return self


class EventoSalida(BaseModel):
    """Evento de riesgo de un recorrido en su detalle (HU-29): dónde y cuándo ocurrió."""

    model_config = ConfigDict(from_attributes=True)

    tipo: TipoEvento
    fecha: datetime
    lat: float
    lon: float
    velocidad_kmh: float
    intensidad: float
    # Solo en el exceso de velocidad
    duracion_s: float | None
    velocidad_maxima_kmh: float | None
