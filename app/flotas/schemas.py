import enum
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.auth.schemas import EmailNormalizado, Telefono
from app.puntaje.service import Agrupacion, Calificacion
from app.recorridos.schemas import RecorridoHistorial
from app.telemetria.models import TipoEvento


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


# --- Perfil de un conductor (HU-25) -------------------------------------------


class NivelRiesgo(str, enum.Enum):
    """Riesgoso = alto, regular = medio, muy bueno y excelente = bajo."""

    alto = "alto"
    medio = "medio"
    bajo = "bajo"


class BaseTendencia(str, enum.Enum):
    """Con qué se compara el periodo para calcular la tendencia."""

    # 7 y 30 días: el periodo anterior de igual duracion
    periodo_anterior = "periodo_anterior"
    # Todo: la segunda mitad del periodo frente a la primera
    mitades = "mitades"


class ConductorPerfil(BaseModel):
    id: int
    nombre: str
    email: str


class CategoriaPerfil(BaseModel):
    promedio: int
    # Frente al periodo anterior de igual duración; nula en "todo" o sin viajes antes
    diferencia: int | None


class PuntoEvolucion(BaseModel):
    # Viaje: su inicio; día o semana: el inicio del tramo
    fecha: datetime
    drivescore: int
    viajes: int


class PerfilConductor(BaseModel):
    conductor: ConductorPerfil
    # Viajes finalizados con DriveScore del periodo (los anteriores a HU-15 no cuentan)
    viajes: int
    distancia_m: float
    drivescore: int | None
    calificacion: Calificacion | None
    nivel_riesgo: NivelRiesgo | None
    # Promedio actual − el de la base de comparación (`tendencia_base`); nula si esa
    # base no tiene viajes
    tendencia: int | None
    tendencia_base: BaseTendencia
    # velocidad, frenadas, aceleraciones, giros; nula sin viajes
    categorias: dict[str, CategoriaPerfil] | None
    # Tipo de la categoría que más resta; nulo si no hubo eventos
    punto_debil: TipoEvento | None
    eventos_por_tipo: dict[TipoEvento, int]
    eventos: int
    eventos_por_10km: float | None
    agrupacion: Agrupacion
    evolucion: list[PuntoEvolucion]


class ViajeConductor(RecorridoHistorial):
    calificacion: Calificacion | None = None


# --- Historial de eventos de riesgo de un conductor (HU-26) --------------------


class FranjaHoraria(str, enum.Enum):
    manana = "manana"
    tarde = "tarde"
    noche = "noche"


class EventoConductor(BaseModel):
    id: int
    recorrido_id: int
    tipo: TipoEvento
    # Instante del evento y, en la zona horaria de la empresa, su dia y su hora
    fecha: datetime
    dia: date
    hora: str
    intensidad: float
    velocidad_kmh: float
    # Solo en el exceso de velocidad
    duracion_s: float | None
    velocidad_maxima_kmh: float | None
    lat: float
    lon: float


class PaginaEventos(BaseModel):
    eventos: list[EventoConductor]
    total: int


class PatronesEventos(BaseModel):
    # Eventos que hacen falta en el periodo para hablar de un patron
    minimo: int
    hay_patron: bool
    # Nulos mientras no haya patron
    tipo_mas_frecuente: TipoEvento | None
    dia_mas_frecuente: int | None
    franja_mas_frecuente: FranjaHoraria | None
    # Eventos por dia de la semana (lunes a domingo; `dia_mas_frecuente` va de 1 a 7)
    por_dia: list[int]
    por_franja: dict[FranjaHoraria, int]


class ResumenEventos(BaseModel):
    total: int
    por_tipo: dict[TipoEvento, int]
    patrones: PatronesEventos


class PuntoEvento(BaseModel):
    id: int
    recorrido_id: int
    tipo: TipoEvento
    lat: float
    lon: float
    fecha: datetime


class ZonaRepetida(BaseModel):
    """Lugar donde se repiten eventos del mismo tipo."""

    tipo: TipoEvento
    lat: float
    lon: float
    radio_m: float
    eventos: int


class MapaEventos(BaseModel):
    puntos: list[PuntoEvento]
    zonas: list[ZonaRepetida]
    # Hay mas eventos en el periodo que los `puntos` devueltos (los mas recientes)
    truncado: bool


# --- Tablero de la flota (pantalla Reportes del panel) --------------------------


class ItemRanking(BaseModel):
    id: int
    nombre: str
    drivescore: int
    calificacion: Calificacion
    distancia_m: float
    eventos: int
    # Eventos por cada 10 km del periodo (un decimal)
    eventos_por_10km: float | None


class ConteoCalificaciones(BaseModel):
    excelente: int
    muy_bueno: int
    regular: int
    riesgoso: int
    # Conductores activos sin viajes con puntaje en el periodo
    sin_viajes: int


class EvolucionFlota(BaseModel):
    # `dia` en 7d y 30d, `semana` en "todo"; nunca por viaje (mezclaria conductores)
    agrupacion: Agrupacion
    puntos: list[PuntoEvolucion]


class TableroFlota(BaseModel):
    # False cuando el plan solo incluye las tarjetas: rankings, distribución, eventos por
    # tipo y evolución van vacíos
    completo: bool
    # Conductores activos de la empresa (no depende del periodo)
    conductores: int
    conductores_con_viajes: int
    viajes: int
    distancia_m: float
    drivescore: int | None
    calificacion: Calificacion | None
    eventos: int
    eventos_por_10km: float | None
    # Conductores con calificacion riesgosa (nivel de riesgo alto) en el periodo
    conductores_en_riesgo: int
    # Distancia minima en el periodo para entrar en `mejores` y `a_vigilar`
    distancia_minima_ranking_m: float
    mejores: list[ItemRanking]
    a_vigilar: list[ItemRanking]
    por_calificacion: ConteoCalificaciones
    eventos_por_tipo: dict[TipoEvento, int]
    evolucion: EvolucionFlota
