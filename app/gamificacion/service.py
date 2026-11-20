"""Nivel y logros (HU-21) y desafíos semanales (HU-22) del conductor.

Los puntos, los niveles, los logros y los desafíos se calculan con funciones
puras; `sincronizar` guarda lo que falte a partir del historial (también los
viajes anteriores a la HU), `obtener_progreso` arma la respuesta y
`obtener_desafios` genera (si faltan) y evalúa los desafíos de la semana.
"""

import enum
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Usuario
from app.core.errores import error_de_campo
from app.gamificacion.models import (
    CodigoDesafio,
    CodigoLogro,
    DesafioSemanal,
    Experiencia,
    FuenteExperiencia,
    LogroUsuario,
    TipoDesafio,
)
from app.puntaje.service import (
    CATEGORIAS_POR_RIESGO,
    Calificacion,
    PeriodoComparativo,
    ViajePuntuado,
    _redondear,
    calificar,
    promedio_ponderado,
    validar_inicio,
)
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento


class ParametrosExperiencia:
    """Puntos de un viaje: (base + por km × km, con tope) × multiplicador de la
    calificación. Con ellos, 7,4 km con DriveScore 86 dan 31."""

    PUNTOS_BASE = 10.0
    PUNTOS_POR_KM = 2.0
    # Un viaje muy largo no dispara los puntos
    KM_MAXIMO = 100.0
    MULTIPLICADOR = {
        Calificacion.excelente: 1.5,
        Calificacion.muy_bueno: 1.25,
        Calificacion.regular: 1.0,
        Calificacion.riesgoso: 0.5,
    }


def puntos_viaje(distancia_m: float, drivescore: int) -> int:
    """Puntos de experiencia de un viaje finalizado con puntaje (a mejor puntaje, más)."""
    p = ParametrosExperiencia
    km = min(distancia_m / 1000, p.KM_MAXIMO)
    return _redondear((p.PUNTOS_BASE + p.PUNTOS_POR_KM * km) * p.MULTIPLICADOR[calificar(drivescore)])


@dataclass(frozen=True)
class Nivel:
    numero: int
    nombre: str
    # Puntos acumulados para alcanzarlo
    puntos: int


NIVELES = (
    Nivel(1, "Novato", 0),
    Nivel(2, "Aprendiz", 100),
    Nivel(3, "Conductor", 300),
    Nivel(4, "Conductor seguro", 600),
    Nivel(5, "Experto", 1000),
    Nivel(6, "Maestro del volante", 1600),
    Nivel(7, "Leyenda", 2500),
)


def nivel_de(puntos: int) -> tuple[Nivel, Nivel | None]:
    """Nivel alcanzado con `puntos` y el siguiente (nulo en el máximo)."""
    indice = max(i for i, nivel in enumerate(NIVELES) if puntos >= nivel.puntos)
    siguiente = NIVELES[indice + 1] if indice + 1 < len(NIVELES) else None
    return NIVELES[indice], siguiente


class TipoProgreso(str, enum.Enum):
    """Qué cuenta el progreso de un logro; la app arma el texto ("Te faltan 3 viajes")."""

    viaje = "viaje"  # uno solo que cumpla la condición: 0 o 1
    viajes = "viajes"
    km = "km"
    racha = "racha"
    # Promedio semanal (desafío "Promedio muy bueno", HU-22)
    promedio = "promedio"


# DriveScore que cuenta para las rachas (Muy bueno o más) y para "Puntaje excelente"
DRIVESCORE_RACHA = 75
DRIVESCORE_EXCELENTE = 90


@dataclass(frozen=True)
class Logro:
    codigo: CodigoLogro
    nombre: str
    descripcion: str
    tipo: TipoProgreso
    meta: int


LOGROS = (
    Logro(CodigoLogro.primer_viaje, "Primer viaje", "Completa tu primer viaje.", TipoProgreso.viajes, 1),
    Logro(CodigoLogro.viaje_limpio, "Viaje limpio", "Haz un viaje sin eventos de riesgo.", TipoProgreso.viaje, 1),
    Logro(
        CodigoLogro.puntaje_excelente, "Puntaje excelente",
        "Logra un DriveScore de 90 o más en un viaje.", TipoProgreso.viaje, 1,
    ),
    Logro(
        CodigoLogro.buena_racha, "Buena racha",
        "Haz 5 viajes seguidos con DriveScore de 75 o más.", TipoProgreso.racha, 5,
    ),
    Logro(
        CodigoLogro.racha_imparable, "Racha imparable",
        "Haz 10 viajes seguidos con DriveScore de 75 o más.", TipoProgreso.racha, 10,
    ),
    Logro(CodigoLogro.constante, "Constante", "Completa 10 viajes.", TipoProgreso.viajes, 10),
    Logro(CodigoLogro.veterano, "Veterano", "Completa 50 viajes.", TipoProgreso.viajes, 50),
    Logro(CodigoLogro.cien_kilometros, "Cien kilómetros", "Recorre 100 km en total.", TipoProgreso.km, 100),
    Logro(CodigoLogro.gran_ruta, "Gran ruta", "Recorre 500 km en total.", TipoProgreso.km, 500),
)
LOGRO_POR_CODIGO = {logro.codigo: logro for logro in LOGROS}


