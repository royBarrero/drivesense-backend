#!/usr/bin/env bash
# Trae los últimos cambios de los dos repos y reconstruye lo que haya cambiado.
# Las migraciones se aplican solas al arrancar el backend.
set -euo pipefail

cd "$(dirname "$0")"

git -C .. pull --ff-only
git -C ../../drivesense-frontend pull --ff-only

docker compose up -d --build
docker image prune -f
docker compose ps
