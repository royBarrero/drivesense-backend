"""Redacción de las recomendaciones del viaje con IA (Claude), HU-18 y HU-19.

Las reglas de `service.py` deciden qué recomendaciones van y en qué orden; aquí Claude solo
redacta el título y el texto de cada una con los números del viaje. Si no hay clave, la API
falla, tarda o responde algo que no sirve, `redactar` devuelve `None` y se usan las plantillas.

A la API solo van datos agregados del viaje: nunca nombres, correos ni coordenadas.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from app.core.config import settings
from app.puntaje.service import Puntaje
from app.recomendaciones.models import TipoRecomendacion
from app.telemetria.models import TipoEvento

logger = logging.getLogger(__name__)

# Límites de las columnas (`titulo` 60, `texto` 300), con margen: lo que no entre usa la plantilla
MAXIMO_TITULO = 60
MAXIMO_TEXTO = 300
MAXIMO_TOKENS = 2000

SISTEMA = """Eres el coach de manejo de DriveSense, una app que mide cómo conduce una persona \
con los sensores de su teléfono y le da un DriveScore de 0 a 100 por viaje.

Recibirás los datos de un viaje recién terminado y la lista de consejos que hay que escribir, \
cada uno con su "orden" y su "tipo". Escribe un consejo por cada elemento de la lista, con el \
mismo "orden" y en el mismo orden.

Cada consejo tiene:
- "titulo": de 2 a 5 palabras, máximo 40 caracteres, sin punto final.
- "texto": una o dos oraciones, máximo 220 caracteres.

