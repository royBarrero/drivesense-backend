from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.gamificacion.models import CodigoDesafio, CodigoLogro, TipoDesafio
from app.gamificacion.service import TipoProgreso


class NivelSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    numero: int
    nombre: str
    # Puntos acumulados para alcanzarlo
    puntos: int


class LogroSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    codigo: CodigoLogro
    nombre: str
    descripcion: str


class EstadoLogroSalida(LogroSalida):
    # Qué cuenta el progreso: viaje (0 o 1), viajes, km o racha (la actual)
    tipo: TipoProgreso
    meta: int
    progreso: int
    desbloqueado: bool
    fecha_desbloqueo: datetime | None


class ProgresoSalida(BaseModel):
    """Nivel, puntos y logros del conductor (HU-21)."""

    model_config = ConfigDict(from_attributes=True)

    puntos: int
    nivel: NivelSalida
    # Nulo en el nivel máximo
    siguiente_nivel: NivelSalida | None
    # Los 9 logros en el orden del catálogo
    logros: list[EstadoLogroSalida]


class DesafioCompletadoSalida(BaseModel):
    """Un desafío completado por el viaje recién finalizado (HU-23)."""

    model_config = ConfigDict(from_attributes=True)

    codigo: CodigoDesafio
    tipo: TipoDesafio
    nombre: str
    puntos: int
    # Solo en el punto débil: frenadas | aceleraciones | giros | velocidad
    categoria: str | None


class RecompensaViajeSalida(BaseModel):
    """Lo que ganó el viaje recién finalizado (respuesta del PATCH)."""

    model_config = ConfigDict(from_attributes=True)

    puntos: int
    subio_de_nivel: bool
    # El nivel después del viaje
    nivel: NivelSalida
    # Solo los que desbloqueó este viaje
    logros_nuevos: list[LogroSalida]
    # Desafíos semanales que completó este viaje, con los puntos que dieron (HU-23).
    # `nivel` y `subio_de_nivel` ya cuentan esos puntos y el bono
    desafios_completados: list[DesafioCompletadoSalida]
    # Puntos del bono por completar los 3 desafíos de la semana, si lo dio este viaje
    bono_semanal: int | None


class DesafioSalida(BaseModel):
    """Un desafío de la semana con su avance (HU-22)."""

    model_config = ConfigDict(from_attributes=True)

    codigo: CodigoDesafio
    tipo: TipoDesafio
    nombre: str
    descripcion: str
    # Qué cuenta el progreso: viajes, km, viaje (0 o 1), racha (la actual) o promedio
    tipo_progreso: TipoProgreso
    meta: int
    progreso: int
    # Una vez cumplido no se pierde en la semana
    completado: bool
    # Recompensa al completarlo (se entrega en la HU-23)
    puntos: int
    # Solo en el punto débil: frenadas | aceleraciones | giros | velocidad
    categoria: str | None
    # Solo en "Promedio muy bueno": promedio de la semana (nulo sin viajes),
    # viajes que lleva y los que pide
    promedio: int | None
    viajes: int | None
    viajes_minimos: int | None


class BonoSemanaSalida(BaseModel):
    """Bono por completar los 3 desafíos de la semana (HU-23)."""

    model_config = ConfigDict(from_attributes=True)

    puntos: int
    recibido: bool


class DesafiosSemanaSalida(BaseModel):
    """Los 3 desafíos de la semana en curso (constancia, calidad, punto débil)."""

    model_config = ConfigDict(from_attributes=True)

    inicio: datetime
    fin: datetime
    desafios: list[DesafioSalida]
    bono: BonoSemanaSalida
