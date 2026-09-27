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
from app.core.errores import manejar_error_validacion

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
    # El token viaja en el header Authorization, no en cookies
    allow_credentials=False,
)

app.add_exception_handler(RequestValidationError, manejar_error_validacion)

app.include_router(auth_router, prefix="/api/v1")


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