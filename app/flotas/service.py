import secrets
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RolUsuario, Usuario
from app.auth.schemas import DatosUsuarioEntrada
from app.auth.service import insertar_usuario
from app.core.security import hashear_contrasenia
from app.flotas.models import Empresa
from app.flotas.schemas import (
    ConductorCreado,
    ConductorFlota,
    ContraseniaTemporal,
    Direccion,
    OrdenConductores,
    PaginaConductores,
    Periodo,
    RegistroConductorFlotaEntrada,
    ResumenFlota,
)
from app.puntaje.service import calificar, promedio_desde_sumas
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento

CONDUCTOR_NO_ENCONTRADO = "Conductor no encontrado"

_DIAS_PERIODO = {Periodo.siete_dias: 7, Periodo.treinta_dias: 30, Periodo.todo: None}

# Sin letras ni números que se confundan al dictarlos (0/O, 1/l/I)
_LETRAS = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
_DIGITOS = "23456789"
LARGO_CONTRASENIA_TEMPORAL = 10


def generar_contrasenia_temporal() -> str:
    """Contraseña aleatoria que cumple las reglas del registro (letra y número)."""
    while True:
        contrasenia = "".join(
            secrets.choice(_LETRAS + _DIGITOS) for _ in range(LARGO_CONTRASENIA_TEMPORAL)
        )
        if any(c in _LETRAS for c in contrasenia) and any(c in _DIGITOS for c in contrasenia):
            return contrasenia


def inicio_periodo(periodo: Periodo, ahora: datetime, zona_horaria: str) -> datetime | None:
    """Medianoche (en la zona de la empresa) del primer día del periodo, contando hoy;
    nulo para `todo`. Los 7 días son hoy y los 6 anteriores."""
    dias = _DIAS_PERIODO[periodo]
    if dias is None:
        return None
    zona = ZoneInfo(zona_horaria)
    primer_dia = ahora.astimezone(zona).date() - timedelta(days=dias - 1)
    return datetime.combine(primer_dia, time.min, tzinfo=zona).astimezone(UTC)


def _de_la_empresa(empresa_id: int):
    return and_(
        Usuario.empresa_id == empresa_id,
        Usuario.rol == RolUsuario.conductor,
        Usuario.activo.is_(True),
    )


def _viajes_del_periodo(desde: datetime | None) -> list:
    condiciones = [Recorrido.estado == EstadoRecorrido.finalizado]
    if desde is not None:
        condiciones.append(Recorrido.fecha_inicio >= desde)
    return condiciones


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def listar_conductores(
    db: AsyncSession,
    admin: Usuario,
    periodo: Periodo,
    busqueda: str | None,
    orden: OrdenConductores,
    direccion: Direccion,
    pagina: int,
    limite: int,
) -> PaginaConductores:
    """Conductores activos de la empresa del admin con los totales de sus viajes
    finalizados del periodo. Los que no tienen viajes también aparecen (al final)."""
    desde = inicio_periodo(periodo, datetime.now(UTC), admin.empresa.zona_horaria)
    con_puntaje = Recorrido.drivescore.is_not(None)

    viajes = (
        select(
            Recorrido.usuario_id,
            func.count().label("viajes"),
            func.sum(Recorrido.drivescore * Recorrido.distancia_m)
            .filter(con_puntaje)
            .label("suma_ponderada"),
            func.sum(Recorrido.distancia_m).filter(con_puntaje).label("suma_distancia"),
            func.max(Recorrido.fecha_inicio).label("ultimo_viaje"),
        )
        .where(*_viajes_del_periodo(desde))
        .group_by(Recorrido.usuario_id)
        .subquery()
    )

    filtro = _de_la_empresa(admin.empresa_id)
    if busqueda and busqueda.strip():
        patron = f"%{_escapar_like(busqueda.strip())}%"
        filtro = and_(
            filtro,
            (Usuario.nombre.ilike(patron, escape="\\")) | (Usuario.email.ilike(patron, escape="\\")),
        )

    total = await db.scalar(select(func.count(Usuario.id)).where(filtro))

    columna = {
        # cast: la división de enteros en PostgreSQL trunca
        OrdenConductores.drivescore: cast(viajes.c.suma_ponderada, Float)
        / func.nullif(viajes.c.suma_distancia, 0),
        OrdenConductores.viajes: func.coalesce(viajes.c.viajes, 0),
        OrdenConductores.ultimo_viaje: viajes.c.ultimo_viaje,
    }[orden]
    # Sin viajes siempre al final, sea cual sea el sentido
    orden_sql = (
        columna.asc().nulls_last() if direccion == Direccion.asc else columna.desc().nulls_last()
    )

    filas = await db.execute(
        select(
            Usuario.id,
            Usuario.nombre,
            Usuario.email,
            viajes.c.viajes,
            viajes.c.suma_ponderada,
            viajes.c.suma_distancia,
            viajes.c.ultimo_viaje,
        )
        .outerjoin(viajes, viajes.c.usuario_id == Usuario.id)
        .where(filtro)
        .order_by(orden_sql, Usuario.nombre, Usuario.id)
        .offset((pagina - 1) * limite)
        .limit(limite)
    )
    conductores = []
    for fila in filas:
        drivescore = promedio_desde_sumas(fila.suma_ponderada, fila.suma_distancia)
        conductores.append(
            ConductorFlota(
                id=fila.id,
                nombre=fila.nombre,
                email=fila.email,
                drivescore=drivescore,
                calificacion=calificar(drivescore) if drivescore is not None else None,
                viajes=fila.viajes or 0,
                ultimo_viaje=fila.ultimo_viaje,
            )
        )
    return PaginaConductores(conductores=conductores, total=total or 0)


