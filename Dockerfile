FROM python:3.14-slim

# OMP/OPENBLAS: el K-means de los estilos (HU-25) usa pocos cientos de filas; con un solo
# hilo de BLAS no se reserva memoria por hilo (medido: 995 MB -> 255 MB de memoria privada)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
# Datos de ejemplo para la presentación (manual: docker compose exec backend python scripts/seed_flota_ejemplo.py)
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 drivesense
USER drivesense

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# Las migraciones se aplican en cada arranque; si fallan, el contenedor no levanta
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
