import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TipoRecomendacion(str, enum.Enum):
    """Sobre qué trata la recomendación: un tipo de evento o un viaje sin eventos."""

    frenada_brusca = "frenada_brusca"
    aceleracion_severa = "aceleracion_severa"
    giro_agresivo = "giro_agresivo"
    exceso_velocidad = "exceso_velocidad"
    sin_eventos = "sin_eventos"


class Recomendacion(Base):
    """Recomendación de un recorrido finalizado (HU-18).

    `orden` 0 es la principal; las secundarias (HU-19) siguen con 1, 2… Se guarda
    el texto ya armado: un cambio en las plantillas no altera los viajes anteriores.
    """

    __tablename__ = "recomendaciones"
    __table_args__ = (
        UniqueConstraint("recorrido_id", "orden", name="uq_recomendaciones_recorrido_orden"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # El único (recorrido_id, orden) también sirve de índice para buscar por recorrido
    recorrido_id: Mapped[int] = mapped_column(ForeignKey("recorridos.id"))
    orden: Mapped[int] = mapped_column(SmallInteger)
    tipo: Mapped[TipoRecomendacion] = mapped_column(
        Enum(TipoRecomendacion, name="tipo_recomendacion")
    )
    titulo: Mapped[str] = mapped_column(String(60))
    texto: Mapped[str] = mapped_column(String(300))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