async def obtener_resumen(db: AsyncSession, admin: Usuario, periodo: Periodo) -> ResumenFlota:
    """Totales de la flota: conductores activos, DriveScore promedio (ponderado por
    distancia, como el histórico) y eventos de riesgo de los viajes del periodo."""
    desde = inicio_periodo(periodo, datetime.now(UTC), admin.empresa.zona_horaria)
    de_la_empresa = _de_la_empresa(admin.empresa_id)
    del_periodo = _viajes_del_periodo(desde)
    con_puntaje = Recorrido.drivescore.is_not(None)

    conductores = await db.scalar(select(func.count(Usuario.id)).where(de_la_empresa))
    suma_ponderada, suma_distancia = (
        await db.execute(
            select(
                func.sum(Recorrido.drivescore * Recorrido.distancia_m).filter(con_puntaje),
                func.sum(Recorrido.distancia_m).filter(con_puntaje),
            )
            .join(Usuario, Usuario.id == Recorrido.usuario_id)
            .where(de_la_empresa, *del_periodo)
        )
    ).one()
    eventos = await db.scalar(
        select(func.count(Evento.id))
        .join(Recorrido, Recorrido.id == Evento.recorrido_id)
        .join(Usuario, Usuario.id == Recorrido.usuario_id)
        .where(de_la_empresa, *del_periodo)
    )

    drivescore = promedio_desde_sumas(suma_ponderada, suma_distancia)
    return ResumenFlota(
        conductores=conductores or 0,
        drivescore_promedio=drivescore,
        calificacion=calificar(drivescore) if drivescore is not None else None,
        eventos=eventos or 0,
    )


async def registrar_conductor(
    db: AsyncSession, admin: Usuario, datos: RegistroConductorFlotaEntrada
) -> ConductorCreado:
    """Crea el conductor en la empresa del admin con una contraseña temporal propia."""
    contrasenia = generar_contrasenia_temporal()
    usuario = await insertar_usuario(
        db,
        DatosUsuarioEntrada(
            nombre=datos.nombre,
            email=datos.email,
            telefono=datos.telefono,
            contrasenia=contrasenia,
        ),
        RolUsuario.conductor,
        admin.empresa,
        debe_cambiar_contrasenia=True,
    )
    return ConductorCreado(
        id=usuario.id,
        nombre=usuario.nombre,
        email=usuario.email,
        telefono=usuario.telefono,
        contrasenia_temporal=contrasenia,
    )


async def regenerar_contrasenia_temporal(
    db: AsyncSession, admin: Usuario, conductor_id: int
) -> ContraseniaTemporal:
    """Nueva contraseña temporal; la anterior deja de servir. Un conductor de otra empresa
    (o inactivo) responde 404, igual que uno inexistente."""
    conductor = await db.scalar(
        select(Usuario).where(Usuario.id == conductor_id, _de_la_empresa(admin.empresa_id))
    )
    if conductor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=CONDUCTOR_NO_ENCONTRADO)

    contrasenia = generar_contrasenia_temporal()
    conductor.contrasenia_hash = hashear_contrasenia(contrasenia)
    conductor.debe_cambiar_contrasenia = True
    await db.commit()
    return ContraseniaTemporal(contrasenia_temporal=contrasenia)
