from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.puntaje.service import Agrupacion, Calificacion, PeriodoComparativo, Resultado


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


# --- Comparativo (HU-20) ---


class CambioSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # actual − anterior (un decimal en los eventos por 10 km, entero en los puntajes)
    diferencia: int | float
    # El puntaje mejora al subir; los eventos por 10 km, al bajar
    resultado: Resultado


class ResumenPeriodoSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    desde: datetime
    hasta: datetime
    viajes: int
    distancia_m: float
    eventos: int
    # Nulos si el periodo no tiene viajes
    drivescore: int | None
    calificacion: Calificacion | None
    # Claves: frenadas, aceleraciones, giros, velocidad
    categorias: dict[str, int] | None
    eventos_por_10km: float | None


class ComparativoSalida(BaseModel):
    """Periodo actual (lo que va) frente al anterior completo (HU-20). Promedios
    ponderados por distancia."""

    model_config = ConfigDict(from_attributes=True)

    periodo: PeriodoComparativo
    # Viajes con puntaje que necesita cada periodo para compararlos
    minimo_viajes: int
    actual: ResumenPeriodoSalida
    anterior: ResumenPeriodoSalida
    # Si es falso, lo que sigue es nulo: no hay datos para comparar
    comparable: bool
    drivescore: CambioSalida | None
    categorias: dict[str, CambioSalida] | None
    eventos_por_10km: CambioSalida | None
    # Solo la diferencia: más viajes o km no es mejor ni peor
    viajes: int | None
    distancia_m: float | None
    # Categoría que más subió / más bajó (empate → mayor riesgo); nula si ninguna
    mas_mejoro: str | None
    mas_empeoro: str | None