@dataclass(frozen=True)
class ViajeLogro:
    """Lo que necesitan los puntos y los logros de cada viaje finalizado con puntaje."""

    id: int
    fecha_inicio: datetime
    fecha_fin: datetime
    distancia_m: float
    drivescore: int
    eventos: int


@dataclass(frozen=True)
class EstadoLogro:
    logro: Logro
    # Hasta la meta; en las rachas, la racha actual (al final de la serie)
    progreso: int
    # Primer viaje que cumplió la condición; nulos si no se cumplió
    recorrido_id: int | None = None
    fecha: datetime | None = None


def evaluar_logros(viajes: Iterable[ViajeLogro]) -> dict[CodigoLogro, EstadoLogro]:
    """Progreso de cada logro y, si se cumple, el primer viaje que lo cumplió.

    Recorre los viajes en orden de `fecha_inicio` llevando los acumulados.
    """
    cumplidos: dict[CodigoLogro, ViajeLogro] = {}
    cantidad = 0
    km = 0.0
    racha = 0
    # Logros de un solo viaje cuya condición ya se cumplió
    de_un_viaje: set[CodigoLogro] = set()

    def valor(logro: Logro) -> int:
        match logro.tipo:
            case TipoProgreso.viajes:
                return cantidad
            case TipoProgreso.km:
                return int(km)
            case TipoProgreso.racha:
                return racha
            case TipoProgreso.viaje:
                return 1 if logro.codigo in de_un_viaje else 0

    for viaje in sorted(viajes, key=lambda v: (v.fecha_inicio, v.id)):
        cantidad += 1
        km += viaje.distancia_m / 1000
        racha = racha + 1 if viaje.drivescore >= DRIVESCORE_RACHA else 0
        if viaje.eventos == 0:
            de_un_viaje.add(CodigoLogro.viaje_limpio)
        if viaje.drivescore >= DRIVESCORE_EXCELENTE:
            de_un_viaje.add(CodigoLogro.puntaje_excelente)
        for logro in LOGROS:
            if logro.codigo not in cumplidos and valor(logro) >= logro.meta:
                cumplidos[logro.codigo] = viaje

    estados = {}
    for logro in LOGROS:
        viaje = cumplidos.get(logro.codigo)
        progreso = logro.meta if viaje else min(valor(logro), logro.meta)
        estados[logro.codigo] = EstadoLogro(
            logro, progreso, viaje.id if viaje else None, viaje.fecha_fin if viaje else None
        )
    return estados


# --- Persistencia --------------------------------------------------------------


@dataclass(frozen=True)
class LogroDesbloqueado:
    codigo: CodigoLogro
    recorrido_id: int | None
    fecha: datetime


@dataclass(frozen=True)
class Sincronizacion:
    puntos: int
    # Puntos guardados en esta sincronización, por recorrido
    puntos_nuevos: dict[int, int]
    estados: dict[CodigoLogro, EstadoLogro]
    # Todos los desbloqueados (los guardados antes y los nuevos)
    desbloqueados: dict[CodigoLogro, LogroDesbloqueado]
    logros_nuevos: list[LogroDesbloqueado]

    @property
    def escribio(self) -> bool:
        return bool(self.puntos_nuevos or self.logros_nuevos)


def faltantes(
    viajes: list[ViajeLogro],
    con_experiencia: set[int],
    estados: dict[CodigoLogro, EstadoLogro],
    guardados: set[CodigoLogro],
) -> tuple[dict[int, int], list[LogroDesbloqueado]]:
    """Qué falta guardar: los puntos de los viajes sin experiencia y los logros
    cumplidos que no estaban. Los guardados no se tocan (nunca se pierden)."""
    puntos = {
        v.id: puntos_viaje(v.distancia_m, v.drivescore) for v in viajes if v.id not in con_experiencia
    }
    logros = [
        LogroDesbloqueado(codigo, estado.recorrido_id, estado.fecha)
        for codigo, estado in estados.items()
        if estado.recorrido_id is not None and codigo not in guardados
    ]
    return {id_: p for id_, p in puntos.items() if p > 0}, logros


