"""Estilo de conducción de los conductores de flota (HU-25), con K-means local.

Cada conductor se resume en unas pocas variables de sus últimos 30 días (`VARIABLES`). Un
K-means (scikit-learn, sin servicios externos) agrupa a **todos** los conductores con datos
suficientes, de todas las empresas, para que los grupos tengan sentido; cada grupo recibe
un estilo con reglas fijas sobre su centro (`nombrar_grupo`). El último estilo de cada
conductor de flota se guarda en `estilos_conductor`, con la alerta si pasó a uno de mayor
riesgo. Las empresas solo ven la etiqueta de sus propios conductores, y la explicación
compara con el promedio de **su** flota.

Las funciones puras (variables, nombres, transición, explicación y tendencia) tienen
pruebas sin BD; `actualizar_estilos` entrena y guarda.
"""

import asyncio
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RolUsuario, Usuario
from app.flotas.models import Empresa, EstiloConduccion, EstiloConductor
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento

log = logging.getLogger(__name__)

# --- Parámetros -----------------------------------------------------------------

# Ventana: hoy y los 29 días anteriores, desde la medianoche en la zona de la empresa (igual
# que el periodo "30 días" y el historial del plan Gratis)
DIAS_VENTANA = 30
ZONA_SIN_EMPRESA = "America/La_Paz"
MINIMO_VIAJES_ESTILO = 3
MINIMO_DISTANCIA_ESTILO_M = 10_000.0

# Uno por estilo con nombre. Revisado con el seed: con 4 grupos el conductor muy riesgoso
# comprimía al resto en "intermedio" y con 3 la que gira brusco quedaba como prudente.
# Elegirlo por silueta no sirve: con un caso extremo siempre gana k = 2 (él contra todos).
# Con pocos conductores distintos se usan menos (como mucho, uno menos que ellos)
NUMERO_GRUPOS = 6
MINIMO_CONDUCTORES_MODELO = 4
# Puntuación z (desvíos sobre la media de todos los conductores) desde la que una tasa de
# eventos define el estilo del grupo, y tasa mínima (por 10 km) para que cuente
UMBRAL_DISTINTIVO = 0.5
MINIMO_TASA_DISTINTIVA = 1.0
# Un grupo prudente necesita además al menos este DriveScore en su centro
MINIMO_DRIVESCORE_PRUDENTE = 75
REENTRENAR_CADA = timedelta(hours=1)

VIAJES_TENDENCIA = 10
MINIMO_VIAJES_TENDENCIA = 5
# Puntos de DriveScore por viaje desde los que la tendencia es "a la baja"
PENDIENTE_A_LA_BAJA = -0.5

MAXIMO_FRASES = 3
# Desde cuántas veces el promedio de la flota una tasa se menciona en la explicación
RAZON_DESTACADA = 1.25
DIFERENCIA_VELOCIDAD_DESTACADA_KMH = 5.0

# Variables del modelo, en el orden de las columnas
TASAS = ("frenadas", "aceleraciones", "giros", "excesos")
VARIABLES = (*TASAS, "drivescore", "velocidad_maxima")
TIPO_DE_TASA = {
    "frenadas": TipoEvento.frenada_brusca,
    "aceleraciones": TipoEvento.aceleracion_severa,
    "giros": TipoEvento.giro_agresivo,
    "excesos": TipoEvento.exceso_velocidad,
}
ESTILO_DE_TASA = {
    "frenadas": EstiloConduccion.brusco_frenar,
    "aceleraciones": EstiloConduccion.acelerador,
    "giros": EstiloConduccion.brusco_curvas,
    "excesos": EstiloConduccion.veloz,
}
TASA_DE_ESTILO = {estilo: tasa for tasa, estilo in ESTILO_DE_TASA.items()}

# De menor a mayor riesgo; `sin_datos` no tiene orden
ORDEN_ESTILOS = (
    EstiloConduccion.prudente,
    EstiloConduccion.intermedio,
    EstiloConduccion.brusco_curvas,
    EstiloConduccion.acelerador,
    EstiloConduccion.brusco_frenar,
    EstiloConduccion.veloz,
)
NOMBRE_ESTILO = {
    EstiloConduccion.prudente: "Prudente",
    EstiloConduccion.intermedio: "Intermedio",
    EstiloConduccion.brusco_curvas: "Brusco en curvas",
    EstiloConduccion.acelerador: "Acelerador",
    EstiloConduccion.brusco_frenar: "Brusco al frenar",
    EstiloConduccion.veloz: "Veloz",
    EstiloConduccion.sin_datos: "Sin datos suficientes",
}

