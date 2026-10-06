import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TipoEvento(str, enum.Enum):
    """Eventos de riesgo que detecta la app (HU-10 a HU-13)."""

    frenada_brusca = "frenada_brusca"
    aceleracion_severa = "aceleracion_severa"
    giro_agresivo = "giro_agresivo"
    exceso_velocidad = "exceso_velocidad"


class Evento(Base):
    """Evento de riesgo de un recorrido finalizado (HU-15)."""

    __tablename__ = "eventos"
    __table_args__ = (
        # La duración y la velocidad máxima son solo del exceso de velocidad
        CheckConstraint(
            "(tipo = 'exceso_velocidad') = "
            "(duracion_s IS NOT NULL AND velocidad_maxima_kmh IS NOT NULL)"
            " AND (tipo = 'exceso_velocidad' OR"
            " (duracion_s IS NULL AND velocidad_maxima_kmh IS NULL))",
            name="ck_eventos_exceso_con_duracion",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recorrido_id: Mapped[int] = mapped_column(ForeignKey("recorridos.id"), index=True)
    tipo: Mapped[TipoEvento] = mapped_column(Enum(TipoEvento, name="tipo_evento"))
    # Hora del teléfono en que se detectó (en el exceso, el inicio del tramo)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    # Antes de la maniobra; en el giro, durante el giro; en el exceso, al empezar
    velocidad_kmh: Mapped[float] = mapped_column(Float)
    # m/s² en frenadas, aceleraciones y giros; m/s sobre el límite en el exceso
    intensidad: Mapped[float] = mapped_column(Float)
    # Solo en el exceso de velocidad
    duracion_s: Mapped[float | None] = mapped_column(Float)
    velocidad_maxima_kmh: Mapped[float | None] = mapped_column(Float)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
