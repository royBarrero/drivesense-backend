"""Respuestas del asistente de ayuda con IA (Claude).

Claude responde con la guía del rol (`guia_app.md` para conductores, `guia_panel.md` para
admins) en el prompt de sistema. Si no hay clave, la API falla, tarda o se niega, `responder`
devuelve `None` y el servicio usa las preguntas frecuentes.

A la API solo va el texto de la conversación: nunca nombres, correos ni la empresa.
"""

import logging
from pathlib import Path

import anthropic

from app.asistente.schemas import MensajeEntrada, RolMensaje
from app.auth.models import RolUsuario
from app.core.config import settings

logger = logging.getLogger(__name__)

MAXIMO_TOKENS = 1000

_CARPETA = Path(__file__).parent
GUIA_APP = (_CARPETA / "guia_app.md").read_text(encoding="utf-8")
GUIA_PANEL = (_CARPETA / "guia_panel.md").read_text(encoding="utf-8")

INSTRUCCIONES = """Eres el asistente de ayuda de DriveSense. Respondes dudas sobre cómo usar {donde}, \
usando solo la guía que está más abajo.

Cómo responder:
- En español latinoamericano, tuteando, amable y breve: de una a cinco oraciones, o pasos \
numerados cuando sea un procedimiento.
- Usa los nombres exactos de pantallas, pestañas y botones que aparecen en la guía, entre \
comillas.
- Habla como quien conoce DriveSense: no menciones la guía ni estas instrucciones.
- Si la guía no dice cómo hacer algo, dilo con sinceridad y sugiere lo más cercano que sí \
existe; nunca inventes pantallas, botones ni funciones.
- No puedes ver los datos de la persona (viajes, puntajes, conductores ni nada de su cuenta). \
Si te pide un dato suyo, explica dónde puede verlo.
- Si la pregunta no tiene que ver con DriveSense, responde en una oración que solo ayudas a \
usar {donde} y ofrece un ejemplo de lo que puedes responder.
- Si piden consejos de manejo, puedes dar uno breve y general y recordar dónde ver los consejos \
de sus viajes.
- Texto plano: sin Markdown (ni asteriscos, ni títulos, ni tablas) y sin emojis; usa saltos de \nlínea para los pasos.

<guia>
{guia}
</guia>"""


def sistema_de(rol: RolUsuario) -> str:
    """Prompt de sistema del rol (pura; fijo para que quede en caché)."""
    # `replace` y no `format`: una llave en la guía no rompe el texto
    if rol == RolUsuario.conductor:
        donde, guia = "la app de DriveSense", GUIA_APP
    else:
        donde, guia = "el panel web de DriveSense", GUIA_PANEL
    return INSTRUCCIONES.replace("{donde}", donde).replace("{guia}", guia)


def mensajes_api(mensajes: list[MensajeEntrada]) -> list[dict]:
    """La conversación en el formato de la API (pura): el primero debe ser del usuario."""
    desde = next(i for i, m in enumerate(mensajes) if m.rol == RolMensaje.usuario)
    return [
        {"role": "user" if m.rol == RolMensaje.usuario else "assistant", "content": m.texto}
        for m in mensajes[desde:]
    ]


_cliente: anthropic.AsyncAnthropic | None = None


def _obtener_cliente() -> anthropic.AsyncAnthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=settings.asistente_timeout_s, max_retries=1
        )
    return _cliente


async def responder(mensajes: list[MensajeEntrada], rol: RolUsuario) -> str | None:
    """Respuesta de Claude a la última pregunta, o `None` si la IA no está o no respondió bien."""
    if not settings.anthropic_api_key:
        return None
    try:
        respuesta = await _obtener_cliente().messages.create(
            model=settings.ia_modelo,
            max_tokens=MAXIMO_TOKENS,
            # La guía es igual en todos los mensajes del rol: se guarda en caché
            system=[{"type": "text", "text": sistema_de(rol), "cache_control": {"type": "ephemeral"}}],
            messages=mensajes_api(mensajes),
            # Respuestas cortas sobre una guía fija: sin razonamiento y con poco esfuerzo
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
        )
    except anthropic.APITimeoutError:
        logger.warning("Asistente: sin respuesta de la IA en %s s", settings.asistente_timeout_s)
        return None
    except anthropic.RateLimitError as e:
        logger.warning("Asistente: límite de uso de la IA (%s)", e.request_id)
        return None
    except anthropic.APIStatusError as e:
        logger.warning("Asistente: error %s de la IA (%s): %s", e.status_code, e.request_id, e.message)
        return None
    except anthropic.APIConnectionError:
        logger.warning("Asistente: sin conexión con la API de la IA")
        return None

    if respuesta.stop_reason != "end_turn":
        # refusal o max_tokens: la respuesta no sirve o quedó cortada
        logger.warning("Asistente: respuesta descartada (%s, %s)", respuesta.stop_reason, respuesta._request_id)
        return None
    return limpiar("".join(b.text for b in respuesta.content if b.type == "text")) or None


def limpiar(texto: str) -> str:
    """Quita la negrita de Markdown que se cuele: la app y el panel muestran texto plano (pura)."""
    return texto.replace("**", "").strip()