_VERBO = {
    "frenadas": "Frena fuerte",
    "aceleraciones": "Acelera fuerte",
    "giros": "Toma las curvas de forma brusca",
    "excesos": "Excede el límite de velocidad",
}
_SUSTANTIVO = {
    "frenadas": "frenadas bruscas",
    "aceleraciones": "aceleraciones severas",
    "giros": "giros agresivos",
    "excesos": "excesos de velocidad",
}


def orden_riesgo(estilo: EstiloConduccion) -> int | None:
    return None if estilo == EstiloConduccion.sin_datos else ORDEN_ESTILOS.index(estilo)


def es_mayor_riesgo(anterior: EstiloConduccion, nuevo: EstiloConduccion) -> bool:
    a, n = orden_riesgo(anterior), orden_riesgo(nuevo)
    return a is not None and n is not None and n > a


def _redondear(valor: float) -> int:
    return math.floor(valor + 0.5)


def _decimal(valor: float) -> str:
    return f"{math.floor(valor * 10 + 0.5) / 10:.1f}".replace(".", ",")


# --- Variables de un conductor ------------------------------------------------------


@dataclass(frozen=True)
class ViajeEstilo:
    id: int
    fecha_inicio: datetime
    distancia_m: float
    drivescore: int
    velocidad_maxima_kmh: float
    eventos: Counter = field(default_factory=Counter)


@dataclass(frozen=True)
class VariablesConductor:
    usuario_id: int
    viajes: int
    distancia_m: float
    # Eventos por cada 10 km de cada tipo
    frenadas: float
    aceleraciones: float
    giros: float
    excesos: float
    # Ponderado por distancia
    drivescore: int
    # Promedio de la velocidad máxima de cada viaje
    velocidad_maxima: float

    @property
    def suficiente(self) -> bool:
        return self.viajes >= MINIMO_VIAJES_ESTILO and self.distancia_m >= MINIMO_DISTANCIA_ESTILO_M

    def vector(self) -> list[float]:
        return [float(getattr(self, v)) for v in VARIABLES]

    @property
    def tasa_total(self) -> float:
        return sum(getattr(self, t) for t in TASAS)


def resumir_variables(usuario_id: int, viajes: list[ViajeEstilo]) -> VariablesConductor | None:
    """Variables del conductor con sus viajes de la ventana; nulo sin viajes o sin km."""
    distancia = sum(v.distancia_m for v in viajes)
    if not viajes or distancia <= 0:
        return None
    por_10km = distancia / 10_000
    eventos = Counter()
    for v in viajes:
        eventos.update(v.eventos)
    return VariablesConductor(
        usuario_id=usuario_id,
        viajes=len(viajes),
        distancia_m=distancia,
        **{t: eventos[TIPO_DE_TASA[t]] / por_10km for t in TASAS},
        drivescore=_redondear(sum(v.drivescore * v.distancia_m for v in viajes) / distancia),
        velocidad_maxima=sum(v.velocidad_maxima_kmh for v in viajes) / len(viajes),
    )


# --- Grupos y nombres ------------------------------------------------------------


def nombrar_grupo(z: dict[str, float], centro: dict[str, float]) -> EstiloConduccion:
    """Estilo de un grupo por reglas fijas sobre su centro, en puntuaciones z (`z`) y en
    las unidades originales (`centro`):

    1. La tasa de eventos con la z más alta, si supera `UMBRAL_DISTINTIVO` y llega a
       `MINIMO_TASA_DISTINTIVA` por 10 km, le da su nombre (frenadas → brusco al frenar,
       aceleraciones → acelerador, giros → brusco en curvas, excesos → veloz).
    2. Si no, es prudente si todas las tasas quedan en la media o por debajo y su DriveScore
       en la media o por encima, y al menos `MINIMO_DRIVESCORE_PRUDENTE`.
    3. Si no, intermedio.
    Empate en la z más alta: la tasa de mayor riesgo (excesos, frenadas, giros, aceleraciones,
    como `ORDEN_RIESGO` de la HU-18)."""
    prioridad = ("excesos", "frenadas", "giros", "aceleraciones")
    tasa = max(prioridad, key=lambda t: (z[t], -prioridad.index(t)))
    if z[tasa] > UMBRAL_DISTINTIVO and centro[tasa] >= MINIMO_TASA_DISTINTIVA:
        return ESTILO_DE_TASA[tasa]
    if (
        all(z[t] <= 0 for t in TASAS)
        and z["drivescore"] >= 0
        and centro["drivescore"] >= MINIMO_DRIVESCORE_PRUDENTE
    ):
        return EstiloConduccion.prudente
    return EstiloConduccion.intermedio


