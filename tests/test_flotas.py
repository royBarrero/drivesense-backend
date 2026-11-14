"""HU-24: listado y registro de conductores de la flota (con base de datos)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.auth.models import Usuario
from app.flotas.schemas import Periodo
from app.flotas.service import (
    LARGO_CONTRASENIA_TEMPORAL,
    generar_contrasenia_temporal,
    inicio_periodo,
)
from app.recorridos.models import EstadoRecorrido, Recorrido
from app.telemetria.models import Evento, TipoEvento
from tests.conftest import cabecera

CONDUCTORES = "/api/v1/flotas/conductores"
RESUMEN = "/api/v1/flotas/resumen"


# --- Pruebas sin base de datos ----------------------------------------------


def test_contrasenia_temporal_cumple_reglas_y_no_se_repite():
    claves = {generar_contrasenia_temporal() for _ in range(200)}
    assert len(claves) == 200
    for clave in claves:
        assert len(clave) == LARGO_CONTRASENIA_TEMPORAL
        assert any(c.isalpha() for c in clave) and any(c.isdigit() for c in clave)


def test_inicio_periodo_usa_la_zona_de_la_empresa():
    # 02:00 UTC del 15 = 22:00 del 14 en La Paz (UTC-4): "hoy" para la empresa es el 14
    ahora = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
    # 7 días = hoy y los 6 anteriores: desde el 8 a las 00:00 de La Paz = 04:00 UTC
    assert inicio_periodo(Periodo.siete_dias, ahora, "America/La_Paz") == datetime(
        2026, 11, 8, 4, 0, tzinfo=UTC
    )
    assert inicio_periodo(Periodo.treinta_dias, ahora, "America/La_Paz") == datetime(
        2026, 10, 16, 4, 0, tzinfo=UTC
    )
    # En UTC "hoy" ya es el 15
    assert inicio_periodo(Periodo.siete_dias, ahora, "UTC") == datetime(2026, 11, 9, tzinfo=UTC)
    assert inicio_periodo(Periodo.todo, ahora, "UTC") is None


# --- Utilidades de las pruebas con BD ---------------------------------------


async def registrar_conductor(cliente, sesion, numero: int, nombre: str | None = None) -> dict:
    respuesta = await cliente.post(
        CONDUCTORES,
        headers=cabecera(sesion),
        json={
            "nombre": nombre or f"Conductor {numero}",
            "email": f"conductor{numero}@empresa.example.com",
            "telefono": "71000000",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


async def crear_viaje(
    db,
    usuario_id: int,
    drivescore: int | None,
    distancia_m: float = 10_000,
    dias_atras: float = 1,
    estado: EstadoRecorrido = EstadoRecorrido.finalizado,
    eventos: int = 0,
) -> Recorrido:
    inicio = datetime.now(UTC) - timedelta(days=dias_atras)
    viaje = Recorrido(
        usuario_id=usuario_id,
        estado=estado,
        fecha_inicio=inicio,
        fecha_fin=inicio + timedelta(minutes=20),
        distancia_m=distancia_m,
        duracion_s=1200,
        velocidad_maxima_kmh=60,
        velocidad_promedio_kmh=30,
        lat_inicio=-17.78,
        lon_inicio=-63.18,
        drivescore=drivescore,
    )
    db.add(viaje)
    await db.flush()
    for _ in range(eventos):
        db.add(
            Evento(
                recorrido_id=viaje.id,
                tipo=TipoEvento.frenada_brusca,
                fecha=inicio + timedelta(minutes=5),
                lat=-17.78,
                lon=-63.18,
                velocidad_kmh=40,
                intensidad=4.0,
            )
        )
    await db.commit()
    return viaje


async def listar(cliente, sesion, **parametros) -> dict:
    respuesta = await cliente.get(CONDUCTORES, headers=cabecera(sesion), params=parametros)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


# --- Acceso -----------------------------------------------------------------


async def test_sin_token_responde_401(cliente):
    assert (await cliente.get(CONDUCTORES)).status_code == 401
    assert (await cliente.get(RESUMEN)).status_code == 401
    assert (await cliente.post(CONDUCTORES, json={})).status_code == 401


async def test_conductor_de_flota_no_accede(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    creado = await registrar_conductor(cliente, admin, 1)
    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": creado["email"], "contrasenia": creado["contrasenia_temporal"]},
    )
    assert login.status_code == 200
    sesion = login.json()
    assert (await cliente.get(CONDUCTORES, headers=cabecera(sesion))).status_code == 403
    assert (await cliente.get(RESUMEN, headers=cabecera(sesion))).status_code == 403
    assert (
        await cliente.post(
            CONDUCTORES,
            headers=cabecera(sesion),
            json={"nombre": "X", "email": "x@empresa.example.com", "telefono": "71000000"},
        )
    ).status_code == 403


async def test_conductor_individual_sin_empresa_no_accede(cliente):
    registro = await cliente.post(
        "/api/v1/auth/registro",
        json={
            "nombre": "Solo",
            "email": "solo@empresa.example.com",
            "telefono": "72000000",
            "contrasenia": "Clave1234",
        },
    )
    assert registro.status_code == 201
    sesion = registro.json()
    assert (await cliente.get(CONDUCTORES, headers=cabecera(sesion))).status_code == 403
    assert (await cliente.get(RESUMEN, headers=cabecera(sesion))).status_code == 403


# --- Aislamiento entre empresas ---------------------------------------------


async def test_una_empresa_no_ve_datos_de_otra(cliente, db, registrar_empresa):
    admin_a = await registrar_empresa(1)
    admin_b = await registrar_empresa(2)
    a = await registrar_conductor(cliente, admin_a, 1, "Ana de A")
    b = await registrar_conductor(cliente, admin_b, 2, "Beto de B")
    await crear_viaje(db, a["id"], 90, eventos=2)
    await crear_viaje(db, b["id"], 40, eventos=5)

    pagina_a = await listar(cliente, admin_a)
    assert [c["nombre"] for c in pagina_a["conductores"]] == ["Ana de A"]
    assert pagina_a["total"] == 1
    pagina_b = await listar(cliente, admin_b)
    assert [c["nombre"] for c in pagina_b["conductores"]] == ["Beto de B"]

    resumen_a = (await cliente.get(RESUMEN, headers=cabecera(admin_a))).json()
    assert resumen_a["conductores"] == 1
    assert resumen_a["drivescore_promedio"] == 90
    assert resumen_a["eventos"] == 2

    # La búsqueda tampoco cruza empresas
    assert (await listar(cliente, admin_a, q="Beto"))["total"] == 0

    # Ni se puede regenerar la contraseña de un conductor ajeno
    ajeno = await cliente.post(
        f"{CONDUCTORES}/{b['id']}/contrasenia-temporal", headers=cabecera(admin_a)
    )
    assert ajeno.status_code == 404
    # y la de B sigue sirviendo
    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": b["email"], "contrasenia": b["contrasenia_temporal"]},
    )
    assert login.status_code == 200


# --- Listado ----------------------------------------------------------------


async def test_promedio_ponderado_por_distancia_solo_finalizados_y_activos(
    cliente, db, registrar_empresa
):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    # 100 con 1 km y 60 con 9 km → (100*1 + 60*9) / 10 = 64 (no el 80 del promedio simple)
    await crear_viaje(db, ana["id"], 100, distancia_m=1_000)
    await crear_viaje(db, ana["id"], 60, distancia_m=9_000, dias_atras=2)
    # El descartado no cuenta; el de 1 m casi no pesa
    await crear_viaje(db, ana["id"], None, estado=EstadoRecorrido.descartado)
    await crear_viaje(db, ana["id"], 10, estado=EstadoRecorrido.finalizado, distancia_m=1)

    fila = (await listar(cliente, admin))["conductores"][0]
    assert fila["viajes"] == 3  # finalizados (el descartado no)
    assert fila["drivescore"] == 64
    assert fila["calificacion"] == "regular"

    # Un conductor desactivado deja de aparecer
    beto = await registrar_conductor(cliente, admin, 2, "Beto")
    assert (await listar(cliente, admin))["total"] == 2
    usuario = await db.get(Usuario, beto["id"])
    usuario.activo = False
    await db.commit()
    pagina = await listar(cliente, admin)
    assert pagina["total"] == 1
    assert (await cliente.get(RESUMEN, headers=cabecera(admin))).json()["conductores"] == 1


async def test_conductor_sin_viajes(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    await registrar_conductor(cliente, admin, 1)
    fila = (await listar(cliente, admin))["conductores"][0]
    assert fila["viajes"] == 0
    assert fila["drivescore"] is None
    assert fila["calificacion"] is None
    assert fila["ultimo_viaje"] is None


async def test_filtro_por_periodo(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ana = await registrar_conductor(cliente, admin, 1, "Ana")
    await crear_viaje(db, ana["id"], 95, dias_atras=1)
    await crear_viaje(db, ana["id"], 50, dias_atras=20)
    await crear_viaje(db, ana["id"], 30, dias_atras=60)

    assert (await listar(cliente, admin, periodo="7d"))["conductores"][0]["viajes"] == 1
    assert (await listar(cliente, admin, periodo="7d"))["conductores"][0]["drivescore"] == 95
    assert (await listar(cliente, admin, periodo="30d"))["conductores"][0]["viajes"] == 2
    assert (await listar(cliente, admin, periodo="todo"))["conductores"][0]["viajes"] == 3
    # Fuera del periodo el conductor sigue en la lista, sin viajes
    await crear_viaje(db, (await registrar_conductor(cliente, admin, 2, "Beto"))["id"], 70, dias_atras=45)
    beto = next(
        c for c in (await listar(cliente, admin, periodo="7d"))["conductores"] if c["nombre"] == "Beto"
    )
    assert beto["viajes"] == 0 and beto["drivescore"] is None

    resumen = (await cliente.get(RESUMEN, headers=cabecera(admin), params={"periodo": "7d"})).json()
    assert resumen["drivescore_promedio"] == 95


async def test_busqueda_orden_y_paginacion(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    ids = {}
    for numero, (nombre, puntaje, viajes) in enumerate(
        [("Carla", 95, 1), ("Diego", 55, 3), ("Elena", 75, 2), ("Fabio", None, 0)], start=1
    ):
        ids[nombre] = (await registrar_conductor(cliente, admin, numero, nombre))["id"]
        for i in range(viajes):
            await crear_viaje(db, ids[nombre], puntaje, dias_atras=numero + i)

    def nombres(pagina):
        return [c["nombre"] for c in pagina["conductores"]]

    assert nombres(await listar(cliente, admin, orden="drivescore", direccion="desc")) == [
        "Carla", "Elena", "Diego", "Fabio",
    ]
    # Sin viajes siempre al final, también en ascendente
    assert nombres(await listar(cliente, admin, orden="drivescore", direccion="asc")) == [
        "Diego", "Elena", "Carla", "Fabio",
    ]
    assert nombres(await listar(cliente, admin, orden="viajes", direccion="desc"))[:3] == [
        "Diego", "Elena", "Carla",
    ]
    assert nombres(await listar(cliente, admin, orden="ultimo_viaje", direccion="desc")) == [
        "Carla", "Diego", "Elena", "Fabio",
    ]

    assert nombres(await listar(cliente, admin, q="ele")) == ["Elena"]
    assert nombres(await listar(cliente, admin, q="CONDUCTOR3@")) == ["Elena"]
    # Los comodines de LIKE se tratan como texto
    assert (await listar(cliente, admin, q="%"))["total"] == 0

    primera = await listar(cliente, admin, orden="drivescore", limite=3, pagina=1)
    segunda = await listar(cliente, admin, orden="drivescore", limite=3, pagina=2)
    assert primera["total"] == segunda["total"] == 4
    assert nombres(primera) == ["Carla", "Elena", "Diego"]
    assert nombres(segunda) == ["Fabio"]

    assert (await cliente.get(CONDUCTORES, headers=cabecera(admin), params={"orden": "x"})).status_code == 422


# --- Registro y contraseña temporal -----------------------------------------


async def test_registrar_conductor_y_entrar_con_la_temporal(cliente, db, registrar_empresa):
    admin = await registrar_empresa(1)
    uno = await registrar_conductor(cliente, admin, 1)
    dos = await registrar_conductor(cliente, admin, 2)
    assert uno["contrasenia_temporal"] != dos["contrasenia_temporal"]

    usuario = await db.get(Usuario, uno["id"])
    assert usuario.empresa_id == admin["usuario"]["empresa"]["id"]
    assert usuario.debe_cambiar_contrasenia is True
    assert usuario.contrasenia_hash != uno["contrasenia_temporal"]

    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": uno["email"], "contrasenia": uno["contrasenia_temporal"]},
    )
    assert login.status_code == 200
    assert login.json()["usuario"]["empresa"]["id"] == admin["usuario"]["empresa"]["id"]
    # La contraseña de otro conductor no sirve
    cruzada = await cliente.post(
        "/api/v1/auth/login",
        json={"email": uno["email"], "contrasenia": dos["contrasenia_temporal"]},
    )
    assert cruzada.status_code == 401


async def test_correo_repetido_responde_409(cliente, registrar_empresa):
    admin_a = await registrar_empresa(1)
    admin_b = await registrar_empresa(2)
    await registrar_conductor(cliente, admin_a, 1)
    for sesion in (admin_a, admin_b):
        # También si el correo es de un conductor de otra empresa o escrito en mayúsculas
        repetido = await cliente.post(
            CONDUCTORES,
            headers=cabecera(sesion),
            json={"nombre": "Otro", "email": "Conductor1@Empresa.example.com", "telefono": "71000000"},
        )
        assert repetido.status_code == 409
        assert repetido.json()["detail"] == "Este correo ya está registrado"


async def test_registro_valida_los_datos(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    respuesta = await cliente.post(
        CONDUCTORES,
        headers=cabecera(admin),
        json={"nombre": " ", "email": "no-es-correo", "telefono": "123"},
    )
    assert respuesta.status_code == 422
    campos = {e["campo"] for e in respuesta.json()["errores"]}
    assert {"nombre", "email", "telefono"} <= campos


async def test_regenerar_contrasenia_temporal(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    conductor = await registrar_conductor(cliente, admin, 1)
    nueva = await cliente.post(
        f"{CONDUCTORES}/{conductor['id']}/contrasenia-temporal", headers=cabecera(admin)
    )
    assert nueva.status_code == 200
    clave = nueva.json()["contrasenia_temporal"]
    assert clave != conductor["contrasenia_temporal"]

    vieja = await cliente.post(
        "/api/v1/auth/login",
        json={"email": conductor["email"], "contrasenia": conductor["contrasenia_temporal"]},
    )
    assert vieja.status_code == 401
    nueva_login = await cliente.post(
        "/api/v1/auth/login", json={"email": conductor["email"], "contrasenia": clave}
    )
    assert nueva_login.status_code == 200

    inexistente = await cliente.post(f"{CONDUCTORES}/9999/contrasenia-temporal", headers=cabecera(admin))
    assert inexistente.status_code == 404


async def test_no_se_puede_regenerar_la_contrasenia_de_un_admin(cliente, registrar_empresa):
    admin = await registrar_empresa(1)
    propio = admin["usuario"]["id"]
    respuesta = await cliente.post(
        f"{CONDUCTORES}/{propio}/contrasenia-temporal", headers=cabecera(admin)
    )
    assert respuesta.status_code == 404


@pytest.mark.parametrize("ruta", [RESUMEN, CONDUCTORES])
async def test_periodo_invalido_responde_422(cliente, registrar_empresa, ruta):
    admin = await registrar_empresa(1)
    respuesta = await cliente.get(ruta, headers=cabecera(admin), params={"periodo": "1h"})
    assert respuesta.status_code == 422
