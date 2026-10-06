"""Modo pruebas (temporal): viajes de todos los conductores para revisar qué detecta
la app antes del despliegue. Se retira junto con todo el módulo."""

import csv
import io
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, exists, func, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.auth.models import RolUsuario, Usuario
from app.core.config import settings
from app.core.errores import error_de_campo
from app.pruebas.models import ResultadoValidacion, ValidacionEvento
from app.pruebas.schemas import (
    ConductorPruebas,
    DetallePruebas,
    EventoPruebas,
    FiltroEventos,
    PaginaRecorridosPruebas,
    RecorridoPruebas,
    ResumenPruebas,
    TesterPruebas,
    ValidacionSalida,
)
from app.puntaje.service import _redondear, calificar, promedio_desde_sumas
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.recorridos.service import cargar_eventos, contar_eventos
from app.telemetria.models import Evento, TipoEvento
from app.telemetria.service import contar_por_tipo

RECORRIDO_NO_ENCONTRADO = "Recorrido no encontrado"
EVENTO_NO_ENCONTRADO = "Evento no encontrado"

# Estados que se listan: un recorrido en curso todavía no tiene métricas
CERRADOS = (EstadoRecorrido.finalizado, EstadoRecorrido.descartado)


def es_cuenta_pruebas(usuario: Usuario) -> bool:
    """La cuenta compartida de pruebas: un admin de empresa con el correo de `PRUEBAS_CORREO`."""
    correo = settings.pruebas_correo.strip().lower()
    return bool(correo) and usuario.rol == RolUsuario.admin_empresa and usuario.email == correo


def exigir_cuenta_pruebas(usuario: Usuario) -> Usuario:
    if not es_cuenta_pruebas(usuario):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Solo la cuenta de pruebas puede usar esta función"
        )
    return usuario


def eventos_por_10km(eventos: int, distancia_m: float) -> float | None:
    """Eventos cada 10 km con un decimal (0,05 hacia arriba); nulo sin distancia."""
    if distancia_m <= 0:
        return None
    return _redondear(eventos * 100_000 / distancia_m) / 10


def _validar_desde(desde: datetime | None, hasta: datetime) -> None:
    if desde is not None and desde >= hasta:
        raise error_de_campo("desde", "El inicio del periodo no puede estar en el futuro")


_CONDUCTOR_ACTIVO = and_(Usuario.rol == RolUsuario.conductor, Usuario.activo.is_(True))


# --- Resumen -----------------------------------------------------------------


async def obtener_resumen(
    db: AsyncSession, desde: datetime | None, usuario_id: int | None
) -> ResumenPruebas:
    """KPIs de los viajes desde `desde` (o de siempre) hasta ahora, de todos los
    conductores o de uno. Con `desde`, también cuenta los viajes del periodo anterior
    de igual duración."""
    hasta = datetime.now(UTC)
    _validar_desde(desde, hasta)

    finalizado = Recorrido.estado == EstadoRecorrido.finalizado
    del_conductor = Recorrido.usuario_id == usuario_id if usuario_id is not None else true()
    en_periodo = and_(del_conductor, Recorrido.fecha_inicio < hasta)
    if desde is not None:
        en_periodo = and_(en_periodo, Recorrido.fecha_inicio >= desde)
    actual = and_(finalizado, en_periodo)
    con_puntaje = and_(actual, Recorrido.drivescore.is_not(None))

    columnas = [
        func.count().filter(actual),
        func.count(Recorrido.usuario_id.distinct()).filter(actual),
        func.coalesce(func.sum(Recorrido.distancia_m).filter(actual), 0),
        func.sum(Recorrido.drivescore * Recorrido.distancia_m).filter(con_puntaje),
        func.sum(Recorrido.distancia_m).filter(con_puntaje),
        func.count().filter(Recorrido.estado == EstadoRecorrido.descartado, en_periodo),
    ]
    if desde is not None:
        # El periodo anterior, de igual duración, en la misma consulta
        inicio_anterior = desde - (hasta - desde)
        columnas.append(
            func.count().filter(
                finalizado,
                del_conductor,
                Recorrido.fecha_inicio >= inicio_anterior,
                Recorrido.fecha_inicio < desde,
            )
        )
    fila = (await db.execute(select(*columnas))).one()
    viajes, testers, distancia_m, suma_ponderada, suma_distancia, descartados = fila[:6]
    viajes_anterior = fila[6] if desde is not None else None

    eventos = await db.scalar(
        select(func.count(Evento.id))
        .join(Recorrido, Recorrido.id == Evento.recorrido_id)
        .where(actual)
    )
    testers_registrados = await db.scalar(
        select(func.count()).select_from(Usuario).where(_CONDUCTOR_ACTIVO)
    )

    promedio = promedio_desde_sumas(suma_ponderada, suma_distancia)
    return ResumenPruebas(
        testers=testers,
        testers_registrados=testers_registrados,
        viajes=viajes,
        viajes_anterior=viajes_anterior,
        descartados=descartados,
        distancia_m=distancia_m,
        drivescore_promedio=promedio,
        calificacion=calificar(promedio) if promedio is not None else None,
        eventos=eventos,
        eventos_por_10km=eventos_por_10km(eventos, suma_distancia or 0),
    )


