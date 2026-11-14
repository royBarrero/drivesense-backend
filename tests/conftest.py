"""Infraestructura de pruebas con base de datos.

Usa una base aparte (`drivesense_test`, en el mismo servidor que `DATABASE_URL`) que se
crea y se migra con Alembic al empezar la sesión; los datos de desarrollo no se tocan.
El motor de la app se apunta a ella ANTES de importar `app`, por eso este bloque va arriba.
Las pruebas sin base de datos no la usan y siguen corriendo igual.
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE_BD_PRUEBAS = "drivesense_test"


def _url_pruebas() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(RAIZ / ".env").get("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DATABASE_URL para derivar la base de pruebas")
    return make_url(url).set(database=NOMBRE_BD_PRUEBAS).render_as_string(hide_password=False)


URL_PRUEBAS = _url_pruebas()
# Las variables de entorno pesan más que el .env en pydantic-settings
os.environ["DATABASE_URL"] = URL_PRUEBAS
os.environ.setdefault("JWT_SECRET", dotenv_values(RAIZ / ".env").get("JWT_SECRET") or "secreto-de-pruebas")
# El modo pruebas temporal no debe activarse en los tests
os.environ["PRUEBAS_CORREO"] = ""

import asyncpg  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


async def _crear_base_si_falta() -> None:
    url = make_url(URL_PRUEBAS)
    conexion = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database="postgres",
    )
    try:
        existe = await conexion.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", NOMBRE_BD_PRUEBAS
        )
        if not existe:
            await conexion.execute(f'CREATE DATABASE "{NOMBRE_BD_PRUEBAS}"')
    finally:
        await conexion.close()


@pytest_asyncio.fixture(scope="session")
async def base_de_datos():
    """Crea la base de pruebas y la deja en la última migración."""
    await _crear_base_si_falta()
    resultado = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=RAIZ,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"No se pudo migrar la base de pruebas:\n{resultado.stderr}")
    yield


@pytest_asyncio.fixture
async def db(base_de_datos):
    """Sesión de BD con las tablas vacías al empezar cada prueba."""
    from app.core.database import SessionLocal, engine

    async with engine.begin() as conexion:
        tablas = await conexion.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        await conexion.execute(text(f"TRUNCATE {tablas.scalar()} RESTART IDENTITY CASCADE"))
    async with SessionLocal() as sesion:
        yield sesion


@pytest_asyncio.fixture
async def cliente(db):
    """Cliente HTTP contra la app real y la base de pruebas."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://prueba") as c:
        yield c


@pytest.fixture
def registrar_empresa(cliente):
    """Registra una empresa con su admin por la API y devuelve su sesión (token y usuario)."""

    async def _registrar(numero: int) -> dict:
        respuesta = await cliente.post(
            "/api/v1/auth/empresa/registro",
            json={
                "empresa": {
                    "nombre": f"Empresa {numero}",
                    "tipo": "flota",
                    "email_contacto": f"contacto{numero}@empresa.example.com",
                    "telefono": "70000000",
                },
                "administrador": {
                    "nombre": f"Admin {numero}",
                    "email": f"admin{numero}@empresa.example.com",
                    "telefono": "70000001",
                    "contrasenia": "Clave1234",
                },
            },
        )
        assert respuesta.status_code == 201, respuesta.text
        return respuesta.json()

    return _registrar


def cabecera(sesion: dict) -> dict:
    return {"Authorization": f"Bearer {sesion['access_token']}"}