async def sincronizar(db: AsyncSession, usuario_id: int) -> Sincronizacion:
    """Guarda los puntos y los logros que falten a partir del historial del conductor.

    No hace commit: al finalizar va en el mismo commit que el recorrido. Bloquea la
    fila del usuario para que una finalización y una consulta no calculen a la vez.
    """
    await db.execute(select(Usuario.id).where(Usuario.id == usuario_id).with_for_update())

    eventos = (
        select(func.count(Evento.id))
        .where(Evento.recorrido_id == Recorrido.id)
        .correlate(Recorrido)
        .scalar_subquery()
    )
    filas = await db.execute(
        select(
            Recorrido.id,
            Recorrido.fecha_inicio,
            Recorrido.fecha_fin,
            Recorrido.distancia_m,
            Recorrido.drivescore,
            eventos,
        ).where(
            Recorrido.usuario_id == usuario_id,
            Recorrido.estado == EstadoRecorrido.finalizado,
            Recorrido.drivescore.is_not(None),
            # Los simulados del modo pruebas no suman puntos ni logros
            Recorrido.simulado.is_(False),
        )
    )
    viajes = [ViajeLogro(*fila) for fila in filas]
    con_experiencia = set(
        await db.scalars(
            select(Experiencia.recorrido_id).where(
                Experiencia.usuario_id == usuario_id, Experiencia.recorrido_id.is_not(None)
            )
        )
    )
    guardados = {
        logro.codigo: LogroDesbloqueado(logro.codigo, logro.recorrido_id, logro.fecha_desbloqueo)
        for logro in await db.scalars(
            select(LogroUsuario).where(LogroUsuario.usuario_id == usuario_id)
        )
    }

    estados = evaluar_logros(viajes)
    puntos_nuevos, logros_nuevos = faltantes(viajes, con_experiencia, estados, set(guardados))
    fin_por_id = {v.id: v.fecha_fin for v in viajes}
    if puntos_nuevos:
        await db.execute(
            insert(Experiencia)
            .values([
                {
                    "usuario_id": usuario_id,
                    "fuente": FuenteExperiencia.recorrido,
                    "recorrido_id": id_,
                    "puntos": puntos,
                    "fecha": fin_por_id[id_],
                }
                for id_, puntos in puntos_nuevos.items()
            ])
            .on_conflict_do_nothing()
        )
    if logros_nuevos:
        await db.execute(
            insert(LogroUsuario)
            .values([
                {
                    "usuario_id": usuario_id,
                    "codigo": logro.codigo,
                    "recorrido_id": logro.recorrido_id,
                    "fecha_desbloqueo": logro.fecha,
                }
                for logro in logros_nuevos
            ])
            .on_conflict_do_nothing()
        )

    # Suma de todas las fuentes, con lo recién insertado
    total = await db.scalar(
        select(func.coalesce(func.sum(Experiencia.puntos), 0)).where(
            Experiencia.usuario_id == usuario_id
        )
    )
    return Sincronizacion(
        puntos=int(total),
        puntos_nuevos=puntos_nuevos,
        estados=estados,
        desbloqueados=guardados | {logro.codigo: logro for logro in logros_nuevos},
        logros_nuevos=logros_nuevos,
    )


@dataclass(frozen=True)
class Recompensa:
    """Lo que ganó un viaje recién finalizado (va en la respuesta del PATCH)."""

    # Los del viaje (los de los desafíos van en `desafios_completados` y `bono_semanal`)
    puntos: int
    subio_de_nivel: bool
    nivel: Nivel
    logros_nuevos: list[Logro]
    # Desafíos que completó este viaje, con sus puntos (HU-23)
    desafios_completados: Sequence["DesafioCompletado"] = ()
    # Puntos del bono por completar los 3 desafíos de la semana, si lo dio este viaje
    bono_semanal: int | None = None


def recompensa_de(
    sinc: Sincronizacion,
    recorrido_id: int,
    desafios: "ResultadoDesafios | None" = None,
) -> Recompensa:
    """Solo cuenta lo de este viaje: los logros que desbloqueó, los desafíos que
    completó (con el bono) y si todo eso hizo subir de nivel. Lo que se calculó de
    viajes anteriores se guarda sin avisar."""
    puntos = sinc.puntos_nuevos.get(recorrido_id, 0)
    de_desafios = desafios.puntos if desafios else 0
    nivel, _ = nivel_de(sinc.puntos + de_desafios)
    anterior, _ = nivel_de(sinc.puntos - puntos)
    return Recompensa(
        puntos=puntos,
        subio_de_nivel=nivel.numero > anterior.numero,
        nivel=nivel,
        logros_nuevos=[
            LOGRO_POR_CODIGO[logro.codigo]
            for logro in sinc.logros_nuevos
            if logro.recorrido_id == recorrido_id
        ],
        desafios_completados=desafios.completados if desafios else (),
        bono_semanal=desafios.bono if desafios else None,
    )


