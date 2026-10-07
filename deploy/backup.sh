#!/usr/bin/env bash
# Copia de seguridad de la base de datos en deploy/backups/ (formato custom de pg_dump).
# Conserva los últimos 14 archivos. Restaurar: ver docs/despliegue.md.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p backups

archivo="backups/drivesense_$(date +%Y%m%d_%H%M%S).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$archivo"
echo "Backup creado: $archivo"

ls -1t backups/*.dump | tail -n +15 | xargs -r rm --
