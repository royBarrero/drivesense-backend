import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.suscripciones.planes import PlanSuscripcion


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
    # Zona IANA de la empresa: de ella salen los periodos (7 días, 30 días) del panel
    zona_horaria: Mapped[str] = mapped_column(
        String(50), default="America/La_Paz", server_default="America/La_Paz"
    )
    # Plan contratado (HU-28). Un plan de pago vencido vale como Gratis: ver `plan_efectivo`
    plan: Mapped[PlanSuscripcion] = mapped_column(
        Enum(PlanSuscripcion, name="plan_suscripcion"),
        default=PlanSuscripcion.gratis,
        server_default="gratis",
    )
    plan_inicio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_vencimiento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EstiloConduccion(str, enum.Enum):
    """Estilo de conducción de un conductor, aprendido con K-means (ver `app/flotas/estilo.py`).

    Salvo `sin_datos`, el orden de la lista es el de riesgo, de menor a mayor (`ORDEN_ESTILOS`)."""

    prudente = "prudente"
    intermedio = "intermedio"
    brusco_curvas = "brusco_curvas"
    acelerador = "acelerador"
    brusco_frenar = "brusco_frenar"
    veloz = "veloz"
    sin_datos = "sin_datos"


class EstiloConductor(Base):
    """Último estilo calculado de cada conductor de flota y su alerta de cambio."""

    __tablename__ = "estilos_conductor"

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), primary_key=True)
    estilo: Mapped[EstiloConduccion] = mapped_column(
        Enum(EstiloConduccion, name="estilo_conduccion")
    )
    # Desde cuándo está en este estilo
    fecha_estilo: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Solo si llegó a `estilo` desde uno de menor riesgo: el anterior y cuándo cambió
    estilo_anterior: Mapped[EstiloConduccion | None] = mapped_column(
        Enum(EstiloConduccion, name="estilo_conduccion")
    )
    fecha_alerta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True))