@dataclass(frozen=True)
class LogroConProgreso:
    codigo: CodigoLogro
    nombre: str
    descripcion: str
    tipo: TipoProgreso
    meta: int
    progreso: int
    desbloqueado: bool
    fecha_desbloqueo: datetime | None


@dataclass(frozen=True)
class Progreso:
    puntos: int
    nivel: Nivel
    siguiente_nivel: Nivel | None
    logros: list[LogroConProgreso]


def armar_progreso(sinc: Sincronizacion) -> Progreso:
    """Nivel y los logros en el orden del catálogo. Un logro guardado sigue
    desbloqueado aunque hoy no se cumpla (p. ej. si cambiara su condición)."""
    nivel, siguiente = nivel_de(sinc.puntos)
    logros = []
    for logro in LOGROS:
        guardado = sinc.desbloqueados.get(logro.codigo)
        estado = sinc.estados[logro.codigo]
        logros.append(
            LogroConProgreso(
                codigo=logro.codigo,
                nombre=logro.nombre,
                descripcion=logro.descripcion,
                tipo=logro.tipo,
                meta=logro.meta,
                progreso=logro.meta if guardado else estado.progreso,
                desbloqueado=guardado is not None,
                fecha_desbloqueo=guardado.fecha if guardado else None,
            )
        )
    return Progreso(sinc.puntos, nivel, siguiente, logros)


async def obtener_progreso(db: AsyncSession, usuario: Usuario) -> Progreso:
    """Nivel, puntos y logros del conductor; antes completa lo que falte (HU-21)."""
    sinc = await sincronizar(db, usuario.id)
    if sinc.escribio:
        await db.commit()
    return armar_progreso(sinc)


# --- Desafíos semanales (HU-22) -----------------------------------------------


DURACION_SEMANA = timedelta(days=7)
# Punto débil: categoría con peor promedio de las 4 semanas anteriores al lunes;
# con menos de 3 viajes en ese tramo, al azar
SEMANAS_PUNTO_DEBIL = 4
MINIMO_VIAJES_PUNTO_DEBIL = 3
# "Viaje perfecto" y los de punto débil solo cuentan viajes de 5 km o más
KM_MINIMO_VIAJE = 5.0
# "Promedio muy bueno": promedio semanal de 80 o más con al menos 3 viajes
PROMEDIO_MUY_BUENO = 80
MINIMO_VIAJES_PROMEDIO = 3

ORDEN_TIPOS_DESAFIO = (TipoDesafio.constancia, TipoDesafio.calidad, TipoDesafio.punto_debil)

# Bono por completar los 3 desafíos de la semana (HU-23), una vez por semana
BONO_SEMANA = 100


@dataclass(frozen=True)
class Desafio:
    codigo: CodigoDesafio
    tipo: TipoDesafio
    nombre: str
    descripcion: str
    progreso: TipoProgreso
    meta: int
    # Recompensa al completarlo (se entrega en la HU-23)
    puntos: int
    # Solo en los de punto débil: la categoría y el evento que no debe ocurrir
    categoria: str | None = None
    evento: TipoEvento | None = None


