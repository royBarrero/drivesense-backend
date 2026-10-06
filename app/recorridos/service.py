from collections import Counter
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RolUsuario, Usuario
from app.core.errores import error_de_campo
from app.puntaje.service import EventoPuntaje, calcular_puntaje
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento
from app.telemetria.schemas import EventoSalida
from app.telemetria.service import contar_por_tipo
from app.recorridos.schemas import (
    TOLERANCIA_RELOJ,
    FinalizarRecorridoEntrada,
    IniciarRecorridoEntrada,
    PaginaHistorial,
    RecorridoDetalle,
    RecorridoHistorial,
    ResumenPeriodo,
)

# Por debajo de cualquiera de estos mínimos el recorrido se descarta
DURACION_MINIMA_S = 60
DISTANCIA_MINIMA_M = 200

RECORRIDO_EN_CURSO = "Ya tienes un recorrido en curso"
RECORRIDO_NO_ENCONTRADO = "Recorrido no encontrado"


def exigir_conductor(usuario: Usuario) -> Usuario:
    if usuario.rol != RolUsuario.conductor:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Solo los conductores pueden registrar recorridos"
        )
    return usuario


async def obtener_recorrido_activo(db: AsyncSession, usuario: Usuario) -> Recorrido | None:
    return await db.scalar(
        select(Recorrido).where(
            Recorrido.usuario_id == usuario.id, Recorrido.estado == EstadoRecorrido.en_curso
        )
    )


async def iniciar_recorrido(
    db: AsyncSession, usuario: Usuario, datos: IniciarRecorridoEntrada
) -> Recorrido:
    """Crea el recorrido en curso del conductor (HU-04)."""
    if await obtener_recorrido_activo(db, usuario) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=RECORRIDO_EN_CURSO)

    recorrido = Recorrido(
        usuario_id=usuario.id,
        # Empresa del conductor en este momento (nula si es individual)
        empresa_id=usuario.empresa_id,
        estado=EstadoRecorrido.en_curso,
        fecha_inicio=datetime.now(UTC),
        lat_inicio=datos.lat_inicio,
        lon_inicio=datos.lon_inicio,
        dispositivo_modelo=datos.dispositivo_modelo,
        dispositivo_android=datos.dispositivo_android,
        version_app=datos.version_app,
    )
    db.add(recorrido)
    try:
        await db.commit()
    except IntegrityError:
        # Otra petición inició un recorrido entre la consulta y el commit (índice único parcial)
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=RECORRIDO_EN_CURSO)

    await db.refresh(recorrido)  # trae fecha_creacion (server_default)
    return recorrido


async def finalizar_recorrido(
    db: AsyncSession, usuario: Usuario, recorrido_id: int, datos: FinalizarRecorridoEntrada
) -> Recorrido:
    """Cierra el recorrido con sus métricas; lo descarta si es demasiado corto (HU-05).

    Si queda finalizado, guarda sus eventos y su puntaje (HU-15) en el mismo commit.
    """
    # FOR UPDATE: dos finalizaciones simultáneas no pueden cerrar el mismo recorrido
    recorrido = await db.scalar(
        select(Recorrido)
        .where(Recorrido.id == recorrido_id, Recorrido.usuario_id == usuario.id)
        .with_for_update()
    )
    # Un recorrido ajeno responde igual que uno inexistente
    if recorrido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=RECORRIDO_NO_ENCONTRADO)
    if recorrido.estado != EstadoRecorrido.en_curso:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Este recorrido ya fue finalizado")
    if datos.fecha_fin <= recorrido.fecha_inicio:
        raise error_de_campo("fecha_fin", "La fecha de fin debe ser posterior a la de inicio")
    # Hora del teléfono contra la del servidor: con la misma tolerancia que fecha_fin
    desde = recorrido.fecha_inicio - TOLERANCIA_RELOJ
    hasta = datos.fecha_fin + TOLERANCIA_RELOJ
    if any(not desde <= evento.fecha <= hasta for evento in datos.eventos):
        raise error_de_campo("eventos", "Hay eventos fuera del horario del viaje")

    es_corto = datos.duracion_s < DURACION_MINIMA_S or datos.distancia_m < DISTANCIA_MINIMA_M
    recorrido.estado = EstadoRecorrido.descartado if es_corto else EstadoRecorrido.finalizado
    recorrido.fecha_fin = datos.fecha_fin
    recorrido.distancia_m = datos.distancia_m
    recorrido.duracion_s = datos.duracion_s
    recorrido.velocidad_maxima_kmh = datos.velocidad_maxima_kmh
    recorrido.velocidad_promedio_kmh = datos.velocidad_promedio_kmh
    recorrido.lat_fin = datos.lat_fin
    recorrido.lon_fin = datos.lon_fin
    # La ruta solo se conserva si el viaje cuenta (HU-08)
    if not es_corto and datos.ruta is not None:
        recorrido.ruta = [punto.model_dump(mode="json") for punto in datos.ruta]
    # Eventos y puntaje, también solo si el viaje cuenta (HU-15)
    if not es_corto:
        db.add_all(
            Evento(recorrido_id=recorrido.id, **evento.model_dump()) for evento in datos.eventos
        )
        puntaje = calcular_puntaje(
            datos.distancia_m,
            datos.duracion_s,
            (EventoPuntaje(e.tipo, e.duracion_s) for e in datos.eventos),
        )
        recorrido.drivescore = puntaje.drivescore
        recorrido.puntaje_frenadas = puntaje.frenadas
        recorrido.puntaje_aceleraciones = puntaje.aceleraciones
        recorrido.puntaje_giros = puntaje.giros
        recorrido.puntaje_velocidad = puntaje.velocidad
    await db.commit()
    return recorrido


