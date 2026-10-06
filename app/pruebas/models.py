"""Modo pruebas (temporal): marcas de los testers sobre los eventos detectados.
Al retirar el módulo, una migración borra la tabla y su enum."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResultadoValidacion(str, enum.Enum):
    correcto = "correcto"
    falso = "falso"


class ValidacionEvento(Base):
    """Si un evento detectado por la app ocurrió de verdad, según el tester del viaje."""

    __tablename__ = "validaciones_eventos"

    evento_id: Mapped[int] = mapped_column(
        ForeignKey("eventos.id", ondelete="CASCADE"), primary_key=True
    )
    resultado: Mapped[ResultadoValidacion] = mapped_column(
        Enum(ResultadoValidacion, name="resultado_validacion")
    )
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