DESAFIOS = (
    Desafio(
        CodigoDesafio.semana_activa, TipoDesafio.constancia, "Semana activa",
        "Completa 5 viajes esta semana.", TipoProgreso.viajes, 5, 150,
    ),
    Desafio(
        CodigoDesafio.kilometros_seguros, TipoDesafio.constancia, "Kilómetros seguros",
        "Recorre 50 km esta semana.", TipoProgreso.km, 50, 150,
    ),
    Desafio(
        CodigoDesafio.viaje_perfecto, TipoDesafio.calidad, "Viaje perfecto",
        "Haz un viaje de 5 km o más sin eventos de riesgo.", TipoProgreso.viaje, 1, 200,
    ),
    Desafio(
        CodigoDesafio.racha_excelente, TipoDesafio.calidad, "Racha excelente",
        "Haz 3 viajes seguidos con DriveScore de 90 o más.", TipoProgreso.racha, 3, 250,
    ),
    Desafio(
        CodigoDesafio.promedio_muy_bueno, TipoDesafio.calidad, "Promedio muy bueno",
        "Logra un promedio semanal de 80 o más con al menos 3 viajes.",
        TipoProgreso.promedio, PROMEDIO_MUY_BUENO, 200,
    ),
    Desafio(
        CodigoDesafio.sin_frenadas_bruscas, TipoDesafio.punto_debil, "Sin frenadas bruscas",
        "Haz 3 viajes de 5 km o más sin frenadas bruscas.", TipoProgreso.viajes, 3, 200,
        "frenadas", TipoEvento.frenada_brusca,
    ),
    Desafio(
        CodigoDesafio.sin_aceleraciones_severas, TipoDesafio.punto_debil,
        "Sin aceleraciones severas",
        "Haz 3 viajes de 5 km o más sin aceleraciones severas.", TipoProgreso.viajes, 3, 200,
        "aceleraciones", TipoEvento.aceleracion_severa,
    ),
    Desafio(
        CodigoDesafio.sin_giros_agresivos, TipoDesafio.punto_debil, "Sin giros agresivos",
        "Haz 3 viajes de 5 km o más sin giros agresivos.", TipoProgreso.viajes, 3, 200,
        "giros", TipoEvento.giro_agresivo,
    ),
    Desafio(
        CodigoDesafio.sin_excesos_velocidad, TipoDesafio.punto_debil,
        "Sin excesos de velocidad",
        "Haz 3 viajes de 5 km o más sin excesos de velocidad.", TipoProgreso.viajes, 3, 200,
        "velocidad", TipoEvento.exceso_velocidad,
    ),
)
DESAFIO_POR_CODIGO = {desafio.codigo: desafio for desafio in DESAFIOS}
PUNTO_DEBIL_POR_CATEGORIA = {
    desafio.categoria: desafio for desafio in DESAFIOS if desafio.tipo == TipoDesafio.punto_debil
}


def punto_debil(historial: Sequence[ViajePuntuado], azar: random.Random) -> str:
    """Categoría con peor promedio (ponderado por distancia) del historial; en
    empate, la de mayor riesgo (`min` se queda con la primera). Con pocos viajes,
    al azar."""
    if len(historial) < MINIMO_VIAJES_PUNTO_DEBIL:
        return azar.choice(CATEGORIAS_POR_RIESGO)
    promedios = {c: promedio_ponderado(list(historial), c) for c in CATEGORIAS_POR_RIESGO}
    return min(CATEGORIAS_POR_RIESGO, key=lambda c: promedios[c])


def elegir_desafios(
    anteriores: set[CodigoDesafio], historial: Sequence[ViajePuntuado], azar: random.Random
) -> list[Desafio]:
    """Los 3 desafíos de una semana: constancia y calidad al azar sin repetir los de
    la semana anterior (`anteriores`) y el del punto débil."""
    elegidos = []
    for tipo in (TipoDesafio.constancia, TipoDesafio.calidad):
        del_tipo = [d for d in DESAFIOS if d.tipo == tipo]
        opciones = [d for d in del_tipo if d.codigo not in anteriores] or del_tipo
        elegidos.append(azar.choice(opciones))
    elegidos.append(PUNTO_DEBIL_POR_CATEGORIA[punto_debil(historial, azar)])
    return elegidos


@dataclass(frozen=True)
class ViajeDesafio:
    """Lo que necesita el avance de cada viaje finalizado con puntaje de la semana."""

    id: int
    fecha_inicio: datetime
    distancia_m: float
    drivescore: int
    eventos_por_tipo: dict[TipoEvento, int] = field(default_factory=dict)

    @property
    def eventos(self) -> int:
        return sum(self.eventos_por_tipo.values())

    @property
    def largo(self) -> bool:
        """5 km o más: los únicos que cuentan para "Viaje perfecto" y el punto débil."""
        return self.distancia_m / 1000 >= KM_MINIMO_VIAJE


@dataclass(frozen=True)
class AvanceDesafio:
    # Hasta la meta; en la racha, la actual; en el promedio, el promedio actual
    progreso: int
    # Una vez cumplido no se pierde, aunque después deje de cumplirse
    completado: bool
    # Solo en el promedio: el de la semana (nulo sin viajes) y cuántos viajes lleva
    promedio: int | None = None
    viajes: int | None = None


