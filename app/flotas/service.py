import math
import secrets
from collections import Counter
from collections.abc import Iterable
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
    BaseTendencia,
    CategoriaPerfil,
    ConductorCreado,
    ConductorFlota,
    ConductorPerfil,
    ContraseniaTemporal,
    Direccion,
    EventoConductor,
    FranjaHoraria,
    MapaEventos,
    NivelRiesgo,
    OrdenConductores,
    PaginaConductores,
    PaginaEventos,
    PatronesEventos,
    PerfilConductor,
    Periodo,
    PuntoEvento,
    PuntoEvolucion,
    RegistroConductorFlotaEntrada,
    ResumenEventos,
    ResumenFlota,
    ViajeConductor,
    ZonaRepetida,
)
from app.puntaje.service import (
    Calificacion,
    ViajePuntuado,
    calcular_eventos_por_10km,
    calificar,
    promedio_desde_sumas,
    promedio_ponderado,
    resumir_historico,
)
from app.recomendaciones.service import ORDEN_RIESGO
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.recorridos.schemas import RecorridoDetalle
from app.recorridos.service import armar_detalle_finalizado
from app.telemetria.models import Evento, TipoEvento
from app.suscripciones.planes import Funcion, limitar_desde
from app.suscripciones.service import (
    exigir_cupo_conductor,
    exigir_funcion,
    limite_historial,
)
from app.telemetria.service import contar_por_tipo

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


def desde_visible(periodo: Periodo, ahora: datetime, empresa: Empresa) -> datetime | None:
    """Inicio del periodo recortado a la ventana de historial del plan de la empresa."""
    return limitar_desde(
        inicio_periodo(periodo, ahora, empresa.zona_horaria), limite_historial(empresa, ahora)
    )


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
    desde = desde_visible(periodo, datetime.now(UTC), admin.empresa)
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
    desde = desde_visible(periodo, datetime.now(UTC), admin.empresa)
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
    """Crea el conductor en la empresa del admin con una contraseña temporal propia. Respeta
    el límite de conductores del plan (403)."""
    await exigir_cupo_conductor(db, admin.empresa)
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


# --- Perfil de un conductor (HU-25) -------------------------------------------

_NIVEL_DE_CALIFICACION = {
    Calificacion.riesgoso: NivelRiesgo.alto,
    Calificacion.regular: NivelRiesgo.medio,
    Calificacion.muy_bueno: NivelRiesgo.bajo,
    Calificacion.excelente: NivelRiesgo.bajo,
}

# Categoría del puntaje <-> tipo de evento que la resta
_TIPO_DE_CATEGORIA = {
    "velocidad": TipoEvento.exceso_velocidad,
    "frenadas": TipoEvento.frenada_brusca,
    "giros": TipoEvento.giro_agresivo,
    "aceleraciones": TipoEvento.aceleracion_severa,
}

# Viajes que muestra el perfil como "recientes"
VIAJES_RECIENTES = 10


def nivel_de_riesgo(calificacion: Calificacion | None) -> NivelRiesgo | None:
    return None if calificacion is None else _NIVEL_DE_CALIFICACION[calificacion]


def punto_debil(promedios: dict[str, int], conteo: dict[TipoEvento, int]) -> TipoEvento | None:
    """Tipo de la categoria con menor puntaje entre los que tuvieron eventos; en empate,
    el de mayor riesgo (`ORDEN_RIESGO`). Mismo criterio que la recomendacion principal
    (HU-18). Sin eventos no hay punto debil."""
    con_eventos = [t for t in ORDEN_RIESGO if conteo.get(t, 0) > 0]
    if not con_eventos:
        return None
    puntaje_de = {_TIPO_DE_CATEGORIA[c]: p for c, p in promedios.items()}
    return min(con_eventos, key=lambda t: (puntaje_de[t], ORDEN_RIESGO.index(t)))