async def listar_historial(
    db: AsyncSession,
    usuario: Usuario,
    desde: datetime | None,
    antes_de: int | None,
    limite: int,
) -> PaginaHistorial:
    """Recorridos finalizados del conductor, del más reciente al más antiguo (HU-06).

    Paginación por cursor: `antes_de` es el id del último recorrido de la página
    anterior. Para un mismo conductor el orden por id coincide con el de
    `fecha_inicio` (solo puede tener uno en curso y `fecha_inicio` es la hora del
    servidor al crearlo), y un viaje nuevo siempre tiene un id mayor: al paginar
    no se repiten ni se saltan viajes aunque se agregue uno mientras se navega.
    """
    filtros = [
        Recorrido.usuario_id == usuario.id,
        Recorrido.estado == EstadoRecorrido.finalizado,
    ]
    if desde is not None:
        filtros.append(Recorrido.fecha_inicio >= desde)

    consulta = select(Recorrido).where(*filtros).order_by(Recorrido.id.desc())
    if antes_de is not None:
        consulta = consulta.where(Recorrido.id < antes_de)
    # Una fila de más para saber si hay otra página
    filas = list(await db.scalars(consulta.limit(limite + 1)))
    hay_mas = len(filas) > limite
    recorridos = filas[:limite]

    resumen = None
    if antes_de is None:
        viajes, distancia_m, duracion_s = (
            await db.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(Recorrido.distancia_m), 0),
                    func.coalesce(func.sum(Recorrido.duracion_s), 0),
                ).where(*filtros)
            )
        ).one()
        resumen = ResumenPeriodo(
            viajes=viajes, distancia_m=distancia_m, duracion_s=duracion_s
        )

    return PaginaHistorial(
        recorridos=[RecorridoHistorial.model_validate(r) for r in recorridos],
        siguiente=recorridos[-1].id if hay_mas else None,
        resumen=resumen,
    )


async def obtener_recorrido_finalizado(
    db: AsyncSession, usuario: Usuario, recorrido_id: int
) -> RecorridoDetalle:
    """Detalle de un recorrido del historial (HU-06), con sus eventos por tipo (HU-16)
    y la lista de eventos para el mapa (HU-29).

    Uno ajeno, inexistente, en curso o descartado responde 404: no es parte del historial.
    """
    recorrido = await db.scalar(
        select(Recorrido)
        .where(
            Recorrido.id == recorrido_id,
            Recorrido.usuario_id == usuario.id,
            Recorrido.estado == EstadoRecorrido.finalizado,
        )
        .options(undefer(Recorrido.ruta))
    )
    if recorrido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=RECORRIDO_NO_ENCONTRADO)

    detalle = RecorridoDetalle.model_validate(recorrido)
    eventos = await cargar_eventos(db, recorrido)
    if eventos is not None:
        detalle.eventos = [EventoSalida.model_validate(e) for e in eventos]
        detalle.eventos_por_tipo = contar_eventos(eventos)
    return detalle


async def cargar_eventos(db: AsyncSession, recorrido: Recorrido) -> list[Evento] | None:
    """Eventos del recorrido en orden cronológico; nulo si no tiene puntaje.

    Sin puntaje (finalizado antes de HU-15, o descartado) no se guardaron sus
    eventos: una lista vacía o un conteo en 0 serían engañosos.
    """
    if recorrido.drivescore is None:
        return None
    return list(
        await db.scalars(
            select(Evento)
            .where(Evento.recorrido_id == recorrido.id)
            .order_by(Evento.fecha, Evento.id)
        )
    )


def contar_eventos(eventos: list[Evento]) -> dict[TipoEvento, int]:
    return contar_por_tipo(Counter(e.tipo for e in eventos).items())
