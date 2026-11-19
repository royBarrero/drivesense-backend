import enum

from pydantic import BaseModel


class TipoPaso(str, enum.Enum):
    """Maniobra de cada paso; nombres estables para la app (ORS usa números del 0 al 13)."""

    girar_izquierda = "girar_izquierda"
    girar_derecha = "girar_derecha"
    girar_fuerte_izquierda = "girar_fuerte_izquierda"
    girar_fuerte_derecha = "girar_fuerte_derecha"
    girar_leve_izquierda = "girar_leve_izquierda"
    girar_leve_derecha = "girar_leve_derecha"
    seguir_recto = "seguir_recto"
    entrar_rotonda = "entrar_rotonda"
    salir_rotonda = "salir_rotonda"
    media_vuelta = "media_vuelta"
    llegada = "llegada"
    salida = "salida"
    mantener_izquierda = "mantener_izquierda"
    mantener_derecha = "mantener_derecha"


class Coordenada(BaseModel):
    lat: float
    lon: float


class PasoRuta(BaseModel):
    # Texto de la indicación en español, tal como lo arma OpenRouteService
    instruccion: str
    tipo: TipoPaso
    # Nombre de la calle; nulo si OSM no lo tiene
    calle: str | None
    distancia_m: float
    duracion_s: float
    # Número de salida, solo al entrar a una rotonda
    salida_rotonda: int | None
    # Tramo del paso: índices dentro de `trazado`
    inicio: int
    fin: int


class RutaSalida(BaseModel):
    """Ruta en auto del origen al destino (HU-30)."""

    distancia_m: float
    duracion_s: float
    trazado: list[Coordenada]
    pasos: list[PasoRuta]


class Lugar(BaseModel):
    nombre: str
    # Nombre completo con barrio, ciudad y departamento
    direccion: str
    lat: float
    lon: float


class LugaresSalida(BaseModel):
    lugares: list[Lugar]