def tendencia_por_mitades(viajes: list[ViajePuntuado], hasta: datetime) -> int | None:
    """Tendencia del periodo "todo": DriveScore de la segunda mitad menos el de la primera
    (ponderados por distancia). El periodo va del primer viaje a `hasta` y se divide por
    tiempo. Nula si alguna de las dos mitades no tiene viajes."""
    if len(viajes) < 2:
        return None
    primero = min(v.fecha_inicio for v in viajes)
    medio = primero + (hasta - primero) / 2
    primera = [v for v in viajes if v.fecha_inicio < medio]
    segunda = [v for v in viajes if v.fecha_inicio >= medio]
    if not primera or not segunda:
        return None
    return promedio_ponderado(segunda) - promedio_ponderado(primera)


async def _conductor_de_la_empresa(db: AsyncSession, admin: Usuario, conductor_id: int) -> Usuario:
    """Un conductor activo de la empresa del admin; cualquier otro (de otra empresa,
    inactivo, un admin o inexistente) responde 404, sin distinguir cual es el caso."""
    conductor = await db.scalar(
        select(Usuario).where(Usuario.id == conductor_id, _de_la_empresa(admin.empresa_id))
    )
    if conductor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=CONDUCTOR_NO_ENCONTRADO)
    return conductor


def _con_puntaje(usuario_id: int) -> list:
    return [
        Recorrido.usuario_id == usuario_id,
        Recorrido.estado == EstadoRecorrido.finalizado,
        Recorrido.drivescore.is_not(None),
    ]


async def obtener_perfil(
    db: AsyncSession, admin: Usuario, conductor_id: int, periodo: Periodo
) -> PerfilConductor:
    """Desempeno del conductor en el periodo: DriveScore, riesgo, categorias, eventos y
    evolucion. Promedios ponderados por distancia, solo viajes finalizados con puntaje."""
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    zona = ZoneInfo(admin.empresa.zona_horaria)
    hasta = datetime.now(UTC)
    desde = inicio_periodo(periodo, hasta, admin.empresa.zona_horaria)
    limite = limite_historial(admin.empresa, hasta)

    # Con periodo, tambien se trae el anterior de igual duracion (para la tendencia); el
    # historial del plan recorta lo que se ve, tambien en "todo"
    condiciones = _con_puntaje(conductor.id)
    if desde is not None:
        condiciones.append(
            Recorrido.fecha_inicio >= limitar_desde(desde - (hasta - desde), limite)
        )
    elif limite is not None:
        condiciones.append(Recorrido.fecha_inicio >= limite)
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
        ).where(*condiciones)
    )
    viajes = [ViajePuntuado(*fila) for fila in filas]
    actuales = [v for v in viajes if desde is None or v.fecha_inicio >= desde]
    anteriores = [v for v in viajes if desde is not None and v.fecha_inicio < desde]

    # En "todo" el grafico empieza el dia (local) del primer viaje
    if desde is None and actuales:
        primero = min(v.fecha_inicio for v in actuales).astimezone(zona).date()
        inicio_grafico = datetime.combine(primero, time.min, tzinfo=zona).astimezone(UTC)
    else:
        inicio_grafico = desde or hasta
    historico = resumir_historico(actuales, anteriores, inicio_grafico, hasta, 0, None)
    if desde is None:
        tendencia, base = tendencia_por_mitades(actuales, hasta), BaseTendencia.mitades
    else:
        tendencia, base = historico.tendencia, BaseTendencia.periodo_anterior

    ids = [v.id for v in actuales]
    conteo_filas = []
    if ids:
        conteo_filas = (
            await db.execute(
                select(Evento.tipo, func.count())
                .where(Evento.recorrido_id.in_(ids))
                .group_by(Evento.tipo)
            )
        ).all()
    eventos_por_tipo = contar_por_tipo(conteo_filas)
    total_eventos = sum(eventos_por_tipo.values())
    distancia_m = sum(v.distancia_m for v in actuales)

    categorias = None
    debil = None
    if historico.categorias is not None:
        categorias = {
            c: CategoriaPerfil(promedio=p.promedio, diferencia=p.diferencia)
            for c, p in historico.categorias.items()
        }
        debil = punto_debil({c: p.promedio for c, p in categorias.items()}, eventos_por_tipo)

    return PerfilConductor(
        conductor=ConductorPerfil(id=conductor.id, nombre=conductor.nombre, email=conductor.email),
        viajes=len(actuales),
        distancia_m=distancia_m,
        drivescore=historico.promedio,
        calificacion=historico.calificacion,
        nivel_riesgo=nivel_de_riesgo(historico.calificacion),
        tendencia=tendencia,
        tendencia_base=base,
        categorias=categorias,
        punto_debil=debil,
        eventos_por_tipo=eventos_por_tipo,
        eventos=total_eventos,
        eventos_por_10km=calcular_eventos_por_10km(total_eventos, distancia_m),
        agrupacion=historico.agrupacion,
        evolucion=[
            PuntoEvolucion(fecha=p.fecha, drivescore=p.drivescore, viajes=p.viajes)
            for p in historico.puntos
        ],
    )


