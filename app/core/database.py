from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Motor de conexión: administra el pool de conexiones a PostgreSQL
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,  # verifica que la conexión siga viva antes de usarla
)

# Fábrica de sesiones: cada petición a la API obtiene su propia sesión
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Clase base de la que heredarán todos los modelos (tablas)."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Entrega una sesión de BD por petición y la cierra al terminar."""
    async with SessionLocal() as session:
        yield session