@dataclass
class ModeloEstilos:
    escalador: object
    kmeans: object
    estilos: list[EstiloConduccion]
    conductores: int
    entrenado_en: datetime
    silueta: float

    def predecir(self, variables: list[VariablesConductor]) -> list[EstiloConduccion]:
        if not variables:
            return []
        grupos = self.kmeans.predict(self.escalador.transform([v.vector() for v in variables]))
        return [self.estilos[g] for g in grupos]


def entrenar(variables: list[VariablesConductor], ahora: datetime) -> ModeloEstilos | None:
    """K-means sobre las variables normalizadas (`StandardScaler`) de los conductores con
    datos suficientes, con `NUMERO_GRUPOS` grupos (menos si hay pocos conductores
    distintos). Nulo con menos de `MINIMO_CONDUCTORES_MODELO`. La silueta (−1 a 1, mejor
    cuanto más alta) solo se informa en el log."""
    datos = [v.vector() for v in variables if v.suficiente]
    k = min(NUMERO_GRUPOS, len({tuple(d) for d in datos}) - 1)
    if len(datos) < MINIMO_CONDUCTORES_MODELO or k < 2:
        return None

    # Import perezoso: scikit-learn (con numpy y scipy) solo se carga al entrenar
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    escalador = StandardScaler().fit(datos)
    normalizados = escalador.transform(datos)
    kmeans = KMeans(n_clusters=k, n_init=10, random_state=0).fit(normalizados)
    silueta = float(silhouette_score(normalizados, kmeans.labels_))
    centros = escalador.inverse_transform(kmeans.cluster_centers_)
    estilos = [
        nombrar_grupo(dict(zip(VARIABLES, z)), dict(zip(VARIABLES, c)))
        for z, c in zip(kmeans.cluster_centers_, centros)
    ]
    return ModeloEstilos(escalador, kmeans, estilos, len(datos), ahora, round(silueta, 2))


# --- Alerta de cambio --------------------------------------------------------------


@dataclass(frozen=True)
class EstadoEstilo:
    estilo: EstiloConduccion
    fecha_estilo: datetime
    estilo_anterior: EstiloConduccion | None
    fecha_alerta: datetime | None


def transicion(actual: EstadoEstilo | None, nuevo: EstiloConduccion, ahora: datetime) -> EstadoEstilo:
    """Nuevo estado guardado. Mismo estilo: no cambia. Paso a uno de mayor riesgo: queda la
    alerta (estilo anterior y fecha). Cualquier otro cambio (menor o igual riesgo, o
    `sin_datos`) la limpia."""
    if actual is None:
        return EstadoEstilo(nuevo, ahora, None, None)
    if actual.estilo == nuevo:
        return actual
    if es_mayor_riesgo(actual.estilo, nuevo):
        return EstadoEstilo(nuevo, ahora, actual.estilo, ahora)
    return EstadoEstilo(nuevo, ahora, None, None)


# --- Explicación -------------------------------------------------------------------


def _cuanto_mas(razon: float) -> str:
    veces = _redondear(razon)
    if razon >= 1.95:
        return {2: "el doble", 3: "el triple"}.get(veces, f"{veces} veces más")
    return f"un {_redondear((razon - 1) * 100)} % más"


def _frase_tasa(tasa: str, valor: float, promedio: float) -> str:
    if promedio <= 0:
        return f"{_VERBO[tasa]} ({_decimal(valor)} cada 10 km) y el resto de tu flota casi no lo hace"
    return f"{_VERBO[tasa]} {_cuanto_mas(valor / promedio)} que el promedio de tu flota"


