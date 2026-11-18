import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    SmallInteger,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FuenteExperiencia(str, enum.Enum):
    """De dónde salen los puntos: un viaje (HU-21), un desafío semanal completado o el
    bono por completar los 3 de la semana (HU-23)."""

    recorrido = "recorrido"
    desafio = "desafio"
    bono_semana = "bono_semana"


class CodigoLogro(str, enum.Enum):
    """Logros de la HU-21; nombres, condiciones y metas en `LOGROS` (service.py)."""

    primer_viaje = "primer_viaje"
    viaje_limpio = "viaje_limpio"
    puntaje_excelente = "puntaje_excelente"
    buena_racha = "buena_racha"
    racha_imparable = "racha_imparable"
    constante = "constante"
    veterano = "veterano"
    cien_kilometros = "cien_kilometros"
    gran_ruta = "gran_ruta"


class TipoDesafio(str, enum.Enum):
    """Los 3 desafíos de cada semana (HU-22): uno de cada tipo."""

    constancia = "constancia"
    calidad = "calidad"
    punto_debil = "punto_debil"


class CodigoDesafio(str, enum.Enum):
    """Desafíos de la HU-22; nombres, condiciones y recompensas en `DESAFIOS` (service.py)."""

    semana_activa = "semana_activa"
    kilometros_seguros = "kilometros_seguros"
    viaje_perfecto = "viaje_perfecto"
    racha_excelente = "racha_excelente"
    promedio_muy_bueno = "promedio_muy_bueno"
    sin_frenadas_bruscas = "sin_frenadas_bruscas"
    sin_aceleraciones_severas = "sin_aceleraciones_severas"
    sin_giros_agresivos = "sin_giros_agresivos"
    sin_excesos_velocidad = "sin_excesos_velocidad"


class Experiencia(Base):
    """Registro de puntos de experiencia (HU-21). El nivel sale de la suma: como las
    filas nunca se borran, el nivel nunca baja."""

    __tablename__ = "experiencias"
    __table_args__ = (
        CheckConstraint("puntos > 0", name="ck_experiencias_puntos_positivos"),
        # Un viaje suma una sola vez
        UniqueConstraint("recorrido_id", name="uq_experiencias_recorrido"),
        # Un desafío da sus puntos una vez y el bono de su semana, otra (HU-23)
        UniqueConstraint("desafio_id", "fuente", name="uq_experiencias_desafio_fuente"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    fuente: Mapped[FuenteExperiencia] = mapped_column(
        Enum(FuenteExperiencia, name="fuente_experiencia")
    )
    # Nula en otras fuentes
    recorrido_id: Mapped[int | None] = mapped_column(ForeignKey("recorridos.id"))
    # El desafío completado (HU-23); en el bono, el último que completó la semana
    desafio_id: Mapped[int | None] = mapped_column(ForeignKey("desafios_semanales.id"))
    puntos: Mapped[int] = mapped_column(SmallInteger)
    # Cuándo se ganaron; en un viaje, su fecha_fin; en un desafío o bono, cuándo se completó
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LogroUsuario(Base):
    """Logro desbloqueado por un conductor (HU-21); nunca se pierde."""

    __tablename__ = "logros_usuario"
    __table_args__ = (
        UniqueConstraint("usuario_id", "codigo", name="uq_logros_usuario_codigo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # El único (usuario_id, codigo) también sirve de índice por usuario
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    codigo: Mapped[CodigoLogro] = mapped_column(Enum(CodigoLogro, name="codigo_logro"))
    # El viaje que cumplió la condición
    recorrido_id: Mapped[int | None] = mapped_column(ForeignKey("recorridos.id"))
    # fecha_fin de ese viaje (también al calcularlo desde el historial)
    fecha_desbloqueo: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DesafioSemanal(Base):
    """Desafío asignado a un conductor para una semana (HU-22). Se generan los 3 en
    la primera consulta de la semana y no cambian hasta que termina."""

    __tablename__ = "desafios_semanales"
    __table_args__ = (
        # Uno de cada tipo por semana; también sirve de índice por usuario
        UniqueConstraint("usuario_id", "semana_inicio", "tipo", name="uq_desafios_usuario_semana_tipo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    # Lunes 00:00 en la zona del teléfono; el fin es 7 días después
    semana_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    semana_fin: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tipo: Mapped[TipoDesafio] = mapped_column(Enum(TipoDesafio, name="tipo_desafio"))
    codigo: Mapped[CodigoDesafio] = mapped_column(Enum(CodigoDesafio, name="codigo_desafio"))
    # Se guardan: cambiar el catálogo no altera las semanas ya generadas
    meta: Mapped[int] = mapped_column(SmallInteger)
    puntos: Mapped[int] = mapped_column(SmallInteger)
    # Cuándo se completó (HU-23); nula = pendiente. Una vez completado no se pierde
    fecha_completado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
