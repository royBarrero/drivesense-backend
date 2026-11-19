"""Lo que comparten los proveedores de la navegación (HU-30): mensajes, errores,
el foco de la búsqueda y las indicaciones en español."""

from fastapi import status

from app.core.errores import ErrorDeNavegacion
from app.navegacion.schemas import TipoPaso

# Búsqueda: solo Bolivia, con prioridad a lo cercano al centro de Santa Cruz de la Sierra
PAIS = "BO"
FOCO_LAT = -17.7833
FOCO_LON = -63.1821
MAXIMO_LUGARES = 5

# Los servidores públicos piden identificar la app; sin datos personales
USER_AGENT = "DriveSense/1.0 (proyecto universitario)"

SIN_RUTA = "No se encontró una ruta en auto hasta ese destino"
DESTINO_LEJANO = "El destino está demasiado lejos para calcular la ruta"
SIN_CUOTA = "Se alcanzó el límite de consultas de rutas; intenta más tarde"
PROVEEDOR_CAIDO = "El servicio de rutas no responde; intenta más tarde"


def caido() -> ErrorDeNavegacion:
    return ErrorDeNavegacion(status.HTTP_503_SERVICE_UNAVAILABLE, "proveedor_caido", PROVEEDOR_CAIDO)


def sin_ruta(mensaje: str = SIN_RUTA) -> ErrorDeNavegacion:
    return ErrorDeNavegacion(status.HTTP_404_NOT_FOUND, "sin_ruta", mensaje)


def sin_cuota() -> ErrorDeNavegacion:
    return ErrorDeNavegacion(status.HTTP_503_SERVICE_UNAVAILABLE, "sin_cuota", SIN_CUOTA)


_ORDINALES = [
    "primera", "segunda", "tercera", "cuarta", "quinta",
    "sexta", "séptima", "octava", "novena", "décima",
]


def instruccion(tipo: TipoPaso, calle: str | None, salida: int | None = None) -> str:
    """Indicación en español y en tuteo, como la voz de la app (pura):
    "Gira a la derecha en Avenida Busch", "En la rotonda, toma la segunda salida"."""
    en = f" en {calle}" if calle else ""
    por = f" por {calle}" if calle else ""
    match tipo:
        case TipoPaso.girar_izquierda:
            return f"Gira a la izquierda{en}"
        case TipoPaso.girar_derecha:
            return f"Gira a la derecha{en}"
        case TipoPaso.girar_fuerte_izquierda:
            return f"Gira cerrado a la izquierda{en}"
        case TipoPaso.girar_fuerte_derecha:
            return f"Gira cerrado a la derecha{en}"
        case TipoPaso.girar_leve_izquierda:
            return f"Gira levemente a la izquierda{en}"
        case TipoPaso.girar_leve_derecha:
            return f"Gira levemente a la derecha{en}"
        case TipoPaso.seguir_recto:
            return f"Sigue derecho{por}"
        case TipoPaso.entrar_rotonda:
            if salida is None:
                return "Entra a la rotonda"
            ordinal = _ORDINALES[salida - 1] if 1 <= salida <= len(_ORDINALES) else f"{salida}.ª"
            return f"En la rotonda, toma la {ordinal} salida"
        case TipoPaso.salir_rotonda:
            return f"Sal de la rotonda{por}"
        case TipoPaso.media_vuelta:
            return "Da media vuelta"
        case TipoPaso.llegada:
            return "Llegaste a tu destino"
        case TipoPaso.salida:
            return f"Sal{por}" if calle else "Comienza el recorrido"
        case TipoPaso.mantener_izquierda:
            return f"Mantente a la izquierda{por}"
        case TipoPaso.mantener_derecha:
            return f"Mantente a la derecha{por}"
