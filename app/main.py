from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models  # noqa: F401  (registra todos los modelos antes de usarlos)
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.database import get_db
from app.core.errores import (
    ErrorDeNavegacion,
    ErrorDePlan,
    manejar_error_de_navegacion,
    manejar_error_de_plan,
    manejar_error_validacion,
)
from app.flotas.router import router as flotas_router
from app.gamificacion.router import router as gamificacion_router
from app.navegacion.router import router as navegacion_router
from app.pruebas.router import router as pruebas_router
from app.puntaje.router import router as puntaje_router
from app.recorridos.router import router as recorridos_router
from app.suscripciones.router import router as suscripciones_router

app = FastAPI(
    title="DriveSense API",
    description="Backend de telemetría vehicular y análisis de conducción",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.lista_cors_origenes,
    allow_methods=["*"],
    allow_headers=["*"],
    # El panel lee el nombre del archivo del reporte (HU-27)
    expose_headers=["Content-Disposition"],
    # El token viaja en el header Authorization, no en cookies
    allow_credentials=False,
)

app.add_exception_handler(RequestValidationError, manejar_error_validacion)
app.add_exception_handler(ErrorDePlan, manejar_error_de_plan)
app.add_exception_handler(ErrorDeNavegacion, manejar_error_de_navegacion)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(recorridos_router, prefix="/api/v1")
app.include_router(navegacion_router, prefix="/api/v1")
app.include_router(puntaje_router, prefix="/api/v1")
app.include_router(gamificacion_router, prefix="/api/v1")
app.include_router(flotas_router, prefix="/api/v1")
app.include_router(suscripciones_router, prefix="/api/v1")

# Temporal: modo pruebas, solo si hay cuenta de pruebas configurada
if settings.pruebas_correo.strip():
    app.include_router(pruebas_router, prefix="/api/v1")


@app.get("/health", tags=["Sistema"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Verifica que la API y la base de datos estén en funcionamiento."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "base_de_datos": "sin conexión"},
        )
    return {"status": "ok", "servicio": "DriveSense API", "base_de_datos": "conectada"}