# --- Listado y exportación --------------------------------------------------


def _filtros_recorridos(
    desde: datetime | None, usuario_id: int | None, eventos: FiltroEventos | None
) -> list:
    filtros = [Recorrido.estado.in_(CERRADOS)]
    if desde is not None:
        filtros.append(Recorrido.fecha_inicio >= desde)
    if usuario_id is not None:
        filtros.append(Recorrido.usuario_id == usuario_id)
    if eventos is not None:
        tiene_eventos = exists().where(Evento.recorrido_id == Recorrido.id)
        if eventos == FiltroEventos.con:
            filtros.append(tiene_eventos)
        else:
            # Sin puntaje (descartado o anterior a HU-15) no se guardaron eventos: no
            # se sabe si los hubo
            filtros.extend([Recorrido.drivescore.is_not(None), ~tiene_eventos])
    return filtros


async def _armar_recorridos(db: AsyncSession, filas) -> list[RecorridoPruebas]:
    """Filas (recorrido, id, nombre, email) → salida, con su conteo de eventos por tipo
    en una sola consulta agrupada."""
    con_puntaje = [r.id for r, *_ in filas if r.drivescore is not None]
    conteos: dict[int, list[tuple[TipoEvento, int]]] = defaultdict(list)
    if con_puntaje:
        agrupados = await db.execute(
            select(Evento.recorrido_id, Evento.tipo, func.count())
            .where(Evento.recorrido_id.in_(con_puntaje))
            .group_by(Evento.recorrido_id, Evento.tipo)
        )
        for recorrido_id, tipo, cantidad in agrupados:
            conteos[recorrido_id].append((tipo, cantidad))

    return [
        RecorridoPruebas(
            id=recorrido.id,
            estado=recorrido.estado,
            conductor=ConductorPruebas(id=usuario_id, nombre=nombre, email=email),
            fecha_inicio=recorrido.fecha_inicio,
            fecha_fin=recorrido.fecha_fin,
            distancia_m=recorrido.distancia_m,
            duracion_s=recorrido.duracion_s,
            velocidad_maxima_kmh=recorrido.velocidad_maxima_kmh,
            velocidad_promedio_kmh=recorrido.velocidad_promedio_kmh,
            drivescore=recorrido.drivescore,
            eventos_por_tipo=(
                contar_por_tipo(conteos[recorrido.id]) if recorrido.drivescore is not None else None
            ),
            dispositivo_modelo=recorrido.dispositivo_modelo,
            dispositivo_android=recorrido.dispositivo_android,
            version_app=recorrido.version_app,
        )
        for recorrido, usuario_id, nombre, email in filas
    ]


def _consulta_recorridos(filtros: list):
    return (
        select(Recorrido, Usuario.id, Usuario.nombre, Usuario.email)
        .join(Usuario, Usuario.id == Recorrido.usuario_id)
        .where(*filtros)
        # Del más reciente al más antiguo; el id desempata
        .order_by(Recorrido.fecha_inicio.desc(), Recorrido.id.desc())
    )


