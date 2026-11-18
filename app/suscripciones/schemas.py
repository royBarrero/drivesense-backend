from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.suscripciones.planes import PlanSuscripcion


class FuncionesPlan(BaseModel):
    exportar: bool
    eventos_mapa_patrones: bool
    tablero_completo: bool


class DetallePlan(BaseModel):
    plan: PlanSuscripcion
    nombre: str
    # Nulo = sin tope
    limite_conductores: int | None
    # Ventana de historial; ambos nulos = historial completo
    historial_dias: int | None
    historial_meses: int | None
    funciones: FuncionesPlan
    precio_bs: int
    # Nulo = no vence (Gratis)
    duracion_meses: int | None


class PlanActual(BaseModel):
    detalle: DetallePlan
    estado: Literal["activo", "por_vencer"]
    fecha_inicio: datetime | None
    fecha_vencimiento: datetime | None
    dias_restantes: int | None
    conductores_usados: int
    # True cuando el plan de pago está a pocos días de vencer
    aviso_vencimiento: bool


class CambioPlanEntrada(BaseModel):
    plan: PlanSuscripcion