async def listar_viajes_recientes(
    db: AsyncSession, admin: Usuario, conductor_id: int, periodo: Periodo, limite: int
) -> list[ViajeConductor]:
    """Ultimos viajes finalizados con puntaje del conductor en el periodo, del mas reciente
    al mas antiguo."""
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    desde = desde_visible(periodo, datetime.now(UTC), admin.empresa)
    condiciones = _con_puntaje(conductor.id)
    if desde is not None:
        condiciones.append(Recorrido.fecha_inicio >= desde)
    recorridos = await db.scalars(
        select(Recorrido)
        .where(*condiciones)
        .order_by(Recorrido.fecha_inicio.desc(), Recorrido.id.desc())
        .limit(limite)
    )
    viajes = []
    for recorrido in recorridos:
        viaje = ViajeConductor.model_validate(recorrido)
        viaje.calificacion = calificar(recorrido.drivescore)
        viajes.append(viaje)
    return viajes


async def obtener_detalle_viaje(
    db: AsyncSession, admin: Usuario, conductor_id: int, recorrido_id: int
) -> RecorridoDetalle:
    """Detalle (ruta, eventos y desglose) de un viaje de un conductor de la empresa. Un
    viaje que no es de ese conductor responde 404."""
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    limite = limite_historial(admin.empresa, datetime.now(UTC))
    if limite is not None:
        # Un viaje fuera de la ventana del plan no se ve (los datos siguen guardados)
        inicio = await db.scalar(select(Recorrido.fecha_inicio).where(Recorrido.id == recorrido_id))
        if inicio is not None and inicio < limite:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recorrido no encontrado")
    return await armar_detalle_finalizado(db, conductor.id, recorrido_id)


# --- Historial de eventos de riesgo de un conductor (HU-26) --------------------

# Eventos del periodo que hacen falta para hablar de un patron (tipo, dia y franja)
MINIMO_EVENTOS_PATRON = 10
# Franjas del dia, en la zona horaria de la empresa: manana 06:00-11:59, tarde 12:00-18:59 y
# noche 19:00-05:59
HORA_INICIO_MANANA = 6
HORA_INICIO_TARDE = 12
HORA_INICIO_NOCHE = 19
_FRANJAS = (FranjaHoraria.manana, FranjaHoraria.tarde, FranjaHoraria.noche)
# Una zona agrupa eventos del mismo tipo a este radio o menos, desde este minimo de eventos
RADIO_ZONA_M = 50.0
MINIMO_EVENTOS_ZONA = 2
# Eventos que devuelve el mapa como maximo (los mas recientes)
MAXIMO_PUNTOS_MAPA = 1000
EVENTOS_POR_PAGINA = 10
_RADIO_TIERRA_M = 6_371_000


def franja_de(hora: int) -> FranjaHoraria:
    """Franja del dia a la que pertenece una hora (0 a 23)."""
    if HORA_INICIO_MANANA <= hora < HORA_INICIO_TARDE:
        return FranjaHoraria.manana
    if HORA_INICIO_TARDE <= hora < HORA_INICIO_NOCHE:
        return FranjaHoraria.tarde
    return FranjaHoraria.noche