async def listar_recorridos(
    db: AsyncSession,
    desde: datetime | None,
    usuario_id: int | None,
    eventos: FiltroEventos | None,
    pagina: int,
    limite: int,
) -> PaginaRecorridosPruebas:
    """Recorridos finalizados y descartados, paginados por número de página (el panel
    muestra "Mostrando X de N" con Anterior y Siguiente)."""
    _validar_desde(desde, datetime.now(UTC))
    filtros = _filtros_recorridos(desde, usuario_id, eventos)

    total = await db.scalar(select(func.count()).select_from(Recorrido).where(*filtros))
    filas = (
        await db.execute(_consulta_recorridos(filtros).offset((pagina - 1) * limite).limit(limite))
    ).all()
    return PaginaRecorridosPruebas(recorridos=await _armar_recorridos(db, filas), total=total)


COLUMNAS_CSV = (
    "id",
    "estado",
    "tester",
    "correo",
    "dispositivo",
    "android",
    "version_app",
    "fecha_inicio",
    "fecha_fin",
    "duracion_min",
    "distancia_km",
    "velocidad_maxima_kmh",
    "velocidad_promedio_kmh",
    "frenadas",
    "aceleraciones",
    "giros",
    "excesos",
    "drivescore",
    "calificacion",
)


def fila_csv(recorrido: RecorridoPruebas) -> list:
    """Una fila del CSV; los conteos van vacíos si el recorrido no tiene puntaje."""
    conteo = recorrido.eventos_por_tipo or {}
    return [
        recorrido.id,
        recorrido.estado.value,
        recorrido.conductor.nombre,
        recorrido.conductor.email,
        recorrido.dispositivo_modelo or "",
        recorrido.dispositivo_android or "",
        recorrido.version_app or "",
        recorrido.fecha_inicio.isoformat(),
        recorrido.fecha_fin.isoformat(),
        round(recorrido.duracion_s / 60, 1),
        round(recorrido.distancia_m / 1000, 2),
        round(recorrido.velocidad_maxima_kmh, 1),
        round(recorrido.velocidad_promedio_kmh, 1),
        *(conteo.get(tipo, "") for tipo in TipoEvento),
        "" if recorrido.drivescore is None else recorrido.drivescore,
        "" if recorrido.calificacion is None else recorrido.calificacion.value,
    ]


async def exportar_recorridos(
    db: AsyncSession,
    desde: datetime | None,
    usuario_id: int | None,
    eventos: FiltroEventos | None,
) -> str:
    """CSV con todos los recorridos que cumplen los filtros (UTF-8 con BOM para Excel)."""
    _validar_desde(desde, datetime.now(UTC))
    filas = (
        await db.execute(_consulta_recorridos(_filtros_recorridos(desde, usuario_id, eventos)))
    ).all()
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(COLUMNAS_CSV)
    for recorrido in await _armar_recorridos(db, filas):
        escritor.writerow(fila_csv(recorrido))
    return "﻿" + salida.getvalue()


# --- Detalle y validaciones -------------------------------------------------

# Campos del recorrido que copia el detalle (el resto sale del conductor y los eventos)
_CAMPOS_RECORRIDO = tuple(
    campo
    for campo in DetallePruebas.model_fields
    if campo not in ("conductor", "eventos", "eventos_por_tipo")
)


async def obtener_detalle(db: AsyncSession, recorrido_id: int) -> DetallePruebas:
    """Detalle de un recorrido finalizado o descartado de cualquier conductor, con la
    marca (correcto o falso) de cada evento. Uno en curso o inexistente responde 404."""
    fila = (
        await db.execute(
            select(Recorrido, Usuario.id, Usuario.nombre, Usuario.email)
            .join(Usuario, Usuario.id == Recorrido.usuario_id)
            .where(Recorrido.id == recorrido_id, Recorrido.estado.in_(CERRADOS))
            .options(undefer(Recorrido.ruta))
        )
    ).one_or_none()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=RECORRIDO_NO_ENCONTRADO)
    recorrido, usuario_id, nombre, email = fila

    detalle = DetallePruebas.model_validate(
        {
            **{campo: getattr(recorrido, campo) for campo in _CAMPOS_RECORRIDO},
            "conductor": ConductorPruebas(id=usuario_id, nombre=nombre, email=email),
        }
    )
    eventos = await cargar_eventos(db, recorrido)
    if eventos is not None:
        validaciones = dict(
            (
                await db.execute(
                    select(ValidacionEvento.evento_id, ValidacionEvento.resultado).where(
                        ValidacionEvento.evento_id.in_([e.id for e in eventos])
                    )
                )
            ).all()
        )
        detalle.eventos = [
            EventoPruebas.model_validate(e).model_copy(update={"validacion": validaciones.get(e.id)})
            for e in eventos
        ]
        detalle.eventos_por_tipo = contar_eventos(eventos)
    return detalle