def _destacadas(conductor: VariablesConductor, promedios: dict[str, float]) -> list[str]:
    """Tasas del conductor por encima del promedio de la flota, de la más alta a la más baja
    (en veces el promedio)."""

    def razon(t: str) -> float:
        valor, promedio = getattr(conductor, t), promedios[t]
        if valor <= 0:
            return 0.0
        return math.inf if promedio <= 0 else valor / promedio

    return sorted((t for t in TASAS if razon(t) >= RAZON_DESTACADA), key=lambda t: -razon(t))


def explicar_estilo(
    estilo: EstiloConduccion,
    conductor: VariablesConductor | None,
    flota: list[VariablesConductor],
) -> list[str]:
    """Por qué el conductor tiene ese estilo, comparado con el promedio de su flota
    (`flota`: los conductores de su empresa con datos suficientes, él incluido). Si nadie
    más de su flota tiene datos, con sus propios valores."""
    if estilo == EstiloConduccion.sin_datos or conductor is None or not conductor.suficiente:
        viajes = conductor.viajes if conductor else 0
        return [
            f"Necesita al menos {MINIMO_VIAJES_ESTILO} viajes y "
            f"{_redondear(MINIMO_DISTANCIA_ESTILO_M / 1000)} km en los últimos {DIAS_VENTANA} días "
            f"(lleva {viajes} {'viaje' if viajes == 1 else 'viajes'})"
        ]

    otros = [v for v in flota if v.usuario_id != conductor.usuario_id]
    principal = TASA_DE_ESTILO.get(estilo)
    if not otros:
        tasas = sorted(TASAS, key=lambda t: -getattr(conductor, t))
        if principal:
            tasas = [principal] + [t for t in tasas if t != principal]
        frases = [
            f"{_decimal(getattr(conductor, t))} {_SUSTANTIVO[t]} cada 10 km"
            for t in tasas
            if getattr(conductor, t) > 0
        ][:2]
        frases.append(f"DriveScore promedio de {conductor.drivescore} en los últimos {DIAS_VENTANA} días")
        return frases[:MAXIMO_FRASES]

    con_datos = [conductor, *otros]
    promedios = {v: sum(getattr(c, v) for c in con_datos) / len(con_datos) for v in VARIABLES}
    destacadas = _destacadas(conductor, promedios)
    frases: list[str] = []

    if estilo == EstiloConduccion.prudente:
        total_flota = sum(promedios[t] for t in TASAS)
        if conductor.tasa_total == 0:
            frases.append(f"No tuvo eventos de riesgo en los últimos {DIAS_VENTANA} días")
        elif total_flota > 0 and conductor.tasa_total < total_flota:
            menos = _redondear((1 - conductor.tasa_total / total_flota) * 100)
            frases.append(f"Tiene un {menos} % menos de eventos que el promedio de tu flota")
    elif principal:
        frases.append(_frase_tasa(principal, getattr(conductor, principal), promedios[principal]))
        if estilo == EstiloConduccion.veloz:
            diferencia = conductor.velocidad_maxima - promedios["velocidad_maxima"]
            if diferencia >= DIFERENCIA_VELOCIDAD_DESTACADA_KMH:
                frases.append(
                    f"Su velocidad máxima promedio es {_redondear(diferencia)} km/h más alta "
                    "que la de tu flota"
                )

    for t in destacadas:
        if t != principal and len(frases) < MAXIMO_FRASES - 1:
            frases.append(_frase_tasa(t, getattr(conductor, t), promedios[t]))
    if estilo == EstiloConduccion.intermedio and not destacadas:
        frases.append("Sus eventos de riesgo están cerca del promedio de tu flota")

    diferencia = conductor.drivescore - _redondear(promedios["drivescore"])
    if diferencia != 0:
        puntos = f"{abs(diferencia)} {'punto' if abs(diferencia) == 1 else 'puntos'}"
        lado = "sobre" if diferencia > 0 else "bajo"
        frases.append(f"Su DriveScore ({conductor.drivescore}) está {puntos} {lado} el promedio de tu flota")
    return frases[:MAXIMO_FRASES]


# --- Tendencia de riesgo -------------------------------------------------------------


@dataclass(frozen=True)
class Tendencia:
    viajes: int
    # Puntos de DriveScore por viaje (regresión lineal); nulos con pocos viajes
    pendiente: float | None
    # DriveScore estimado para el próximo viaje, 0–100
    proyectado: int | None
    a_la_baja: bool | None


