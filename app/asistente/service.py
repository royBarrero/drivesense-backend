import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

from app.asistente import ia
from app.asistente.preguntas import responder_sin_ia, sugerencias_de
from app.asistente.schemas import ConversacionEntrada, RespuestaAsistente, Sugerencias
from app.auth.models import Usuario

# Cuida el costo de la IA: preguntas por usuario por hora (en la memoria de cada worker)
MAXIMO_PREGUNTAS_POR_HORA = 30
VENTANA_S = 3600
DEMASIADAS_PREGUNTAS = "Hiciste muchas preguntas seguidas. Intenta de nuevo en unos minutos."


class LimitePreguntas:
    """Cuenta las preguntas de cada usuario en la última hora."""

    def __init__(self, maximo: int = MAXIMO_PREGUNTAS_POR_HORA, ventana_s: float = VENTANA_S):
        self.maximo = maximo
        self.ventana_s = ventana_s
        self._preguntas: dict[int, deque[float]] = defaultdict(deque)

    def registrar(self, usuario_id: int, ahora: float) -> bool:
        """Anota la pregunta y devuelve `True`, o `False` si ya llegó al máximo."""
        preguntas = self._preguntas[usuario_id]
        while preguntas and ahora - preguntas[0] >= self.ventana_s:
            preguntas.popleft()
        if len(preguntas) >= self.maximo:
            return False
        preguntas.append(ahora)
        return True


limite = LimitePreguntas()


def obtener_sugerencias(usuario: Usuario) -> Sugerencias:
    return Sugerencias(sugerencias=sugerencias_de(usuario.rol))


async def responder(usuario: Usuario, datos: ConversacionEntrada) -> RespuestaAsistente:
    """Respuesta a la última pregunta con la guía del rol del usuario: la redacta la IA y, si no
    está o falla, sale de las preguntas frecuentes."""
    if not limite.registrar(usuario.id, time.monotonic()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=DEMASIADAS_PREGUNTAS)
    texto = await ia.responder(datos.mensajes, usuario.rol)
    if texto is not None:
        return RespuestaAsistente(texto=texto, generada_por_ia=True)
    pregunta = datos.mensajes[-1].texto
    return RespuestaAsistente(texto=responder_sin_ia(pregunta, usuario.rol), generada_por_ia=False)