def evaluar_desafio(desafio: Desafio, meta: int, viajes: Iterable[ViajeDesafio]) -> AvanceDesafio:
    """Avance de un desafío con los viajes de su semana, recorridos en orden de
    `fecha_inicio`: en cuanto se cumple, queda completado."""
    ordenados = sorted(viajes, key=lambda v: (v.fecha_inicio, v.id))
    match desafio.codigo:
        case CodigoDesafio.semana_activa:
            cuenta = len(ordenados)
            return AvanceDesafio(min(cuenta, meta), cuenta >= meta)
        case CodigoDesafio.kilometros_seguros:
            km = sum(v.distancia_m for v in ordenados) / 1000
            return AvanceDesafio(min(int(km), meta), km >= meta)
        case CodigoDesafio.viaje_perfecto:
            cumplido = any(v.largo and v.eventos == 0 for v in ordenados)
            return AvanceDesafio(meta if cumplido else 0, cumplido)
        case CodigoDesafio.racha_excelente:
            racha = 0
            cumplido = False
            for v in ordenados:
                racha = racha + 1 if v.drivescore >= DRIVESCORE_EXCELENTE else 0
                cumplido = cumplido or racha >= meta
            return AvanceDesafio(meta if cumplido else racha, cumplido)
        case CodigoDesafio.promedio_muy_bueno:
            cumplido = any(
                promedio_ponderado(ordenados[:i]) >= meta
                for i in range(MINIMO_VIAJES_PROMEDIO, len(ordenados) + 1)
            )
            promedio = promedio_ponderado(ordenados)
            return AvanceDesafio(
                meta if cumplido else min(promedio or 0, meta),
                cumplido,
                promedio=promedio,
                viajes=len(ordenados),
            )
        case _:
            # Punto débil: viajes de 5 km o más sin eventos de su tipo
            cuenta = sum(
                1 for v in ordenados if v.largo and v.eventos_por_tipo.get(desafio.evento, 0) == 0
            )
            return AvanceDesafio(min(cuenta, meta), cuenta >= meta)


@dataclass(frozen=True)
class EstadoDesafio:
    codigo: CodigoDesafio
    tipo: TipoDesafio
    nombre: str
    descripcion: str
    tipo_progreso: TipoProgreso
    meta: int
    progreso: int
    completado: bool
    puntos: int
    categoria: str | None
    promedio: int | None
    viajes: int | None
    viajes_minimos: int | None


@dataclass(frozen=True)
class BonoSemana:
    puntos: int
    # Ya se entregó (con este viaje o antes)
    recibido: bool


@dataclass(frozen=True)
class SemanaDesafios:
    inicio: datetime
    fin: datetime
    desafios: list[EstadoDesafio]
    bono: BonoSemana


def armar_semana(
    filas: Sequence[DesafioSemanal], viajes: list[ViajeDesafio], bono_recibido: bool = False
) -> SemanaDesafios:
    """Los desafíos guardados de una semana con su avance, en orden de tipo.

    `completado` sale de `fecha_completado` (una vez completado no se pierde); el
    resto del avance, de los viajes de la semana."""
    ordenadas = sorted(filas, key=lambda f: ORDEN_TIPOS_DESAFIO.index(f.tipo))
    estados = []
    for fila in ordenadas:
        desafio = DESAFIO_POR_CODIGO[fila.codigo]
        avance = evaluar_desafio(desafio, fila.meta, viajes)
        completado = fila.fecha_completado is not None
        es_promedio = desafio.progreso == TipoProgreso.promedio
        estados.append(
            EstadoDesafio(
                codigo=fila.codigo,
                tipo=fila.tipo,
                nombre=desafio.nombre,
                descripcion=desafio.descripcion,
                tipo_progreso=desafio.progreso,
                meta=fila.meta,
                progreso=fila.meta if completado else avance.progreso,
                completado=completado,
                puntos=fila.puntos,
                categoria=desafio.categoria,
                promedio=avance.promedio,
                viajes=avance.viajes,
                viajes_minimos=MINIMO_VIAJES_PROMEDIO if es_promedio else None,
            )
        )
    return SemanaDesafios(
        ordenadas[0].semana_inicio,
        ordenadas[0].semana_fin,
        estados,
        BonoSemana(BONO_SEMANA, bono_recibido),
    )


# --- Completar desafíos y recompensa (HU-23) ----------------------------------


@dataclass(frozen=True)
class DesafioCompletado:
    """Un desafío que se completó, con los puntos que dio."""

    codigo: CodigoDesafio
    tipo: TipoDesafio
    nombre: str
    puntos: int
    # Solo en el punto débil
    categoria: str | None


def completado_de(fila: DesafioSemanal) -> DesafioCompletado:
    desafio = DESAFIO_POR_CODIGO[fila.codigo]
    return DesafioCompletado(fila.codigo, fila.tipo, desafio.nombre, fila.puntos, desafio.categoria)


def desafios_por_completar(
    filas: Sequence[DesafioSemanal], viajes: Sequence[ViajeDesafio]
) -> list[DesafioSemanal]:
    """Los desafíos sin completar cuya meta ya se alcanzó con los viajes de la semana.
    Los ya completados no se repiten: cada uno da su recompensa una sola vez."""
    return [
        fila
        for fila in filas
        if fila.fecha_completado is None
        and evaluar_desafio(DESAFIO_POR_CODIGO[fila.codigo], fila.meta, viajes).completado
    ]


