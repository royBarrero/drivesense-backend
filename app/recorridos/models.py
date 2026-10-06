import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EstadoRecorrido(str, enum.Enum):
    en_curso = "en_curso"
    finalizado = "finalizado"
    # Demasiado corto (duración o distancia) para considerarse un viaje
    descartado = "descartado"


class Recorrido(Base):
    __tablename__ = "recorridos"
    __table_args__ = (
        # Un conductor solo puede tener un recorrido en curso a la vez
        Index(
            "uq_recorridos_usuario_en_curso",
            "usuario_id",
            unique=True,
            postgresql_where=text("estado = 'en_curso'"),
        ),
        # Un recorrido cerrado siempre tiene fecha de fin y métricas
        CheckConstraint(
            "estado = 'en_curso' OR ("
            "fecha_fin IS NOT NULL AND distancia_m IS NOT NULL AND duracion_s IS NOT NULL"
            " AND velocidad_maxima_kmh IS NOT NULL AND velocidad_promedio_kmh IS NOT NULL)",
            name="ck_recorridos_cerrado_con_metricas",
        ),
        # Puntajes (HU-15): de 0 a 100 cuando existen
        CheckConstraint(
            "(drivescore IS NULL OR drivescore BETWEEN 0 AND 100)"
            " AND (puntaje_frenadas IS NULL OR puntaje_frenadas BETWEEN 0 AND 100)"
            " AND (puntaje_aceleraciones IS NULL OR puntaje_aceleraciones BETWEEN 0 AND 100)"
            " AND (puntaje_giros IS NULL OR puntaje_giros BETWEEN 0 AND 100)"
            " AND (puntaje_velocidad IS NULL OR puntaje_velocidad BETWEEN 0 AND 100)",
            name="ck_recorridos_puntajes_0_100",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    # Empresa del conductor al iniciar el recorrido; nulo para el conductor individual
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    estado: Mapped[EstadoRecorrido] = mapped_column(Enum(EstadoRecorrido, name="estado_recorrido"))
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Campos de cierre: nulos mientras el recorrido está en curso
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distancia_m: Mapped[float | None] = mapped_column(Float)
    duracion_s: Mapped[int | None] = mapped_column(Integer)
    velocidad_maxima_kmh: Mapped[float | None] = mapped_column(Float)
    velocidad_promedio_kmh: Mapped[float | None] = mapped_column(Float)
    lat_inicio: Mapped[float] = mapped_column(Float)
    lon_inicio: Mapped[float] = mapped_column(Float)
    lat_fin: Mapped[float | None] = mapped_column(Float)
    lon_fin: Mapped[float | None] = mapped_column(Float)
    # Puntos de la ruta (HU-08): solo en recorridos finalizados. Diferida: el
    # historial y /activo no la cargan; el detalle la pide con undefer()
    ruta: Mapped[list[dict] | None] = mapped_column(JSONB, deferred=True)
    # DriveScore y puntaje por categoría (HU-15): solo en recorridos finalizados
    drivescore: Mapped[int | None] = mapped_column(SmallInteger)
    puntaje_frenadas: Mapped[int | None] = mapped_column(SmallInteger)
    puntaje_aceleraciones: Mapped[int | None] = mapped_column(SmallInteger)
    puntaje_giros: Mapped[int | None] = mapped_column(SmallInteger)
    puntaje_velocidad: Mapped[int | None] = mapped_column(SmallInteger)
    # Teléfono y versión de la app que registraron el viaje; nulos si la app no los envía
    dispositivo_modelo: Mapped[str | None] = mapped_column(String(100))
    dispositivo_android: Mapped[str | None] = mapped_column(String(20))
    version_app: Mapped[str | None] = mapped_column(String(20))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