Cómo escribirlos:
- En español latinoamericano, tuteando, cálido y directo; nunca regañes.
- El texto menciona lo que pasó en el viaje con sus números reales (cantidad de eventos, \
velocidades en km/h, tiempo sobre el límite) y termina con UNA acción concreta de manejo para \
el próximo viaje.
- Usa solo los números que vienen en los datos; no inventes otros ni supongas el lugar, el \
clima o el tráfico. No menciones la "intensidad" ni el puntaje por categoría: sirven solo para \
entender qué tan fuerte fue cada evento.
- Decimales con coma (7,4 km) y velocidades redondeadas sin decimales.
- Las acciones son solo de cómo manejar (distancia, mirada, pedales, velocidad, salir con \
tiempo); no sugieras activar funciones, alarmas ni otras apps.
- Cada consejo trata solo de su tipo: frenada_brusca (frenadas fuertes), aceleracion_severa \
(arranques o aceleraciones fuertes), giro_agresivo (curvas o giros rápidos), exceso_velocidad \
(ir sobre el límite). Los consejos de un mismo viaje no repiten la misma idea.
- sin_eventos: el viaje no tuvo eventos de riesgo; felicita y anima a repetirlo, sin dar \
correcciones.
- No menciones que eres una IA ni la app; no uses emojis ni comillas."""


class TextoIA(BaseModel):
    orden: int
    titulo: str
    texto: str


class RespuestaIA(BaseModel):
    recomendaciones: list[TextoIA]


@dataclass(frozen=True)
class EventoViaje:
    tipo: TipoEvento
    velocidad_kmh: float
    intensidad: float
    duracion_s: float | None = None
    velocidad_maxima_kmh: float | None = None


@dataclass(frozen=True)
class ContextoViaje:
    """Lo que se envía a Claude: el viaje y los consejos a redactar (`orden`, `tipo`, cantidad)."""

    distancia_m: float
    duracion_s: int
    velocidad_promedio_kmh: float
    velocidad_maxima_kmh: float
    puntaje: Puntaje
    eventos: Sequence[EventoViaje]
    consejos: Sequence[tuple[int, TipoRecomendacion, int]]


def mensaje_de(contexto: ContextoViaje) -> str:
    """Datos del viaje en JSON (pura, para las pruebas)."""
    p = contexto.puntaje
    datos = {
        "viaje": {
            "distancia_km": round(contexto.distancia_m / 1000, 1),
            "duracion_min": round(contexto.duracion_s / 60),
            "velocidad_promedio_kmh": round(contexto.velocidad_promedio_kmh),
            "velocidad_maxima_kmh": round(contexto.velocidad_maxima_kmh),
            "drivescore": p.drivescore,
            "puntaje_por_categoria": {
                "velocidad": p.velocidad,
                "frenadas": p.frenadas,
                "aceleraciones": p.aceleraciones,
                "giros": p.giros,
            },
        },
        "eventos": [
            {
                "tipo": e.tipo.value,
                "velocidad_kmh": round(e.velocidad_kmh),
                # m/s² en las maniobras; m/s sobre el límite en el exceso
                "intensidad": round(e.intensidad, 1),
                **(
                    {"duracion_s": round(e.duracion_s), "velocidad_maxima_kmh": round(e.velocidad_maxima_kmh or 0)}
                    if e.tipo == TipoEvento.exceso_velocidad and e.duracion_s is not None
                    else {}
                ),
            }
            for e in contexto.eventos
        ],
        "consejos_a_escribir": [
            {"orden": orden, "tipo": tipo.value, "cantidad_de_eventos": cantidad}
            for orden, tipo, cantidad in contexto.consejos
        ],
    }
    return json.dumps(datos, ensure_ascii=False)


def validar(respuesta: RespuestaIA, contexto: ContextoViaje) -> dict[int, TextoIA] | None:
    """Textos por `orden` si la respuesta trae exactamente los consejos pedidos (pura).

    Un consejo vacío o más largo que su columna queda fuera: esa fila usa la plantilla.
    """
    pedidos = [orden for orden, _, _ in contexto.consejos]
    recibidos = [r.orden for r in respuesta.recomendaciones]
    if sorted(recibidos) != sorted(pedidos):
        return None
    validos = {}
    for r in respuesta.recomendaciones:
        titulo, texto = r.titulo.strip(), r.texto.strip()
        if 0 < len(titulo) <= MAXIMO_TITULO and 0 < len(texto) <= MAXIMO_TEXTO:
            validos[r.orden] = TextoIA(orden=r.orden, titulo=titulo, texto=texto)
    return validos


_cliente: anthropic.AsyncAnthropic | None = None


def _obtener_cliente() -> anthropic.AsyncAnthropic:
    global _cliente
    if _cliente is None:
        # Sin reintentos: un reintento haría esperar de más al conductor; si falla, plantilla
        _cliente = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=settings.ia_timeout_s, max_retries=0
        )
    return _cliente


async def redactar(contexto: ContextoViaje) -> dict[int, TextoIA] | None:
    """Título y texto de cada consejo por `orden`, o `None` si la IA no está o no respondió bien."""
    if not settings.anthropic_api_key or not contexto.consejos:
        return None
    try:
        respuesta = await _obtener_cliente().messages.parse(
            model=settings.ia_modelo,
            max_tokens=MAXIMO_TOKENS,
            system=SISTEMA,
            messages=[{"role": "user", "content": mensaje_de(contexto)}],
            output_format=RespuestaIA,
            # Textos cortos: sin razonamiento extendido y con poco esfuerzo responde más rápido
            # (con razonamiento, 3 consejos tardaban ~4,5 s, cerca del tope)
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
        )
    except anthropic.APITimeoutError:
        logger.warning("IA de recomendaciones: sin respuesta en %s s", settings.ia_timeout_s)
        return None
    except anthropic.RateLimitError as e:
        logger.warning("IA de recomendaciones: límite de uso (%s)", e.request_id)
        return None
    except anthropic.APIStatusError as e:
        logger.warning("IA de recomendaciones: error %s (%s): %s", e.status_code, e.request_id, e.message)
        return None
    except anthropic.APIConnectionError:
        logger.warning("IA de recomendaciones: sin conexión con la API")
        return None
    except ValueError:
        # Salida que no se pudo validar contra el esquema
        logger.warning("IA de recomendaciones: respuesta con formato inválido")
        return None

    if respuesta.stop_reason == "refusal" or respuesta.parsed_output is None:
        logger.warning(
            "IA de recomendaciones: sin texto utilizable (%s, %s)", respuesta.stop_reason, respuesta._request_id
        )
        return None
    validos = validar(respuesta.parsed_output, contexto)
    if validos is None:
        logger.warning("IA de recomendaciones: los consejos no coinciden con los pedidos (%s)", respuesta._request_id)
    return validos
