from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

app = FastAPI(
    title="DriveSense API",
    description="Backend de telemetría vehicular y análisis de conducción",
    version="0.1.0",
)


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