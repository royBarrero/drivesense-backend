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
    func,
    text,
)
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
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
