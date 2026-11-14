"""Datos de ejemplo para el panel de flotas (manual, no forma parte de la app).

Crea una empresa con su administrador y 5 conductores con viajes del último mes
(ruta en Santa Cruz de la Sierra, eventos de los cuatro tipos y DriveScore).

    python scripts/seed_flota_ejemplo.py             # crea (o recrea) los datos
    python scripts/seed_flota_ejemplo.py --borrar    # solo borra los datos de ejemplo

Se puede ejecutar varias veces: antes de crear, borra lo que ya había. Todo lo que crea
usa correos @ejemplo.com, y por ese dominio se identifica al borrar. Contraseña de todas
las cuentas: Ejemplo1234. Ejecutar desde la raíz del backend (lee el .env de ahí).
"""

import argparse
import asyncio
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select, text  # noqa: E402

from app import models  # noqa: E402, F401
from app.auth.models import RolUsuario, Usuario  # noqa: E402
from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.security import hashear_contrasenia  # noqa: E402
from app.flotas.models import Empresa, TipoEmpresa  # noqa: E402
from app.gamificacion.models import DesafioSemanal, Experiencia, LogroUsuario  # noqa: E402
from app.puntaje.service import EventoPuntaje, calcular_puntaje  # noqa: E402
from app.recomendaciones import service as recomendaciones  # noqa: E402
from app.recomendaciones.models import Recomendacion  # noqa: E402
from app.recorridos.models import EstadoRecorrido, Recorrido  # noqa: E402
from app.telemetria.models import Evento, TipoEvento  # noqa: E402

DOMINIO = "@ejemplo.com"
CONTRASENIA = "Ejemplo1234"
SEMILLA = 24  # mismos datos en cada ejecución
DESFASE_LOCAL_H = 4  # Bolivia es UTC-4

# Puntos de referencia de Santa Cruz de la Sierra (lat, lon): plaza, anillos y avenidas
PUNTOS_CIUDAD = [
    (-17.7834, -63.1821),  # Plaza 24 de Septiembre
    (-17.7650, -63.1790),  # Av. Cristo Redentor, 2.º anillo
    (-17.7450, -63.1750),  # Av. Banzer, 4.º anillo
    (-17.7620, -63.1960),  # Equipetrol
    (-17.7690, -63.2010),  # Av. San Martín
    (-17.7700, -63.1700),  # Av. Alemana
    (-17.7950, -63.1700),  # Av. Santos Dumont
    (-17.8000, -63.1930),  # Av. Grigotá
]

# Cada perfil: cuántos viajes y cuántos eventos de cada tipo por cada 10 km (en promedio)
PERFILES = [
    ("Ana Flores", "ana.flores", 12, dict(frenada=0.1, aceleracion=0.05, giro=0.05, exceso=0.0)),
    ("Bruno Rojas", "bruno.rojas", 10, dict(frenada=3.5, aceleracion=3.0, giro=2.5, exceso=1.8)),
    ("Carla Suárez", "carla.suarez", 8, dict(frenada=1.0, aceleracion=0.6, giro=0.8, exceso=0.4)),
    ("Diego Méndez", "diego.mendez", 6, dict(frenada=1.8, aceleracion=1.2, giro=1.0, exceso=0.8)),
    ("Elena Vaca", "elena.vaca", 0, dict(frenada=0, aceleracion=0, giro=0, exceso=0)),
]


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6_371_000
    f1, f2 = math.radians(a[0]), math.radians(b[0])
    df, dl = f2 - f1, math.radians(b[1] - a[1])
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _poisson(rng: random.Random, media: float) -> int:
    limite, k, p = math.exp(-media), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limite:
            return k
        k += 1


