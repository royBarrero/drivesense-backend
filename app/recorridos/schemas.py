from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.recorridos.models import EstadoRecorrido

# Margen para diferencias entre el reloj del teléfono y el del servidor
TOLERANCIA_RELOJ = timedelta(minutes=2)

Latitud = Annotated[float, Field(ge=-90, le=90)]
Longitud = Annotated[float, Field(ge=-180, le=180)]
Velocidad = Annotated[float, Field(ge=0, le=300)]


class IniciarRecorridoEntrada(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    lat_inicio: Latitud
    lon_inicio: Longitud


class FinalizarRecorridoEntrada(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    # La envía la app: el resumen puede llegar tarde si no había conexión
    fecha_fin: AwareDatetime
    distancia_m: Annotated[float, Field(ge=0)]
    duracion_s: Annotated[int, Field(ge=0)]
    # Declarada antes que la promedio para poder compararlas
    velocidad_maxima_kmh: Velocidad
    velocidad_promedio_kmh: Velocidad
    lat_fin: Latitud
    lon_fin: Longitud

    @field_validator("fecha_fin")
    @classmethod
    def validar_fecha_fin(cls, valor: datetime) -> datetime:
        if valor > datetime.now(UTC) + TOLERANCIA_RELOJ:
            raise ValueError("La fecha de fin no puede estar en el futuro")
        return valor

    @field_validator("velocidad_promedio_kmh")
    @classmethod
    def validar_velocidad_promedio(cls, valor: float, info: ValidationInfo) -> float:
        # Si la máxima no pasó su propia validación, no está en info.data
        maxima = info.data.get("velocidad_maxima_kmh")
        if maxima is not None and valor > maxima:
            raise ValueError("La velocidad promedio no puede ser mayor que la máxima")
        return valor


class RecorridoSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int
    empresa_id: int | None
    estado: EstadoRecorrido
    fecha_inicio: datetime
    # Nulos mientras el recorrido está en curso
    fecha_fin: datetime | None
    distancia_m: float | None
    duracion_s: int | None
    velocidad_maxima_kmh: float | None
    velocidad_promedio_kmh: float | None
    lat_inicio: float
    lon_inicio: float
    lat_fin: float | None
    lon_fin: float | None
    fecha_creacion: datetime
