"""Recomendaciones del recorrido (HU-18 y HU-19), con reglas fijas (sin IA).

La elección y el armado del texto son puros; `obtener_recomendaciones` hace las
consultas. `generar_recomendaciones` devuelve la lista completa y ordenada: la
primera es la principal (HU-18) y las siguientes, las secundarias (HU-19).
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.puntaje.service import EventoPuntaje, Puntaje
from app.recomendaciones.models import Recomendacion, TipoRecomendacion
from app.recorridos.models import Recorrido
from app.telemetria.models import Evento, TipoEvento

# Desempate cuando dos categorías quedan con el mismo puntaje: primero el de mayor riesgo
ORDEN_RIESGO: tuple[TipoEvento, ...] = (
    TipoEvento.exceso_velocidad,
    TipoEvento.frenada_brusca,
    TipoEvento.giro_agresivo,
    TipoEvento.aceleracion_severa,
)

ORDEN_PRINCIPAL = 0
# Secundarias que se guardan como máximo (HU-19), después de la principal
MAXIMO_SECUNDARIAS = 2


@dataclass(frozen=True)
class Plantilla:
    """Textos de una variante. `{n}` = cantidad de eventos; `{duracion}` = tiempo
    total en exceso (solo en el exceso de velocidad)."""

    titulo: str
    texto_una: str
    texto_varias: str


@dataclass(frozen=True)
class RecomendacionGenerada:
    tipo: TipoRecomendacion
    titulo: str
    texto: str


PLANTILLAS: dict[TipoRecomendacion, tuple[Plantilla, ...]] = {
    TipoRecomendacion.frenada_brusca: (
        Plantilla(
            "Anticipa tus frenadas",
            "Tuviste una frenada brusca. Deja unos 3 segundos de distancia con el auto "
            "de adelante para poder frenar con suavidad.",
            "Frenaste bruscamente {n} veces. Deja unos 3 segundos de distancia con el "
            "auto de adelante para poder frenar con suavidad.",
        ),
        Plantilla(
            "Mira más adelante",
            "Tuviste una frenada brusca. Fíjate en los semáforos y el tráfico que vienen "
            "y suelta el acelerador antes.",
            "Frenaste bruscamente {n} veces. Fíjate en los semáforos y el tráfico que "
            "vienen y suelta el acelerador antes.",
        ),
        Plantilla(
            "Frena de a poco",
            "Hubo una frenada brusca. Empieza a frenar antes y aprieta el pedal poco a poco.",
            "Hubo {n} frenadas bruscas. Empieza a frenar antes y aprieta el pedal poco a poco.",
        ),
    ),
    TipoRecomendacion.aceleracion_severa: (
        Plantilla(
            "Acelera con suavidad",
            "Tuviste una aceleración fuerte. Al arrancar, pisa el acelerador de a poco "
            "durante los primeros segundos.",
            "Aceleraste fuerte {n} veces. Al arrancar, pisa el acelerador de a poco "
            "durante los primeros segundos.",
        ),
        Plantilla(
            "Arranca sin apuro",
            "Hubo una aceleración fuerte. Cuando cambie el semáforo, arranca de forma "
            "gradual: llegarás casi al mismo tiempo.",
            "Hubo {n} aceleraciones fuertes. Cuando cambie el semáforo, arranca de forma "
            "gradual: llegarás casi al mismo tiempo.",
        ),
        Plantilla(
            "Cuida el acelerador",
            "Aceleraste fuerte una vez. Acelera de forma pareja: es más seguro y gastas "
            "menos combustible.",
            "Aceleraste fuerte {n} veces. Acelera de forma pareja: es más seguro y gastas "
            "menos combustible.",
        ),
    ),
    TipoRecomendacion.giro_agresivo: (
        Plantilla(
            "Frena antes de la curva",
            "Tomaste una curva muy rápido. Baja la velocidad antes de entrar a la curva, "
            "no durante, y gira el volante con suavidad.",
            "Tomaste {n} curvas muy rápido. Baja la velocidad antes de entrar a la curva, "
            "no durante, y gira el volante con suavidad.",
        ),
        Plantilla(
            "Gira con suavidad",
            "Hubo un giro brusco. Reduce la velocidad antes de doblar y mueve el volante "
            "de forma pareja.",
            "Hubo {n} giros bruscos. Reduce la velocidad antes de doblar y mueve el "
            "volante de forma pareja.",
        ),
        Plantilla(
            "Curvas más tranquilas",
            "Un giro fue demasiado brusco. En curvas y esquinas, entra despacio y acelera "
            "recién al salir.",
            "Hubo {n} giros demasiado bruscos. En curvas y esquinas, entra despacio y "
            "acelera recién al salir.",
        ),
    ),
    TipoRecomendacion.exceso_velocidad: (
        Plantilla(
            "Respeta el límite de velocidad",
            "Superaste el límite durante {duracion}. Mira el velocímetro seguido y suelta "
            "el acelerador al acercarte al límite.",
            "Superaste el límite {n} veces, en total {duracion}. Mira el velocímetro "
            "seguido y suelta el acelerador al acercarte al límite.",
        ),
        Plantilla(
            "Sal con tiempo",
            "Fuiste sobre el límite durante {duracion}. Sal unos minutos antes para no "
            "tener que apurarte.",
            "Fuiste sobre el límite {n} veces, en total {duracion}. Sal unos minutos "
            "antes para no tener que apurarte.",
        ),
        Plantilla(
            "Baja la velocidad",
            "Ibas sobre el límite durante {duracion}. Dentro del límite tienes más tiempo "
            "para reaccionar ante cualquier imprevisto.",
            "Ibas sobre el límite {n} veces, en total {duracion}. Dentro del límite "
            "tienes más tiempo para reaccionar ante cualquier imprevisto.",
        ),
    ),
    TipoRecomendacion.sin_eventos: (
        Plantilla(
            "¡Excelente viaje!",
            "No tuviste eventos de riesgo. Sigue manejando con esta calma y atención.",
            "No tuviste eventos de riesgo. Sigue manejando con esta calma y atención.",
        ),
        Plantilla(
            "Conducción impecable",
            "Ni frenadas bruscas ni excesos en este viaje. Mantén la distancia y la "
            "velocidad como hoy.",
            "Ni frenadas bruscas ni excesos en este viaje. Mantén la distancia y la "
            "velocidad como hoy.",
        ),
        Plantilla(
            "¡Así se maneja!",
            "Fue un viaje sin eventos de riesgo. ¡Sigue así en el próximo!",
            "Fue un viaje sin eventos de riesgo. ¡Sigue así en el próximo!",
        ),
    ),
}


def _puntaje_de(puntaje: Puntaje, tipo: TipoEvento) -> int:
    return {
        TipoEvento.frenada_brusca: puntaje.frenadas,
        TipoEvento.aceleracion_severa: puntaje.aceleraciones,
        TipoEvento.giro_agresivo: puntaje.giros,
        TipoEvento.exceso_velocidad: puntaje.velocidad,
    }[tipo]


def ordenar_tipos(puntaje: Puntaje, conteo: dict[TipoEvento, int]) -> list[TipoEvento]:
    """Tipos con eventos, del que más restó al que menos.

    Mismo criterio que "Lo que más restó" de la app (HU-16): menor puntaje de la
    categoría; en empate, mayor riesgo (`ORDEN_RIESGO`). Un tipo con eventos cuya
    categoría quedó en 100 por redondeo también entra, al final.
    """
    con_eventos = [tipo for tipo in ORDEN_RIESGO if conteo.get(tipo, 0) > 0]
    return sorted(con_eventos, key=lambda t: (_puntaje_de(puntaje, t), ORDEN_RIESGO.index(t)))


def formatear_duracion(segundos: float) -> str:
    """"1 segundo", "45 segundos"; desde 1 min, en minutos redondeados ("2 minutos")."""
    s = max(1, round(segundos))
    if s < 60:
        return f"{s} {'segundo' if s == 1 else 'segundos'}"
    minutos = round(s / 60)
    return f"{minutos} {'minuto' if minutos == 1 else 'minutos'}"


def _armar(
    tipo: TipoRecomendacion, variante: int, cantidad: int, segundos_exceso: float
) -> RecomendacionGenerada:
    plantillas = PLANTILLAS[tipo]
    plantilla = plantillas[variante % len(plantillas)]
    texto = plantilla.texto_una if cantidad <= 1 else plantilla.texto_varias
    texto = texto.format(n=cantidad, duracion=formatear_duracion(segundos_exceso))
    return RecomendacionGenerada(tipo=tipo, titulo=plantilla.titulo, texto=texto)


def generar_recomendaciones(
    recorrido_id: int, puntaje: Puntaje, eventos: Iterable[EventoPuntaje]
) -> list[RecomendacionGenerada]:
    """Recomendaciones del viaje, de la más importante a la menos.

    Sin eventos, una sola: la positiva. La variante sale del id del recorrido
    (estable y distinta de un viaje a otro).
    """
    eventos = list(eventos)
    conteo = Counter(e.tipo for e in eventos)
    tipos = ordenar_tipos(puntaje, conteo)
    if not tipos:
        return [_armar(TipoRecomendacion.sin_eventos, recorrido_id, 0, 0)]
    segundos_exceso = sum(
        e.duracion_s or 0 for e in eventos if e.tipo == TipoEvento.exceso_velocidad
    )
    return [
        _armar(TipoRecomendacion(tipo.value), recorrido_id, conteo[tipo], segundos_exceso)
        for tipo in tipos
    ]


def filas(
    recorrido_id: int, puntaje: Puntaje, eventos: Iterable[EventoPuntaje]
) -> list[Recomendacion]:
    """Filas a guardar: la principal (`orden` 0) y como máximo `MAXIMO_SECUNDARIAS`
    secundarias (1, 2…), una por cada otro tipo con eventos, en el mismo orden."""
    generadas = generar_recomendaciones(recorrido_id, puntaje, eventos)
    return [
        Recomendacion(
            recorrido_id=recorrido_id,
            orden=orden,
            tipo=generada.tipo,
            titulo=generada.titulo,
            texto=generada.texto,
        )
        for orden, generada in enumerate(generadas[: 1 + MAXIMO_SECUNDARIAS])
    ]


def _puntaje_guardado(recorrido: Recorrido) -> Puntaje:
    return Puntaje(
        drivescore=recorrido.drivescore,
        frenadas=recorrido.puntaje_frenadas,
        aceleraciones=recorrido.puntaje_aceleraciones,
        giros=recorrido.puntaje_giros,
        velocidad=recorrido.puntaje_velocidad,
    )


def faltantes(
    guardadas: list[Recomendacion], esperadas: list[Recomendacion]
) -> list[Recomendacion]:
    """De las esperadas, las que no están guardadas (por `orden`).

    Las guardadas no se tocan. Si la principal guardada es de un tipo que ahora
    sale como secundaria (p. ej. cambiaron las reglas), esa secundaria se omite
    para no repetirla.
    """
    ordenes = {r.orden for r in guardadas}
    principal = next((r for r in guardadas if r.orden == ORDEN_PRINCIPAL), None)
    return [
        r
        for r in esperadas
        if r.orden not in ordenes
        and not (
            principal is not None
            and r.orden != ORDEN_PRINCIPAL
            and r.tipo == principal.tipo
        )
    ]


async def obtener_recomendaciones(
    db: AsyncSession, recorrido: Recorrido, eventos: list[Evento] | None
) -> list[Recomendacion]:
    """Recomendaciones guardadas del recorrido por `orden` (la principal primero).

    Si faltan (finalizado antes de HU-18, o con HU-18 y sin secundarias), las
    genera y las guarda. Vacía sin puntaje: de esos viajes no se guardaron eventos.
    """
    if recorrido.drivescore is None or eventos is None:
        return []
    consulta = (
        select(Recomendacion)
        .where(Recomendacion.recorrido_id == recorrido.id)
        .order_by(Recomendacion.orden)
    )
    guardadas = list(await db.scalars(consulta))
    esperadas = filas(
        recorrido.id,
        _puntaje_guardado(recorrido),
        (EventoPuntaje(e.tipo, e.duracion_s) for e in eventos),
    )
    nuevas = faltantes(guardadas, esperadas)
    if not nuevas:
        return guardadas

    # Dos consultas simultáneas: la segunda no inserta y lee las de la primera
    await db.execute(
        insert(Recomendacion)
        .values(
            [
                dict(
                    recorrido_id=r.recorrido_id,
                    orden=r.orden,
                    tipo=r.tipo,
                    titulo=r.titulo,
                    texto=r.texto,
                )
                for r in nuevas
            ]
        )
        .on_conflict_do_nothing(constraint="uq_recomendaciones_recorrido_orden")
    )
    await db.commit()
    return list(await db.scalars(consulta))
