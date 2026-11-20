"""Asistente de ayuda: responde cómo usar la app (conductores) o el panel (admins). No lee
datos del usuario ni guarda la conversación."""

from fastapi import APIRouter

from app.asistente import service
from app.asistente.schemas import ConversacionEntrada, RespuestaAsistente, Sugerencias
from app.auth.dependencias import UsuarioActual

router = APIRouter(prefix="/asistente", tags=["Asistente"])


@router.get("/sugerencias", response_model=Sugerencias)
async def sugerencias(usuario: UsuarioActual):
    """Preguntas frecuentes del rol, para ofrecerlas al abrir el asistente."""
    return service.obtener_sugerencias(usuario)


@router.post("/mensajes", response_model=RespuestaAsistente)
async def mensajes(datos: ConversacionEntrada, usuario: UsuarioActual):
    """Responde la última pregunta de la conversación."""
    return await service.responder(usuario, datos)