def fila_del_bono(filas: Sequence[DesafioSemanal]) -> DesafioSemanal | None:
    """Con los 3 desafíos completados, el último que se completó (el bono se guarda
    con su id); si falta alguno, nulo. En empate de fecha, el de mayor id."""
    if len(filas) < len(ORDEN_TIPOS_DESAFIO) or any(f.fecha_completado is None for f in filas):
        return None
    return max(filas, key=lambda f: (f.fecha_completado, f.id or 0))


@dataclass(frozen=True)
class ResultadoDesafios:
    # Los de la semana en curso (vacía si el conductor aún no los tiene) y sus viajes
    filas: list[DesafioSemanal]
    viajes: list[ViajeDesafio]
    # Los que se completaron en esta sincronización
    completados: list[DesafioCompletado]
    # Puntos del bono si se entregó en esta sincronización
    bono: int | None
    # El bono de la semana ya está entregado (ahora o antes)
    bono_recibido: bool

    @property
    def puntos(self) -> int:
        return sum(c.puntos for c in self.completados) + (self.bono or 0)

    @property
    def escribio(self) -> bool:
        return bool(self.completados or self.bono)


async def completar_desafios(
    db: AsyncSession, usuario_id: int, ahora: datetime
) -> ResultadoDesafios:
    """Completa los desafíos de la semana en curso que ya llegaron a su meta y suma
    sus puntos (y el bono si quedan los 3) a la experiencia (HU-23).

    Solo cuenta la semana que contiene `ahora`: los desafíos de una semana que ya
    terminó vencieron sin recompensa. Los viajes descartados no cuentan. No hace
    commit; corre bajo el `FOR UPDATE` del usuario que toman `sincronizar` y
    `obtener_desafios`.
    """
    filas = list(
        await db.scalars(
            select(DesafioSemanal).where(
                DesafioSemanal.usuario_id == usuario_id,
                DesafioSemanal.semana_inicio <= ahora,
                DesafioSemanal.semana_fin > ahora,
            )
        )
    )
    if not filas:
        return ResultadoDesafios([], [], [], None, False)
    # Si un cambio de zona dejó dos semanas solapadas un rato, vale la más reciente
    inicio = max(f.semana_inicio for f in filas)
    filas = [f for f in filas if f.semana_inicio == inicio]
    viajes = await _viajes_semana(db, usuario_id, inicio, filas[0].semana_fin)

    nuevas = desafios_por_completar(filas, viajes)
    for fila in nuevas:
        fila.fecha_completado = ahora
    if nuevas:
        await db.execute(
            insert(Experiencia)
            .values([
                {
                    "usuario_id": usuario_id,
                    "fuente": FuenteExperiencia.desafio,
                    "desafio_id": fila.id,
                    "puntos": fila.puntos,
                    "fecha": ahora,
                }
                for fila in nuevas
            ])
            .on_conflict_do_nothing()
        )

    bono = None
    bono_recibido = False
    ultima = fila_del_bono(filas)
    if ultima is not None:
        bono_recibido = (
            await db.scalar(
                select(Experiencia.id).where(
                    Experiencia.fuente == FuenteExperiencia.bono_semana,
                    Experiencia.desafio_id.in_([f.id for f in filas]),
                )
            )
        ) is not None
        if not bono_recibido:
            await db.execute(
                insert(Experiencia)
                .values(
                    usuario_id=usuario_id,
                    fuente=FuenteExperiencia.bono_semana,
                    desafio_id=ultima.id,
                    puntos=BONO_SEMANA,
                    fecha=ahora,
                )
                .on_conflict_do_nothing()
            )
            bono = BONO_SEMANA
            bono_recibido = True
    return ResultadoDesafios(
        filas, viajes, [completado_de(f) for f in nuevas], bono, bono_recibido
    )


def validar_semana(desde: datetime, ahora: datetime) -> None:
    """`desde` debe ser el lunes 00:00 de la semana en curso, en la zona del teléfono."""
    if desde > ahora:
        raise error_de_campo("desde", "El inicio de la semana no puede estar en el futuro")
    validar_inicio(PeriodoComparativo.semana, desde)
    if ahora >= desde + DURACION_SEMANA:
        raise error_de_campo("desde", "El inicio debe ser el de la semana actual")


def _finalizados_con_puntaje(usuario_id: int, desde: datetime, hasta: datetime):
    return (
        Recorrido.usuario_id == usuario_id,
        Recorrido.estado == EstadoRecorrido.finalizado,
        Recorrido.drivescore.is_not(None),
        # Los simulados del modo pruebas no cuentan para los desafíos
        Recorrido.simulado.is_(False),
        Recorrido.fecha_inicio >= desde,
        Recorrido.fecha_inicio < hasta,
    )


