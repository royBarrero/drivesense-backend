"""Importa todos los modelos para registrarlos en Base.metadata.

Lo usan alembic/env.py (autogenerate) y app/main.py (resolución de FK).
Cada modelo nuevo debe agregarse aquí.
"""

from app.auth.models import Usuario  # noqa: F401
from app.flotas.models import Empresa, EstiloConductor  # noqa: F401
from app.gamificacion.models import DesafioSemanal, Experiencia, LogroUsuario  # noqa: F401
from app.pruebas.models import ValidacionEvento  # noqa: F401  (temporal: modo pruebas)
from app.recomendaciones.models import Recomendacion  # noqa: F401
from app.recorridos.models import Recorrido  # noqa: F401
from app.telemetria.models import Evento  # noqa: F401
