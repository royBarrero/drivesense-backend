import enum
from typing import Annotated

from pydantic import BaseModel, StringConstraints, field_validator, model_validator

# Topes: una pregunta de uso es corta; la conversación se recorta para cuidar el costo
MAXIMO_MENSAJES = 20
MAXIMO_PREGUNTA = 500
MAXIMO_RESPUESTA = 2000


class RolMensaje(str, enum.Enum):
    usuario = "usuario"
    asistente = "asistente"


class MensajeEntrada(BaseModel):
    rol: RolMensaje
    texto: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAXIMO_RESPUESTA)]

    @model_validator(mode="after")
    def validar_largo(self) -> "MensajeEntrada":
        if self.rol == RolMensaje.usuario and len(self.texto) > MAXIMO_PREGUNTA:
            raise ValueError(f"La pregunta puede tener hasta {MAXIMO_PREGUNTA} caracteres")
        return self


class ConversacionEntrada(BaseModel):
    """La conversación completa: la guarda el cliente; el backend no guarda nada."""

    mensajes: list[MensajeEntrada]

    @field_validator("mensajes")
    @classmethod
    def validar_mensajes(cls, valor: list[MensajeEntrada]) -> list[MensajeEntrada]:
        if not valor:
            raise ValueError("Escribe una pregunta")
        if len(valor) > MAXIMO_MENSAJES:
            raise ValueError(f"La conversación puede tener hasta {MAXIMO_MENSAJES} mensajes")
        if valor[-1].rol != RolMensaje.usuario:
            raise ValueError("El último mensaje debe ser una pregunta")
        return valor


class RespuestaAsistente(BaseModel):
    texto: str
    # Redactada por la IA; falsa = respuesta fija de las preguntas frecuentes
    generada_por_ia: bool


class Sugerencias(BaseModel):
    sugerencias: list[str]