def _trazar_ruta(rng: random.Random, inicio: datetime) -> tuple[list[dict], float, int]:
    """Ruta entre 2–3 puntos de la ciudad, con un punto cada ~10 s. Devuelve
    (puntos, distancia en metros, duración en segundos)."""
    paradas = rng.sample(PUNTOS_CIUDAD, rng.choice([2, 3]))
    velocidad_kmh = rng.uniform(24, 38)
    # Se recorre la ruta (con una vuelta de ida y vuelta si quedó corta) hasta ~3-12 km
    tramos = list(zip(paradas, paradas[1:]))
    distancia = sum(_haversine_m(a, b) for a, b in tramos)
    if distancia < 3000:
        tramos += [(b, a) for a, b in reversed(tramos)]
        distancia *= 2
    distancia *= rng.uniform(1.25, 1.4)  # las calles no van en línea recta

    duracion_s = int(distancia / (velocidad_kmh / 3.6))
    cantidad = max(duracion_s // 10, 2)
    puntos = []
    for i in range(cantidad + 1):
        avance = i / cantidad * len(tramos)
        indice = min(int(avance), len(tramos) - 1)
        t = avance - indice
        (la1, lo1), (la2, lo2) = tramos[indice]
        puntos.append(
            {
                "lat": round(la1 + (la2 - la1) * t + rng.uniform(-0.0002, 0.0002), 6),
                "lon": round(lo1 + (lo2 - lo1) * t + rng.uniform(-0.0002, 0.0002), 6),
                "fecha": (inicio + timedelta(seconds=duracion_s * i / cantidad)).isoformat(),
                "velocidad_kmh": round(max(0.0, rng.gauss(velocidad_kmh, 8)), 1),
            }
        )
    return puntos, distancia, duracion_s


def _eventos_del_viaje(
    rng: random.Random, tasas: dict, distancia_m: float, puntos: list[dict]
) -> list[Evento]:
    por_10km = distancia_m / 10_000
    eventos = []

    def en_ruta() -> dict:
        return puntos[rng.randrange(1, len(puntos) - 1)]

    for tipo, clave, intensidad in (
        (TipoEvento.frenada_brusca, "frenada", (3.5, 7.0)),
        (TipoEvento.aceleracion_severa, "aceleracion", (3.0, 5.0)),
        (TipoEvento.giro_agresivo, "giro", (3.0, 5.0)),
    ):
        for _ in range(_poisson(rng, tasas[clave] * por_10km)):
            punto = en_ruta()
            eventos.append(
                Evento(
                    tipo=tipo,
                    fecha=datetime.fromisoformat(punto["fecha"]),
                    lat=punto["lat"],
                    lon=punto["lon"],
                    velocidad_kmh=round(rng.uniform(25, 60), 1),
                    intensidad=round(rng.uniform(*intensidad), 2),
                )
            )
    for _ in range(_poisson(rng, tasas["exceso"] * por_10km)):
        punto = en_ruta()
        limite = rng.choice([40, 50, 60])
        sobre = rng.uniform(5, 25)
        eventos.append(
            Evento(
                tipo=TipoEvento.exceso_velocidad,
                fecha=datetime.fromisoformat(punto["fecha"]),
                lat=punto["lat"],
                lon=punto["lon"],
                velocidad_kmh=round(limite + rng.uniform(2, 6), 1),
                intensidad=round(rng.uniform(1.0, 6.0), 2),
                duracion_s=round(rng.uniform(6, 50), 1),
                velocidad_maxima_kmh=round(limite + sobre, 1),
            )
        )
    return sorted(eventos, key=lambda e: e.fecha)


def _dias_de_viajes(rng: random.Random, cantidad: int) -> list[int]:
    """Días atrás (0–29) sin repetir; al menos 2 en la última semana si hay viajes."""
    if cantidad == 0:
        return []
    recientes = rng.sample(range(0, 7), min(2, cantidad))
    resto = rng.sample(range(7, 30), cantidad - len(recientes))
    return sorted(recientes + resto)


async def borrar(sesion) -> tuple[int, int]:
    """Borra usuarios @ejemplo.com, sus empresas y todo lo que cuelga de ellos."""
    usuarios = select(Usuario.id).where(Usuario.email.like(f"%{DOMINIO}"))
    empresas = list(
        await sesion.scalars(
            select(Usuario.empresa_id)
            .where(Usuario.email.like(f"%{DOMINIO}"), Usuario.empresa_id.is_not(None))
            .distinct()
        )
    )
    recorridos = select(Recorrido.id).where(Recorrido.usuario_id.in_(usuarios))
    eventos = select(Evento.id).where(Evento.recorrido_id.in_(recorridos))

    # La tabla del modo pruebas (temporal) puede no existir
    if await sesion.scalar(text("SELECT to_regclass('validaciones_eventos')")):
        await sesion.execute(
            text("DELETE FROM validaciones_eventos WHERE evento_id IN (SELECT id FROM eventos "
                 "WHERE recorrido_id IN (SELECT id FROM recorridos WHERE usuario_id IN "
                 "(SELECT id FROM usuarios WHERE email LIKE :patron)))"),
            {"patron": f"%{DOMINIO}"},
        )
    # Experiencias antes que desafíos: apuntan a ellos
    await sesion.execute(delete(Experiencia).where(Experiencia.usuario_id.in_(usuarios)))
    await sesion.execute(delete(DesafioSemanal).where(DesafioSemanal.usuario_id.in_(usuarios)))
    await sesion.execute(delete(LogroUsuario).where(LogroUsuario.usuario_id.in_(usuarios)))
    await sesion.execute(delete(Recomendacion).where(Recomendacion.recorrido_id.in_(recorridos)))
    await sesion.execute(delete(Evento).where(Evento.id.in_(eventos)))
    await sesion.execute(delete(Recorrido).where(Recorrido.usuario_id.in_(usuarios)))
    eliminados = (
        await sesion.execute(delete(Usuario).where(Usuario.email.like(f"%{DOMINIO}")))
    ).rowcount
    # Solo las empresas que quedaron sin usuarios
    for empresa_id in empresas:
        if not await sesion.scalar(select(Usuario.id).where(Usuario.empresa_id == empresa_id).limit(1)):
            await sesion.execute(delete(Empresa).where(Empresa.id == empresa_id))
    await sesion.commit()
    return eliminados, len(empresas)


async def crear(sesion) -> None:
    rng = random.Random(SEMILLA)
    hash_contrasenia = hashear_contrasenia(CONTRASENIA)
    ahora = datetime.now(UTC)

    empresa = Empresa(
        nombre="Flota Ejemplo (datos de ejemplo)",
        tipo=TipoEmpresa.flota,
        email_contacto=f"admin{DOMINIO}",
        telefono="70000000",
        activo=True,
    )
    sesion.add(empresa)
    await sesion.flush()
    sesion.add(
        Usuario(
            empresa_id=empresa.id,
            nombre="Admin Ejemplo",
            email=f"admin{DOMINIO}",
            telefono="70000001",
            contrasenia_hash=hash_contrasenia,
            rol=RolUsuario.admin_empresa,
            activo=True,
            debe_cambiar_contrasenia=False,
        )
    )

    for indice, (nombre, usuario_correo, viajes, tasas) in enumerate(PERFILES):
        conductor = Usuario(
            empresa_id=empresa.id,
            nombre=nombre,
            email=f"{usuario_correo}{DOMINIO}",
            telefono=f"7100000{indice}",
            contrasenia_hash=hash_contrasenia,
            rol=RolUsuario.conductor,
            activo=True,
            debe_cambiar_contrasenia=False,
        )
        sesion.add(conductor)
        await sesion.flush()

        for dias_atras in _dias_de_viajes(rng, viajes):
            medianoche_utc = (ahora - timedelta(days=dias_atras)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            inicio = medianoche_utc + timedelta(
                hours=rng.randint(6, 20) + DESFASE_LOCAL_H, minutes=rng.randint(0, 59)
            )
            puntos, distancia_m, duracion_s = _trazar_ruta(rng, inicio)
            fin = inicio + timedelta(seconds=duracion_s)
            if fin > ahora:  # el viaje de hoy no puede terminar en el futuro
                inicio -= timedelta(days=1)
                fin -= timedelta(days=1)
                puntos, distancia_m, duracion_s = _trazar_ruta(rng, inicio)
                fin = inicio + timedelta(seconds=duracion_s)

            eventos = _eventos_del_viaje(rng, tasas, distancia_m, puntos)
            eventos_puntaje = [EventoPuntaje(e.tipo, e.duracion_s) for e in eventos]
            puntaje = calcular_puntaje(distancia_m, duracion_s, eventos_puntaje)
            velocidades = [p["velocidad_kmh"] for p in puntos]

            recorrido = Recorrido(
                usuario_id=conductor.id,
                empresa_id=empresa.id,
                estado=EstadoRecorrido.finalizado,
                fecha_inicio=inicio,
                fecha_fin=fin,
                distancia_m=round(distancia_m, 1),
                duracion_s=duracion_s,
                velocidad_maxima_kmh=max(velocidades),
                velocidad_promedio_kmh=round(sum(velocidades) / len(velocidades), 1),
                lat_inicio=puntos[0]["lat"],
                lon_inicio=puntos[0]["lon"],
                lat_fin=puntos[-1]["lat"],
                lon_fin=puntos[-1]["lon"],
                ruta=puntos,
                drivescore=puntaje.drivescore,
                puntaje_frenadas=puntaje.frenadas,
                puntaje_aceleraciones=puntaje.aceleraciones,
                puntaje_giros=puntaje.giros,
                puntaje_velocidad=puntaje.velocidad,
                dispositivo_modelo="Ejemplo",
                dispositivo_android="14",
                version_app="ejemplo",
            )
            sesion.add(recorrido)
            await sesion.flush()
            for evento in eventos:
                evento.recorrido_id = recorrido.id
            sesion.add_all(eventos)
            sesion.add_all(recomendaciones.filas(recorrido.id, puntaje, eventos_puntaje))
    await sesion.commit()


async def principal(solo_borrar: bool) -> None:
    async with SessionLocal() as sesion:
        usuarios, empresas = await borrar(sesion)
        if usuarios:
            print(f"Borrados {usuarios} usuarios de ejemplo y {empresas} empresa(s).")
        if solo_borrar:
            if not usuarios:
                print("No había datos de ejemplo.")
            return
        await crear(sesion)
        resumen = await sesion.execute(
            text(
                "SELECT u.nombre, count(r.id), round(sum(r.drivescore * r.distancia_m) / "
                "nullif(sum(r.distancia_m), 0)) FROM usuarios u LEFT JOIN recorridos r "
                "ON r.usuario_id = u.id WHERE u.email LIKE :p AND u.rol = 'conductor' "
                "GROUP BY u.nombre ORDER BY 3 DESC NULLS LAST"
            ),
            {"p": f"%{DOMINIO}"},
        )
        print("Datos de ejemplo creados. Conductores (viajes, DriveScore):")
        for nombre, viajes, drivescore in resumen:
            print(f"  {nombre}: {viajes} viajes, DriveScore {drivescore if drivescore is not None else '—'}")
        print(f"Panel: admin{DOMINIO} / {CONTRASENIA}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Datos de ejemplo de la flota")
    parser.add_argument("--borrar", action="store_true", help="solo borra los datos de ejemplo")
    asyncio.run(principal(parser.parse_args().borrar))
