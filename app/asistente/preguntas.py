"""Preguntas frecuentes del asistente: los chips de sugerencias y el respaldo sin IA.

Cada pregunta tiene palabras clave (sin tildes, en minúsculas) y una respuesta fija sacada de la
guía de su rol. Si cambia una pantalla, actualizar aquí y en `guia_app.md` / `guia_panel.md`.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.auth.models import RolUsuario


@dataclass(frozen=True)
class PreguntaFrecuente:
    pregunta: str
    palabras_clave: tuple[str, ...]
    respuesta: str
    # Se ofrece como chip al abrir el asistente
    sugerida: bool = True


PREGUNTAS_APP: tuple[PreguntaFrecuente, ...] = (
    PreguntaFrecuente(
        "¿Cómo inicio un viaje?",
        ("inici", "empez", "comenz", "arranc", "recorrido", "viaje nuevo"),
        "1. En Inicio, toca \"Iniciar recorrido\".\n"
        "2. Elige \"Recorrido libre\" o \"Con destino\" (con destino eliges a dónde vas y ves la ruta).\n"
        "3. La primera vez, permite la ubicación mientras usas la app y las notificaciones.\n"
        "Pon el teléfono en un soporte y maneja: puedes apagar la pantalla y el viaje se sigue registrando.",
    ),
    PreguntaFrecuente(
        "¿Cómo termino un viaje?",
        ("termin", "finaliz", "acab", "deten", "parar"),
        "En la pantalla del viaje, toca \"Finalizar viaje\" y confirma con \"Finalizar\". Verás el resumen con "
        "el mapa, los eventos, tu DriveScore y tus consejos. Si el auto queda detenido 3 minutos, el viaje se "
        "finaliza solo. Los viajes de menos de 1 minuto o 200 metros no se guardan.",
    ),
    PreguntaFrecuente(
        "¿Dónde veo mis puntajes?",
        ("puntaje", "drivescore", "nota", "calificacion", "promedio", "score"),
        "El DriveScore de cada viaje está en su resumen y en su detalle (pestaña Viajes). Tu promedio de los "
        "últimos 7 días está en el anillo del encabezado de Inicio: tócalo para abrir \"Mi DriveScore\", donde "
        "eliges \"7 días\", \"30 días\" o \"3 meses\" y ves el gráfico y el promedio por categoría.",
    ),
    PreguntaFrecuente(
        "¿Cómo veo mi historial de viajes?",
        ("historial", "storial", "viajes anteriores", "mis viajes", "ver viaje", "detalle", "pasados"),
        "Toca la pestaña \"Viajes\" y elige \"Esta semana\", \"Este mes\" o \"Todos\". Toca un viaje para ver su "
        "mapa, su DriveScore, los eventos y los consejos; \"Ver mapa completo\" lo abre a pantalla completa.",
    ),
    PreguntaFrecuente(
        "¿Cómo comparo esta semana con la anterior?",
        ("compar", "semana pasada", "mes pasado", "anterior", "mejore"),
        "En Inicio, toca el anillo del DriveScore para abrir \"Mi DriveScore\". Al final, toca \"Comparar "
        "periodos\" y elige \"Semana\" o \"Mes\". Hacen falta al menos 2 viajes en cada periodo.",
    ),
    PreguntaFrecuente(
        "¿Qué son los desafíos semanales?",
        ("desafio", "reto", "bono"),
        "Son 3 metas que cambian cada lunes. Los ves en Inicio, en \"Desafíos de la semana\" (\"Ver todos\" "
        "muestra el detalle). Se completan solos al terminar un viaje que cumple la meta y dan puntos; si "
        "completas los 3, ganas un bono de 100 puntos.",
    ),
    PreguntaFrecuente(
        "¿Dónde veo mi nivel y mis logros?",
        ("nivel", "logro", "puntos", "insignia", "medalla"),
        "En Perfil, la tarjeta de nivel muestra tu nivel y cuántos puntos te faltan para el siguiente. Toca "
        "\"Logros\" para ver los desbloqueados y los pendientes.",
        sugerida=False,
    ),
    PreguntaFrecuente(
        "¿Cómo uso el Copiloto?",
        ("copiloto", "destino", "ruta", "navega", "voz", "guia"),
        "Toca \"Iniciar recorrido\" y elige \"Con destino\". Elige el destino tocando el mapa o con \"Buscar "
        "destino\", revisa la ruta y toca \"Iniciar con destino\". Te guía por voz y recalcula si te sales de "
        "la ruta; \"Silenciar voz\" la calla en ese viaje.",
        sugerida=False,
    ),
    PreguntaFrecuente(
        "¿Qué es el Modo aprendizaje?",
        ("aprendizaje", "consejo por voz", "consejos por voz"),
        "Da consejos breves por voz después de cada evento de riesgo. Actívalo con el interruptor \"Modo "
        "aprendizaje\" en Perfil, en el Copiloto o en la pantalla del viaje.",
        sugerida=False,
    ),
    PreguntaFrecuente(
        "¿Qué eventos de riesgo detecta la app?",
        ("evento", "frenada", "aceleracion", "giro", "exceso", "velocidad", "riesgo"),
        "Frenada brusca, aceleración severa, giro agresivo y exceso de velocidad (más de 60 km/h durante 5 "
        "segundos o más). Cuando ocurre uno, el teléfono vibra y muestra un aviso; el sonido se ajusta en "
        "Perfil, en \"Sonido de avisos\".",
        sugerida=False,
    ),
    # Antes que la de iniciar sesión: en un empate gana la primera
    PreguntaFrecuente(
        "¿Cómo cierro sesión?",
        ("cerrar sesion", "cierr", "salir", "logout", "desconect"),
        "En Perfil, toca \"Cerrar sesión\". Si tienes un viaje en curso, primero finalízalo.",
        sugerida=False,
    ),
    PreguntaFrecuente(
        "¿Cómo inicio sesión o creo una cuenta?",
        ("inicio sesion", "iniciar sesion", "sesion", "login", "entrar", "ingres", "cuenta", "registr", "contrasena"),
        "Escribe tu correo y contraseña y toca \"Iniciar sesión\". Para crear una cuenta, toca \"Crear "
        "cuenta\" y completa tus datos. Si eres conductor de una flota, tu empresa te da una contraseña "
        "temporal que cambias la primera vez; si la olvidaste, pídele al administrador una nueva.",
        sugerida=False,
    ),
)

PREGUNTAS_PANEL: tuple[PreguntaFrecuente, ...] = (
    PreguntaFrecuente(
        "¿Cómo registro un conductor?",
        ("registr", "agreg", "nuevo conductor", "anadir", "alta", "crear conductor"),
        "1. Ve a \"Conductores\" y toca \"Registrar conductor\".\n"
        "2. Escribe su nombre, correo y teléfono, y confirma.\n"
        "3. Copia la contraseña temporal (se muestra una sola vez) y entrégasela: con ella entra a la app y la "
        "cambia la primera vez.",
    ),
    PreguntaFrecuente(
        "¿Cómo veo el perfil de un conductor?",
        ("perfil", "ver conductor", "desempeno", "detalle del conductor", "estilo"),
        "En \"Conductores\", toca su fila. La pestaña \"Resumen\" muestra su DriveScore, su estilo de "
        "conducción, la evolución y sus viajes; la pestaña \"Eventos de riesgo\" muestra los patrones, el mapa "
        "y el historial de eventos.",
    ),
    PreguntaFrecuente(
        "¿Cómo exporto un reporte?",
        ("export", "reporte", "pdf", "csv", "descarg", "informe"),
        "Toca \"Exportar reporte\" en \"Conductores\" (reporte de la flota) o en el perfil de un conductor. Elige "
        "el periodo y el formato (PDF o CSV) y descárgalo. Exportar no está en el plan Gratis.",
    ),
    PreguntaFrecuente(
        "¿Cómo cambio de plan?",
        ("plan", "suscrip", "premium", "pagar", "precio", "mejorar"),
        "Ve a \"Plan\", y en \"Compara los planes\" toca \"Cambiar a …\" en el plan que quieras. Revisa el "
        "resumen y toca \"Confirmar\". El pago es simulado y bajar de plan nunca borra datos.",
    ),
    PreguntaFrecuente(
        "Un conductor olvidó su contraseña",
        ("contrasena", "olvid", "clave", "password"),
        "En \"Conductores\", toca el botón de la llave en su fila (\"Nueva contraseña temporal\") y confirma "
        "con \"Generar contraseña\". Entrégale la nueva; la anterior deja de servir.",
    ),
    PreguntaFrecuente(
        "¿Qué muestra la sección Reportes?",
        ("tablero", "flota", "estadistica", "indicador", "ranking", "vigilar", "destacado"),
        "Es el tablero de la flota: DriveScore, kilómetros, eventos cada 10 km, conductores en riesgo, los "
        "destacados y los que hay que vigilar, la distribución por calificación y por estilo, y la evolución. "
        "Elige el periodo arriba. En el plan Gratis solo se ven las tarjetas de resumen.",
    ),
    PreguntaFrecuente(
        "¿Cómo manejan los conductores sus viajes?",
        ("app", "telefono", "celular", "movil", "registrar viaje", "descarg la app"),
        "Los conductores usan la app de DriveSense en su teléfono Android: inician sesión con su correo y la "
        "contraseña temporal, y registran cada viaje con \"Iniciar recorrido\". El panel muestra sus viajes y "
        "puntajes cuando los terminan.",
        sugerida=False,
    ),
)

MENSAJE_SIN_RESPUESTA = "No tengo esa respuesta ahora. Prueba con una de estas preguntas:"


def preguntas_de(rol: RolUsuario) -> tuple[PreguntaFrecuente, ...]:
    return PREGUNTAS_APP if rol == RolUsuario.conductor else PREGUNTAS_PANEL


def sugerencias_de(rol: RolUsuario) -> list[str]:
    return [p.pregunta for p in preguntas_de(rol) if p.sugerida]


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes ni signos (pura)."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]+", " ", sin_tildes)


def responder_sin_ia(pregunta: str, rol: RolUsuario) -> str:
    """La respuesta fija que más palabras clave comparte con la pregunta; si ninguna, el
    mensaje con las sugerencias (pura). En empate gana la que aparece primero."""
    texto = normalizar(pregunta)
    mejor, coincidencias = None, 0
    for frecuente in preguntas_de(rol):
        n = sum(1 for clave in frecuente.palabras_clave if clave in texto)
        if n > coincidencias:
            mejor, coincidencias = frecuente, n
    if mejor is not None:
        return mejor.respuesta
    return "\n".join([MENSAJE_SIN_RESPUESTA, *(f"• {s}" for s in sugerencias_de(rol))])
