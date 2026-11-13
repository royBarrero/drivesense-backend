from pydantic import BaseModel, ConfigDict

from app.recomendaciones.models import TipoRecomendacion


class RecomendacionSalida(BaseModel):
    """Recomendación de un recorrido (HU-18): título corto y texto con una acción."""

    model_config = ConfigDict(from_attributes=True)

    tipo: TipoRecomendacion
    titulo: str
    texto: str
