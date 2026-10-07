# Despliegue en AWS (EC2 + Docker Compose)

Todo DriveSense corre en una sola EC2 con `docker compose`:

```
Internet :80/:443 ──► caddy ──┬─ /api/*, /health, /docs ──► backend (uvicorn :8000)
                              └─ el resto ───────────────► panel (build de Vite)
                     backend ──► db (PostgreSQL 17, volumen postgres_data)
```

- **Caddy** saca y renueva el certificado HTTPS solo (Let's Encrypt) y redirige HTTP a HTTPS.
- La API y el panel comparten dominio: el panel llama a `https://<dominio>/api/v1`.
- Solo Caddy publica puertos; `backend` y `db` quedan en la red interna de Docker.
- Las migraciones de Alembic se aplican solas cada vez que arranca el backend.

Archivos: `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/.env.example`, `deploy/actualizar.sh`, `deploy/backup.sh`, el `Dockerfile` del backend y el `Dockerfile` + `deploy/Caddyfile` de `drivesense-frontend`.

## 1. Crear la instancia

1. **IP elástica**: EC2 → Elastic IPs → *Allocate*. Anotarla (p. ej. `3.85.120.14`).
2. **Security group** `drivesense-web`, reglas de entrada:
   | Puerto | Origen | Para qué |
   |---|---|---|
   | 22 (SSH) | *My IP* | administrar la instancia |
   | 80 (HTTP) | 0.0.0.0/0 | validación del certificado y redirección a HTTPS |
   | 443 (HTTPS) | 0.0.0.0/0 | la API y el panel |
3. **Instancia**: Ubuntu Server 24.04 LTS, `t3.small` (2 GB; con `t3.micro` el build del panel puede quedarse sin memoria), disco gp3 de 20 GB, el security group de arriba y un key pair nuevo (`.pem`).
4. Asociar la IP elástica a la instancia.
5. **Dominio** con sslip.io: la IP con guiones, `3-85-120-14.sslip.io`. No hay que registrar nada: ya resuelve a esa IP. Comprobarlo con `nslookup 3-85-120-14.sslip.io`.

## 2. Preparar la instancia

```bash
ssh -i drivesense.pem ubuntu@3.85.120.14

# Docker Engine + plugin compose (script oficial)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit   # volver a entrar para que tome el grupo docker
```

Opcional pero recomendado en `t3.small`: 2 GB de swap para los builds.

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. Clonar y configurar

Los dos repos van **uno al lado del otro** (el compose construye el panel desde `../../drivesense-frontend`):

```bash
mkdir -p ~/drivesense && cd ~/drivesense
git clone <url-del-repo-backend> drivesense-backend
git clone <url-del-repo-frontend> drivesense-frontend

cd drivesense-backend/deploy
cp .env.example .env
nano .env
```

En `.env`:
- `DOMINIO=3-85-120-14.sslip.io` (sin `https://`).
- `POSTGRES_PASSWORD`: solo letras y números (va dentro de la URL de conexión). Generarla con `openssl rand -hex 24`.
- `JWT_SECRET`: `openssl rand -base64 32`. **Uno nuevo**, distinto del de desarrollo.
- `PRUEBAS_CORREO`: el correo de la cuenta de pruebas (paso 5), o vacío para desactivar el modo pruebas.

## 4. Levantar

```bash
docker compose up -d --build     # la primera vez tarda unos minutos
docker compose ps                # db y backend "healthy", panel y caddy "Up"
docker compose logs -f backend   # migraciones aplicadas y "Application startup complete"
```

Comprobar:

```bash
curl https://3-85-120-14.sslip.io/health
# {"status":"ok","servicio":"DriveSense API","base_de_datos":"conectada"}
```

Y abrir `https://3-85-120-14.sslip.io` en el navegador: tiene que cargar la landing del panel con candado.

Si el certificado falla, ver `docker compose logs caddy`: casi siempre es el puerto 80 cerrado en el security group o el dominio mal escrito en `.env`. Let's Encrypt limita los intentos fallidos, así que corregir antes de reintentar muchas veces.

## 5. Cuenta de pruebas

1. En el panel, **Registrar empresa** con el correo que se puso en `PRUEBAS_CORREO`.
2. Si `PRUEBAS_CORREO` se completó después de levantar: `docker compose up -d backend` para que lo tome.
3. Al iniciar sesión con esa cuenta, Inicio muestra el modo pruebas.

## 6. App (APK para los testers)

En la PC de desarrollo, desde `drivesense-mobile`:

```bash
flutter build apk --release --dart-define=API_URL=https://3-85-120-14.sslip.io
```

El APK queda en `build/app/outputs/flutter-apk/app-release.apk` (ver el CLAUDE.md de la app).

## Operación

| Tarea | Comando (desde `deploy/`) |
|---|---|
| Actualizar a lo último de `main` | `bash actualizar.sh` |
| Ver estado | `docker compose ps` |
| Logs | `docker compose logs -f backend` (o `caddy`, `db`) |
| Reiniciar la API | `docker compose restart backend` |
| Cambiar variables del `.env` | editar y `docker compose up -d` |
| Consola SQL | `docker compose exec db psql -U drivesense -d drivesense_db` |
| Apagar (conserva datos) | `docker compose down` |

**Nunca** `docker compose down -v` en el servidor: `-v` borra los volúmenes, es decir, la base de datos y los certificados.

### Copias de seguridad

`bash backup.sh` deja un `pg_dump` en `deploy/backups/` y conserva los 14 últimos. Programarlo a diario con `crontab -e`:

```
0 3 * * * bash /home/ubuntu/drivesense/drivesense-backend/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```

Los backups viven en el mismo disco que la base: de vez en cuando bajarlos a la PC (`scp`) o crear un snapshot del volumen EBS desde la consola.

Restaurar un backup (reemplaza los datos actuales):

```bash
docker compose stop backend
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/<archivo>.dump
docker compose start backend
```

## Probar el stack en local

Con Docker Desktop, desde `deploy/`, un `.env` con `DOMINIO=localhost` y `docker compose up -d --build`. Caddy usa un certificado interno (el navegador avisa; `curl -k`). Necesita los puertos 80 y 443 libres. Al terminar, `docker compose down -v` borra los datos de prueba.