def resumir_patrones(
    eventos: Iterable[tuple[TipoEvento, datetime]], zona: ZoneInfo
) -> ResumenEventos:
    """Cantidad de eventos por tipo, dia de la semana y franja, y lo que mas se repite.

    El dia y la franja se toman en la zona horaria de la empresa. Mientras haya menos de
    `MINIMO_EVENTOS_PATRON` eventos no se nombra ningun ganador. Empates: el tipo de
    mayor riesgo (`ORDEN_RIESGO`, como la HU-18); el dia mas cercano al lunes; la franja
    mas temprana.
    """
    por_tipo = dict.fromkeys(TipoEvento, 0)
    por_dia = [0] * 7
    por_franja = dict.fromkeys(_FRANJAS, 0)
    for tipo, fecha in eventos:
        local = fecha.astimezone(zona)
        por_tipo[tipo] += 1
        por_dia[local.isoweekday() - 1] += 1
        por_franja[franja_de(local.hour)] += 1

    total = sum(por_tipo.values())
    hay_patron = total >= MINIMO_EVENTOS_PATRON
    tipo = dia = franja = None
    if hay_patron:
        tipo = max(ORDEN_RIESGO, key=lambda t: (por_tipo[t], -ORDEN_RIESGO.index(t)))
        dia = max(range(7), key=lambda i: (por_dia[i], -i)) + 1
        franja = max(_FRANJAS, key=lambda f: (por_franja[f], -_FRANJAS.index(f)))
    return ResumenEventos(
        total=total,
        por_tipo=por_tipo,
        patrones=PatronesEventos(
            minimo=MINIMO_EVENTOS_PATRON,
            hay_patron=hay_patron,
            tipo_mas_frecuente=tipo,
            dia_mas_frecuente=dia,
            franja_mas_frecuente=franja,
            por_dia=por_dia,
            por_franja=por_franja,
        ),
    )


