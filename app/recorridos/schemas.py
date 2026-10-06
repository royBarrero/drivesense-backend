from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.recorridos.models import EstadoRecorrido
from app.telemetria.models import TipoEvento
from app.telemetria.schemas import EventoEntrada, EventoSalida

# Margen para diferencias entre el reloj del teléfono y el del servidor
TOLERANCIA_RELOJ = timedelta(minutes=2)

Latitud = Annotated[float, Field(ge=-90, le=90)]
Longitud = Annotated[float, Field(ge=-180, le=180)]
Velocidad = Annotated[float, Field(ge=0, le=300)]
ModeloDispositivo = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
# Versión de Android o de la app, p. ej. "14" o "1.0.3"
Version = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]

# Un punto cada ~5 s o ~20 m: alcanza para ~55 h o ~200 km; la app diezma la ruta
# antes de pasarse
MAXIMO_PUNTOS_RUTA = 10_000

# Muy por encima de un viaje real (un evento cada pocos segundos durante horas):
# solo frena envíos absurdos
MAXIMO_EVENTOS = 2_000


class PuntoRuta(BaseModel):
    """Punto de la ruta GPS del recorrido (HU-08)."""

    model_config = ConfigDict(allow_inf_nan=False)

    lat: Latitud
    lon: Longitud
    fecha: AwareDatetime
    velocidad_kmh: Velocidad


class IniciarRecorridoEntrada(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    lat_inicio: Latitud
    lon_inicio: Longitud
    # Opcionales: una versión anterior de la app no los envía
    dispositivo_modelo: ModeloDispositivo | None = None
    dispositivo_android: Version | None = None
    version_app: Version | None = None


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
    # Opcional: un resumen sin ruta (p. ej. de una versión anterior de la app) se acepta
    ruta: Annotated[list[PuntoRuta] | None, Field(max_length=MAXIMO_PUNTOS_RUTA)] = None
    # Eventos de riesgo (HU-15); una app sin eventos (versión anterior) no los envía
    eventos: Annotated[list[EventoEntrada], Field(max_length=MAXIMO_EVENTOS)] = []

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

    @field_validator("ruta")
    @classmethod
    def validar_ruta(cls, valor: list[PuntoRuta] | None) -> list[PuntoRuta] | None:
        if valor is not None and any(
            siguiente.fecha < anterior.fecha for anterior, siguiente in zip(valor, valor[1:])
        ):
            raise ValueError("Los puntos de la ruta deben estar en orden cronológico")
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
    # HU-15: nulos en curso y en recorridos descartados
    drivescore: int | None
    puntaje_frenadas: int | None
    puntaje_aceleraciones: int | None
    puntaje_giros: int | None
    puntaje_velocidad: int | None
    dispositivo_modelo: str | None
    dispositivo_android: str | None
    version_app: str | None


class RecorridoHistorial(BaseModel):
    """Recorrido finalizado del historial (HU-06): siempre tiene fin y métricas."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fecha_inicio: datetime
    fecha_fin: datetime
    distancia_m: float
    duracion_s: int
    velocidad_maxima_kmh: float
    velocidad_promedio_kmh: float
    # HU-15; nulo en los finalizados antes de existir. El listado no trae las categorías
    drivescore: int | None


class RecorridoDetalle(RecorridoHistorial):
    """Detalle de un recorrido finalizado: incluye la ruta (el historial no, para ser
    liviano), el puntaje (HU-15), cuántos eventos hubo de cada tipo (HU-16) y la lista
    de eventos con su posición (HU-29). Puntaje, conteo y eventos son nulos en los
    finalizados antes de HU-15."""

    ruta: list[PuntoRuta] | None
    puntaje_frenadas: int | None
    puntaje_aceleraciones: int | None
    puntaje_giros: int | None
    puntaje_velocidad: int | None
    # Los cuatro tipos, con 0 si no hubo
    eventos_por_tipo: dict[TipoEvento, int] | None = None
    # En orden cronológico, para el mapa del viaje (HU-29)
    eventos: list[EventoSalida] | None = None


class ResumenPeriodo(BaseModel):
    """Totales de los recorridos finalizados del periodo consultado."""

    viajes: int
    distancia_m: float
    duracion_s: int


class PaginaHistorial(BaseModel):
    recorridos: list[RecorridoHistorial]
    # Cursor de la página siguiente (valor de `antes_de`); nulo si no hay más
    siguiente: int | None
    # Solo en la primera página (sin `antes_de`)
    resumen: ResumenPeriodo | None