def tendencia_riesgo(puntajes: list[int]) -> Tendencia:
    """Regresión lineal (mínimos cuadrados) del DriveScore de los últimos `VIAJES_TENDENCIA`
    viajes, en orden cronológico. Con menos de `MINIMO_VIAJES_TENDENCIA`, todo nulo."""
    ultimos = puntajes[-VIAJES_TENDENCIA:]
    n = len(ultimos)
    if n < MINIMO_VIAJES_TENDENCIA:
        return Tendencia(n, None, None, None)
    media_x = (n - 1) / 2
    media_y = sum(ultimos) / n
    pendiente = sum((x - media_x) * (y - media_y) for x, y in enumerate(ultimos)) / sum(
        (x - media_x) ** 2 for x in range(n)
    )
    proyectado = media_y + pendiente * (n - media_x)
    return Tendencia(
        viajes=n,
        pendiente=math.floor(pendiente * 10 + 0.5) / 10,
        proyectado=min(100, max(0, _redondear(proyectado))),
        a_la_baja=pendiente <= PENDIENTE_A_LA_BAJA,
    )


# --- Consultas ----------------------------------------------------------------------


def inicio_ventana(ahora: datetime, zona_horaria: str) -> datetime:
    """Medianoche local de hoy menos 29 días (como el periodo "30 días")."""
    zona = ZoneInfo(zona_horaria)
    primer_dia = ahora.astimezone(zona).date() - timedelta(days=DIAS_VENTANA - 1)
    return datetime.combine(primer_dia, time.min, tzinfo=zona).astimezone(UTC)


async def cargar_viajes(
    db: AsyncSession, inicios: dict[int, datetime]
) -> dict[int, list[ViajeEstilo]]:
    """Viajes finalizados con puntaje de cada conductor desde su inicio de ventana
    (`inicios`: usuario → inicio), en orden cronológico, con sus eventos por tipo."""
    if not inicios:
        return {}
    desde = min(inicios.values())
    condiciones = [
        Recorrido.usuario_id.in_(inicios),
        Recorrido.estado == EstadoRecorrido.finalizado,
        Recorrido.drivescore.is_not(None),
        Recorrido.fecha_inicio >= desde,
    ]
    filas = await db.execute(
        select(
            Recorrido.id,
            Recorrido.usuario_id,
            Recorrido.fecha_inicio,
            Recorrido.distancia_m,
            Recorrido.drivescore,
            Recorrido.velocidad_maxima_kmh,
        )
        .where(*condiciones)
        .order_by(Recorrido.fecha_inicio, Recorrido.id)
    )
    viajes = [f for f in filas if f.fecha_inicio >= inicios[f.usuario_id]]
    eventos: dict[int, Counter] = {}
    if viajes:
        conteo = await db.execute(
            select(Evento.recorrido_id, Evento.tipo, func.count())
            .join(Recorrido, Recorrido.id == Evento.recorrido_id)
            .where(*condiciones)
            .group_by(Evento.recorrido_id, Evento.tipo)
        )
        for recorrido_id, tipo, cantidad in conteo:
            eventos.setdefault(recorrido_id, Counter())[tipo] = cantidad
    por_conductor: dict[int, list[ViajeEstilo]] = {}
    for f in viajes:
        por_conductor.setdefault(f.usuario_id, []).append(
            ViajeEstilo(
                f.id,
                f.fecha_inicio,
                f.distancia_m,
                f.drivescore,
                f.velocidad_maxima_kmh,
                eventos.get(f.id, Counter()),
            )
        )
    return por_conductor


def variables_de(viajes: dict[int, list[ViajeEstilo]]) -> dict[int, VariablesConductor]:
    resumen = {u: resumir_variables(u, v) for u, v in viajes.items()}
    return {u: v for u, v in resumen.items() if v is not None}


# --- Entrenamiento y estilos guardados ---------------------------------------------------


@dataclass
class _Cache:
    modelo: ModeloEstilos | None = None
    entrenado_en: datetime | None = None


# Un modelo por worker, en memoria: se reentrena cada `REENTRENAR_CADA`
_cache = _Cache()
_candado = asyncio.Lock()
# Clave del candado de PostgreSQL que serializa la actualización entre workers
_CLAVE_CANDADO = 2025_0025


def reiniciar_cache() -> None:
    _cache.modelo = None
    _cache.entrenado_en = None


