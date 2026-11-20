"""Asistente de ayuda: esquemas, preguntas frecuentes, guía por rol, límite y servicio con la IA
simulada (sin BD y sin llamar a la API)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.asistente import ia, service
from app.asistente.preguntas import (
    MENSAJE_SIN_RESPUESTA,
    PREGUNTAS_APP,
    PREGUNTAS_PANEL,
    responder_sin_ia,
    sugerencias_de,
)
from app.asistente.schemas import MAXIMO_MENSAJES, ConversacionEntrada, MensajeEntrada, RolMensaje
from app.auth.models import RolUsuario

CONDUCTOR = RolUsuario.conductor
ADMIN = RolUsuario.admin_empresa


def conversacion(*textos: str) -> ConversacionEntrada:
    """Alterna usuario / asistente empezando por el usuario."""
    return ConversacionEntrada.model_validate(
        {"mensajes": [{"rol": "usuario" if i % 2 == 0 else "asistente", "texto": t} for i, t in enumerate(textos)]}
    )


# --- Esquemas ----------------------------------------------------------------


def test_conversacion_valida_recorta_espacios():
    datos = conversacion("  ¿Cómo inicio un viaje?  ")
    assert datos.mensajes[0].texto == "¿Cómo inicio un viaje?"


@pytest.mark.parametrize(
    "mensajes",
    [
        [],
        [{"rol": "usuario", "texto": "hola"}, {"rol": "asistente", "texto": "¿En qué te ayudo?"}],
        [{"rol": "usuario", "texto": "   "}],
        [{"rol": "usuario", "texto": "a" * 501}],
        [{"rol": "usuario", "texto": "hola"}] * (MAXIMO_MENSAJES + 1),
        [{"rol": "sistema", "texto": "hola"}],
    ],
)
def test_conversacion_invalida(mensajes):
    with pytest.raises(ValidationError):
        ConversacionEntrada.model_validate({"mensajes": mensajes})


def test_respuestas_del_asistente_pueden_ser_largas():
    conversacion("hola", "a" * 1500, "gracias")


# --- Preguntas frecuentes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("pregunta", "esperada"),
    [
        ("¿Cómo inicio un viaje?", "¿Cómo inicio un viaje?"),
        ("como veo mis puntajes", "¿Dónde veo mis puntajes?"),
        ("como veo mi estorial", "¿Cómo veo mi historial de viajes?"),
        ("¿Cómo cierro sesión?", "¿Cómo cierro sesión?"),
        ("como inicio sesion", "¿Cómo inicio sesión o creo una cuenta?"),
        ("¿qué son los DESAFÍOS?", "¿Qué son los desafíos semanales?"),
    ],
)
def test_respuesta_sin_ia_del_conductor(pregunta, esperada):
    frecuente = next(p for p in PREGUNTAS_APP if p.pregunta == esperada)
    assert responder_sin_ia(pregunta, CONDUCTOR) == frecuente.respuesta


@pytest.mark.parametrize(
    ("pregunta", "esperada"),
    [
        ("¿cómo agrego un conductor nuevo?", "¿Cómo registro un conductor?"),
        ("quiero descargar un pdf", "¿Cómo exporto un reporte?"),
        ("mi conductor olvidó la contraseña", "Un conductor olvidó su contraseña"),
    ],
)
def test_respuesta_sin_ia_del_admin(pregunta, esperada):
    frecuente = next(p for p in PREGUNTAS_PANEL if p.pregunta == esperada)
    assert responder_sin_ia(pregunta, ADMIN) == frecuente.respuesta


def test_sin_coincidencia_ofrece_las_sugerencias():
    respuesta = responder_sin_ia("¿cuál es la capital de Francia?", CONDUCTOR)
    assert respuesta.startswith(MENSAJE_SIN_RESPUESTA)
    assert "¿Cómo inicio un viaje?" in respuesta


def test_sugerencias_por_rol():
    assert "¿Cómo inicio un viaje?" in sugerencias_de(CONDUCTOR)
    assert "¿Cómo registro un conductor?" in sugerencias_de(ADMIN)
    assert 4 <= len(sugerencias_de(CONDUCTOR)) <= 6
    assert 4 <= len(sugerencias_de(ADMIN)) <= 6


# --- Guía y formato para la API --------------------------------------------------


def test_cada_rol_recibe_su_guia():
    app, panel = ia.sistema_de(CONDUCTOR), ia.sistema_de(ADMIN)
    assert "la app de DriveSense" in app and "Iniciar recorrido" in app
    assert "el panel web de DriveSense" in panel and "Registrar conductor" in panel
    assert "{donde}" not in app and "{guia}" not in panel


def test_mensajes_api_empiezan_por_el_usuario():
    mensajes = [
        MensajeEntrada(rol=RolMensaje.asistente, texto="¡Hola! ¿En qué te ayudo?"),
        MensajeEntrada(rol=RolMensaje.usuario, texto="¿Cómo inicio un viaje?"),
    ]
    assert ia.mensajes_api(mensajes) == [{"role": "user", "content": "¿Cómo inicio un viaje?"}]


async def test_sin_clave_no_llama_a_la_api(monkeypatch):
    monkeypatch.setattr(ia.settings, "anthropic_api_key", "")
    assert await ia.responder(conversacion("hola").mensajes, CONDUCTOR) is None


# --- Límite y servicio ------------------------------------------------------------


def test_limite_por_usuario_y_ventana():
    limite = service.LimitePreguntas(maximo=2, ventana_s=60)
    assert limite.registrar(1, 0) and limite.registrar(1, 10)
    assert not limite.registrar(1, 20)
    assert limite.registrar(2, 20)  # otro usuario
    assert limite.registrar(1, 60)  # la primera ya salió de la ventana


@pytest.fixture
def limite_nuevo(monkeypatch):
    monkeypatch.setattr(service, "limite", service.LimitePreguntas())


async def test_servicio_con_ia(monkeypatch, limite_nuevo):
    async def responder(mensajes, rol):
        assert rol == CONDUCTOR
        return "Toca \"Iniciar recorrido\" en Inicio."

    monkeypatch.setattr(ia, "responder", responder)
    respuesta = await service.responder(SimpleNamespace(id=1, rol=CONDUCTOR), conversacion("¿Cómo inicio un viaje?"))
    assert respuesta.generada_por_ia and "Iniciar recorrido" in respuesta.texto


async def test_servicio_sin_ia_usa_las_preguntas_frecuentes(monkeypatch, limite_nuevo):
    async def responder(mensajes, rol):
        return None

    monkeypatch.setattr(ia, "responder", responder)
    respuesta = await service.responder(SimpleNamespace(id=1, rol=ADMIN), conversacion("¿cómo exporto un pdf?"))
    assert not respuesta.generada_por_ia
    assert "Exportar reporte" in respuesta.texto


async def test_servicio_responde_429_al_pasar_el_limite(monkeypatch):
    async def responder(mensajes, rol):
        return "ok"

    monkeypatch.setattr(ia, "responder", responder)
    monkeypatch.setattr(service, "limite", service.LimitePreguntas(maximo=1))
    usuario = SimpleNamespace(id=7, rol=CONDUCTOR)
    await service.responder(usuario, conversacion("hola"))
    with pytest.raises(HTTPException) as error:
        await service.responder(usuario, conversacion("hola"))
    assert error.value.status_code == 429


def test_limpiar_quita_la_negrita():
    assert ia.limpiar("  Ve a **Viajes** y toca un viaje.\n") == "Ve a Viajes y toca un viaje."
