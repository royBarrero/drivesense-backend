import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.flotas.models import Empresa


class RolUsuario(str, enum.Enum):
    conductor = "conductor"
    admin_empresa = "admin_empresa"


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        # Un admin de empresa siempre pertenece a una empresa
        CheckConstraint(
            "rol <> 'admin_empresa' OR empresa_id IS NOT NULL",
            name="ck_usuarios_admin_requiere_empresa",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nulo para el conductor individual
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    telefono: Mapped[str] = mapped_column(String(20))
    contrasenia_hash: Mapped[str] = mapped_column(String(255))
    rol: Mapped[RolUsuario] = mapped_column(Enum(RolUsuario, name="rol_usuario"))
    debe_cambiar_contrasenia: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Se carga con JOIN junto al usuario (en async no hay carga perezosa)
    empresa: Mapped["Empresa | None"] = relationship(lazy="joined")