def modelo_en_cache() -> ModeloEstilos | None:
    return _cache.modelo


async def actualizar_estilos(db: AsyncSession, forzar: bool = False) -> bool:
    """Recalcula y guarda el estilo de todos los conductores de flota activos si los guardados
    tienen más de `REENTRENAR_CADA` o hay viajes finalizados después de la última
    actualización (o con `forzar`). El modelo se reentrena como mucho cada `REENTRENAR_CADA`
    por worker. Si otro proceso ya está actualizando, no espera. Hace commit; devuelve si
    actualizó."""
    ahora = datetime.now(UTC)
    async with _candado:
        bloqueado = await db.scalar(select(func.pg_try_advisory_xact_lock(_CLAVE_CANDADO)))
        if not bloqueado:
            return False
        ultima = await db.scalar(select(func.max(EstiloConductor.fecha_actualizacion)))
        if not forzar and ultima is not None and ahora - ultima < REENTRENAR_CADA:
            ultimo_viaje = await db.scalar(
                select(func.max(Recorrido.fecha_fin)).where(
                    Recorrido.estado == EstadoRecorrido.finalizado,
                    Recorrido.drivescore.is_not(None),
                )
            )
            if ultimo_viaje is None or ultimo_viaje <= ultima:
                await db.commit()
                return False

        conductores = (
            await db.execute(
                select(Usuario.id, Usuario.empresa_id, Empresa.zona_horaria)
                .outerjoin(Empresa, Empresa.id == Usuario.empresa_id)
                .where(Usuario.rol == RolUsuario.conductor, Usuario.activo.is_(True))
            )
        ).all()
        inicios = {c.id: inicio_ventana(ahora, c.zona_horaria or ZONA_SIN_EMPRESA) for c in conductores}
        variables = variables_de(await cargar_viajes(db, inicios))

        if (
            forzar
            or _cache.modelo is None
            or _cache.entrenado_en is None
            or ahora - _cache.entrenado_en >= REENTRENAR_CADA
        ):
            _cache.modelo = await asyncio.to_thread(entrenar, list(variables.values()), ahora)
            _cache.entrenado_en = ahora
            if _cache.modelo:
                log.info(
                    "Estilos: modelo con %d conductores, silueta %.2f y grupos %s",
                    _cache.modelo.conductores,
                    _cache.modelo.silueta,
                    [e.value for e in _cache.modelo.estilos],
                )

        de_flota = [c.id for c in conductores if c.empresa_id is not None]
        suficientes = [variables[u] for u in de_flota if u in variables and variables[u].suficiente]
        predichos = dict(
            zip((v.usuario_id for v in suficientes), _cache.modelo.predecir(suficientes))
        ) if _cache.modelo else {}

        # Columnas y no entidades: el upsert de abajo no actualiza el mapa de identidad de la
        # sesión, y una entidad cargada aquí quedaría con los valores viejos
        guardados = {}
        if de_flota:
            filas_guardadas = await db.execute(
                select(
                    EstiloConductor.usuario_id,
                    EstiloConductor.estilo,
                    EstiloConductor.fecha_estilo,
                    EstiloConductor.estilo_anterior,
                    EstiloConductor.fecha_alerta,
                ).where(EstiloConductor.usuario_id.in_(de_flota))
            )
            guardados = {f.usuario_id: EstadoEstilo(*f[1:]) for f in filas_guardadas}
        filas = []
        for usuario_id in de_flota:
            estado = transicion(
                guardados.get(usuario_id),
                predichos.get(usuario_id, EstiloConduccion.sin_datos),
                ahora,
            )
            filas.append(
                {
                    "usuario_id": usuario_id,
                    "estilo": estado.estilo,
                    "fecha_estilo": estado.fecha_estilo,
                    "estilo_anterior": estado.estilo_anterior,
                    "fecha_alerta": estado.fecha_alerta,
                    "fecha_actualizacion": ahora,
                }
            )
        if filas:
            sentencia = insert(EstiloConductor).values(filas)
            await db.execute(
                sentencia.on_conflict_do_update(
                    index_elements=[EstiloConductor.usuario_id],
                    set_={
                        c: sentencia.excluded[c]
                        for c in ("estilo", "fecha_estilo", "estilo_anterior", "fecha_alerta", "fecha_actualizacion")
                    },
                )
            )
        await db.commit()
        return True