async def _historial_punto_debil(
    db: AsyncSession, usuario_id: int, desde: datetime
) -> list[ViajePuntuado]:
    """Viajes con puntaje de las semanas anteriores a `desde`, con sus categorías."""
    filas = await db.execute(
        select(
            Recorrido.id,
            Recorrido.fecha_inicio,
            Recorrido.distancia_m,
            Recorrido.drivescore,
            Recorrido.puntaje_frenadas,
            Recorrido.puntaje_aceleraciones,
            Recorrido.puntaje_giros,
            Recorrido.puntaje_velocidad,
        ).where(
            *_finalizados_con_puntaje(
                usuario_id, desde - SEMANAS_PUNTO_DEBIL * DURACION_SEMANA, desde
            )
        )
    )
    return [ViajePuntuado(*fila) for fila in filas]


async def _viajes_semana(
    db: AsyncSession, usuario_id: int, desde: datetime, hasta: datetime
) -> list[ViajeDesafio]:
    """Viajes finalizados con puntaje de `[desde, hasta)` con sus eventos por tipo
    (los descartados no cuentan)."""
    viajes = list(
        await db.execute(
            select(
                Recorrido.id, Recorrido.fecha_inicio, Recorrido.distancia_m, Recorrido.drivescore
            ).where(*_finalizados_con_puntaje(usuario_id, desde, hasta))
        )
    )
    if not viajes:
        return []
    conteos: dict[int, dict[TipoEvento, int]] = {}
    for recorrido_id, tipo, cantidad in await db.execute(
        select(Evento.recorrido_id, Evento.tipo, func.count(Evento.id))
        .where(Evento.recorrido_id.in_([v.id for v in viajes]))
        .group_by(Evento.recorrido_id, Evento.tipo)
    ):
        conteos.setdefault(recorrido_id, {})[tipo] = cantidad
    return [
        ViajeDesafio(v.id, v.fecha_inicio, v.distancia_m, v.drivescore, conteos.get(v.id, {}))
        for v in viajes
    ]


async def obtener_desafios(
    db: AsyncSession, usuario: Usuario, desde: datetime, azar: random.Random | None = None
) -> SemanaDesafios:
    """Desafíos de la semana en curso con su avance (HU-22).

    Si el conductor aún no tiene los de esta semana, se generan con `desde` (el
    lunes 00:00 en la zona del teléfono) y se guardan; los guardados se usan tal
    cual hasta que termine su semana, aunque cambie la zona. Los que ya llegaron a
    su meta se completan y dan su recompensa (HU-23). Bloquea la fila del usuario
    para que dos consultas no los generen ni los premien a la vez.
    """
    ahora = datetime.now(UTC)
    validar_semana(desde, ahora)
    await db.execute(select(Usuario.id).where(Usuario.id == usuario.id).with_for_update())

    semana_actual = select(DesafioSemanal).where(
        DesafioSemanal.usuario_id == usuario.id,
        DesafioSemanal.semana_inicio <= ahora,
        DesafioSemanal.semana_fin > ahora,
    )
    generar = not list(await db.scalars(semana_actual))
    if generar:
        inicio_anterior = await db.scalar(
            select(func.max(DesafioSemanal.semana_inicio)).where(
                DesafioSemanal.usuario_id == usuario.id, DesafioSemanal.semana_inicio < desde
            )
        )
        anteriores = set()
        if inicio_anterior is not None:
            anteriores = set(
                await db.scalars(
                    select(DesafioSemanal.codigo).where(
                        DesafioSemanal.usuario_id == usuario.id,
                        DesafioSemanal.semana_inicio == inicio_anterior,
                    )
                )
            )
        historial = await _historial_punto_debil(db, usuario.id, desde)
        elegidos = elegir_desafios(anteriores, historial, azar or random.Random())
        await db.execute(
            insert(DesafioSemanal)
            .values([
                {
                    "usuario_id": usuario.id,
                    "semana_inicio": desde,
                    "semana_fin": desde + DURACION_SEMANA,
                    "tipo": d.tipo,
                    "codigo": d.codigo,
                    "meta": d.meta,
                    "puntos": d.puntos,
                }
                for d in elegidos
            ])
            .on_conflict_do_nothing()
        )

    # Completa lo que ya llegó a su meta (por ejemplo, si las filas se generaron
    # después de los viajes de la semana). Un solo commit, para no soltar el
    # bloqueo del usuario entre generar y completar
    resultado = await completar_desafios(db, usuario.id, ahora)
    if generar or resultado.escribio:
        await db.commit()
    return armar_semana(resultado.filas, resultado.viajes, resultado.bono_recibido)