def distancia_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos coordenadas (haversine)."""
    f1, f2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((f2 - f1) / 2) ** 2
        + math.cos(f1) * math.cos(f2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * _RADIO_TIERRA_M * math.asin(math.sqrt(a))


def agrupar_zonas(puntos: Iterable[PuntoEvento]) -> list[ZonaRepetida]:
    """Zonas donde se repiten eventos del mismo tipo: cada evento entra en la primera zona
    de su tipo cuyo centro queda a `RADIO_ZONA_M` o menos (si no hay, abre una) y solo se
    devuelven las de `MINIMO_EVENTOS_ZONA` eventos o mas. Nunca mezcla tipos. El centro es
    el promedio de las posiciones de la zona."""
    zonas: dict[TipoEvento, list[list[float]]] = {}  # tipo -> [[suma lat, suma lon, cantidad]]
    for punto in puntos:
        candidatas = zonas.setdefault(punto.tipo, [])
        for zona in candidatas:
            cantidad = zona[2]
            if distancia_m(zona[0] / cantidad, zona[1] / cantidad, punto.lat, punto.lon) <= RADIO_ZONA_M:
                zona[0] += punto.lat
                zona[1] += punto.lon
                zona[2] += 1
                break
        else:
            candidatas.append([punto.lat, punto.lon, 1])
    return [
        ZonaRepetida(
            tipo=tipo,
            lat=suma_lat / cantidad,
            lon=suma_lon / cantidad,
            radio_m=RADIO_ZONA_M,
            eventos=int(cantidad),
        )
        for tipo, grupo in zonas.items()
        for suma_lat, suma_lon, cantidad in grupo
        if cantidad >= MINIMO_EVENTOS_ZONA
    ]


def _eventos_del_conductor(conductor_id: int, desde: datetime | None, tipo: TipoEvento | None) -> list:
    """Condiciones de los eventos de los viajes finalizados con puntaje (los unicos que
    guardan eventos) del conductor en el periodo, por la fecha de inicio del viaje: asi el
    total coincide con los `eventos` del perfil (HU-25)."""
    condiciones = [
        Recorrido.usuario_id == conductor_id,
        Recorrido.estado == EstadoRecorrido.finalizado,
        Recorrido.drivescore.is_not(None),
    ]
    if desde is not None:
        condiciones.append(Recorrido.fecha_inicio >= desde)
    if tipo is not None:
        condiciones.append(Evento.tipo == tipo)
    return condiciones


async def listar_eventos(
    db: AsyncSession,
    admin: Usuario,
    conductor_id: int,
    periodo: Periodo,
    tipo: TipoEvento | None,
    pagina: int,
    limite: int,
) -> PaginaEventos:
    """Historial de eventos del conductor, del mas reciente al mas antiguo. El dia y la hora
    van en la zona horaria de la empresa."""
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    zona = ZoneInfo(admin.empresa.zona_horaria)
    desde = desde_visible(periodo, datetime.now(UTC), admin.empresa)
    condiciones = _eventos_del_conductor(conductor.id, desde, tipo)

    base = select(Evento).join(Recorrido, Recorrido.id == Evento.recorrido_id).where(*condiciones)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    filas = await db.scalars(
        base.order_by(Evento.fecha.desc(), Evento.id.desc()).offset((pagina - 1) * limite).limit(limite)
    )
    eventos = []
    for evento in filas:
        local = evento.fecha.astimezone(zona)
        eventos.append(
            EventoConductor(
                id=evento.id,
                recorrido_id=evento.recorrido_id,
                tipo=evento.tipo,
                fecha=evento.fecha,
                dia=local.date(),
                hora=local.strftime("%H:%M"),
                intensidad=evento.intensidad,
                velocidad_kmh=evento.velocidad_kmh,
                duracion_s=evento.duracion_s,
                velocidad_maxima_kmh=evento.velocidad_maxima_kmh,
                lat=evento.lat,
                lon=evento.lon,
            )
        )
    return PaginaEventos(eventos=eventos, total=total or 0)


async def resumen_eventos(
    db: AsyncSession, admin: Usuario, conductor_id: int, periodo: Periodo
) -> ResumenEventos:
    """Eventos por tipo y patrones (tipo, dia y franja) del periodo, siempre con todos los
    tipos: el filtro por tipo del historial no los cambia."""
    exigir_funcion(admin.empresa, Funcion.eventos_mapa_patrones)
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    desde = desde_visible(periodo, datetime.now(UTC), admin.empresa)
    filas = await db.execute(
        select(Evento.tipo, Evento.fecha)
        .join(Recorrido, Recorrido.id == Evento.recorrido_id)
        .where(*_eventos_del_conductor(conductor.id, desde, None))
    )
    return resumir_patrones(((tipo, fecha) for tipo, fecha in filas), ZoneInfo(admin.empresa.zona_horaria))


async def mapa_eventos(
    db: AsyncSession, admin: Usuario, conductor_id: int, periodo: Periodo, tipo: TipoEvento | None
) -> MapaEventos:
    """Posicion de los eventos del periodo (los `MAXIMO_PUNTOS_MAPA` mas recientes) y las
    zonas donde se repiten eventos del mismo tipo."""
    exigir_funcion(admin.empresa, Funcion.eventos_mapa_patrones)
    conductor = await _conductor_de_la_empresa(db, admin, conductor_id)
    desde = desde_visible(periodo, datetime.now(UTC), admin.empresa)
    filas = await db.execute(
        select(Evento.id, Evento.recorrido_id, Evento.tipo, Evento.lat, Evento.lon, Evento.fecha)
        .join(Recorrido, Recorrido.id == Evento.recorrido_id)
        .where(*_eventos_del_conductor(conductor.id, desde, tipo))
        .order_by(Evento.fecha.desc(), Evento.id.desc())
        .limit(MAXIMO_PUNTOS_MAPA + 1)
    )
    puntos = [PuntoEvento(**fila._asdict()) for fila in filas]
    truncado = len(puntos) > MAXIMO_PUNTOS_MAPA
    puntos = puntos[:MAXIMO_PUNTOS_MAPA]
    return MapaEventos(puntos=puntos, zonas=agrupar_zonas(puntos), truncado=truncado)
