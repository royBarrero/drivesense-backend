from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RolUsuario, Usuario
from app.core.errores import error_de_campo
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.recorridos.schemas import FinalizarRecorridoEntrada, IniciarRecorridoEntrada

# Por debajo de cualquiera de estos mínimos el recorrido se descarta
DURACION_MINIMA_S = 60
DISTANCIA_MINIMA_M = 200

RECORRIDO_EN_CURSO = "Ya tienes un recorrido en curso"


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
    """Cierra el recorrido con sus métricas; lo descarta si es demasiado corto (HU-05)."""
    # FOR UPDATE: dos finalizaciones simultáneas no pueden cerrar el mismo recorrido
    recorrido = await db.scalar(
        select(Recorrido)
        .where(Recorrido.id == recorrido_id, Recorrido.usuario_id == usuario.id)
        .with_for_update()
    )
    # Un recorrido ajeno responde igual que uno inexistente
    if recorrido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recorrido no encontrado")
    if recorrido.estado != EstadoRecorrido.en_curso:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Este recorrido ya fue finalizado")
    if datos.fecha_fin <= recorrido.fecha_inicio:
        raise error_de_campo("fecha_fin", "La fecha de fin debe ser posterior a la de inicio")

    es_corto = datos.duracion_s < DURACION_MINIMA_S or datos.distancia_m < DISTANCIA_MINIMA_M
    recorrido.estado = EstadoRecorrido.descartado if es_corto else EstadoRecorrido.finalizado
    recorrido.fecha_fin = datos.fecha_fin
    recorrido.distancia_m = datos.distancia_m
    recorrido.duracion_s = datos.duracion_s
    recorrido.velocidad_maxima_kmh = datos.velocidad_maxima_kmh
    recorrido.velocidad_promedio_kmh = datos.velocidad_promedio_kmh
    recorrido.lat_fin = datos.lat_fin
    recorrido.lon_fin = datos.lon_fin
    await db.commit()
    return recorrido