async def _exigir_evento(db: AsyncSession, evento_id: int) -> None:
    if await db.get(Evento, evento_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=EVENTO_NO_ENCONTRADO)


async def marcar_evento(
    db: AsyncSession, evento_id: int, resultado: ResultadoValidacion
) -> ValidacionSalida:
    """Marca un evento como correcto o falso; si ya tenía marca, la reemplaza."""
    await _exigir_evento(db, evento_id)
    await db.execute(
        insert(ValidacionEvento)
        .values(evento_id=evento_id, resultado=resultado)
        .on_conflict_do_update(
            index_elements=[ValidacionEvento.evento_id],
            set_={"resultado": resultado, "fecha": func.now()},
        )
    )
    await db.commit()
    return ValidacionSalida(evento_id=evento_id, resultado=resultado)


async def desmarcar_evento(db: AsyncSession, evento_id: int) -> None:
    """Quita la marca de un evento (sin marca no hace nada)."""
    await _exigir_evento(db, evento_id)
    await db.execute(delete(ValidacionEvento).where(ValidacionEvento.evento_id == evento_id))
    await db.commit()


# --- Testers ----------------------------------------------------------------


async def listar_testers(db: AsyncSession, desde: datetime | None) -> list[TesterPruebas]:
    """Conductores activos con los totales de sus viajes finalizados del periodo; los que
    no tienen viajes también aparecen. Del que viajó más recientemente al que menos."""
    _validar_desde(desde, datetime.now(UTC))
    del_periodo = [Recorrido.estado == EstadoRecorrido.finalizado]
    if desde is not None:
        del_periodo.append(Recorrido.fecha_inicio >= desde)
    con_puntaje = Recorrido.drivescore.is_not(None)

    viajes = (
        select(
            Recorrido.usuario_id,
            func.count().label("viajes"),
            func.sum(Recorrido.distancia_m).label("distancia_m"),
            func.sum(Recorrido.drivescore * Recorrido.distancia_m)
            .filter(con_puntaje)
            .label("suma_ponderada"),
            func.sum(Recorrido.distancia_m).filter(con_puntaje).label("suma_distancia"),
            func.max(Recorrido.fecha_inicio).label("ultimo_viaje"),
        )
        .where(*del_periodo)
        .group_by(Recorrido.usuario_id)
        .subquery()
    )
    eventos = (
        select(Recorrido.usuario_id, func.count(Evento.id).label("eventos"))
        .join(Evento, Evento.recorrido_id == Recorrido.id)
        .where(*del_periodo)
        .group_by(Recorrido.usuario_id)
        .subquery()
    )
    filas = await db.execute(
        select(
            Usuario.id,
            Usuario.nombre,
            Usuario.email,
            viajes.c.viajes,
            viajes.c.distancia_m,
            viajes.c.suma_ponderada,
            viajes.c.suma_distancia,
            viajes.c.ultimo_viaje,
            eventos.c.eventos,
        )
        .outerjoin(viajes, viajes.c.usuario_id == Usuario.id)
        .outerjoin(eventos, eventos.c.usuario_id == Usuario.id)
        .where(_CONDUCTOR_ACTIVO)
        .order_by(viajes.c.ultimo_viaje.desc().nulls_last(), Usuario.nombre)
    )
    testers = []
    for fila in filas:
        distancia_m = fila.distancia_m or 0
        cantidad_eventos = fila.eventos or 0
        testers.append(
            TesterPruebas(
                id=fila.id,
                nombre=fila.nombre,
                email=fila.email,
                viajes=fila.viajes or 0,
                distancia_m=distancia_m,
                drivescore_promedio=promedio_desde_sumas(fila.suma_ponderada, fila.suma_distancia),
                eventos=cantidad_eventos,
                eventos_por_10km=eventos_por_10km(cantidad_eventos, fila.suma_distancia or 0),
                ultimo_viaje=fila.ultimo_viaje,
            )
        )
    return testers
