import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TipoEmpresa(str, enum.Enum):
    flota = "flota"
    aseguradora = "aseguradora"


class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(150))
    tipo: Mapped[TipoEmpresa] = mapped_column(Enum(TipoEmpresa, name="tipo_empresa"))
    email_contacto: Mapped[str] = mapped_column(String(255))
    telefono: Mapped[str] = mapped_column(String(20))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
