# drivesense-backend

API de DriveSense. El contexto general del proyecto (módulos, HU, roles, multitenancy) está en `docs/contexto-general.md` (versionado aquí; el `../CLAUDE.md` de la carpeta raíz solo lo importa).

## Estado actual

- Endpoints: `GET /health`, `POST /api/v1/auth/registro` (HU-01), `POST /api/v1/auth/login` (HU-02, solo conductores), `POST /api/v1/auth/empresa/registro` y `POST /api/v1/auth/empresa/login` (HU-03, panel web, solo admins) y `GET /api/v1/auth/yo` (incluye `empresa`, nula para el conductor individual). Recorridos: `POST /api/v1/recorridos` y `GET /api/v1/recorridos/activo` (HU-04, 204 si no hay ninguno en curso), `PATCH /api/v1/recorridos/{id}/finalizar` (HU-05; con la ruta, HU-08), `GET /api/v1/recorridos` (HU-06, historial) y `GET /api/v1/recorridos/{id}` (HU-06, detalle con la ruta, el DriveScore y los eventos con su posición, HU-29). Puntaje: `GET /api/v1/puntaje/historico` (HU-17) y `GET /api/v1/puntaje/comparativo` (HU-20). Recomendaciones (HU-18, HU-19): sin endpoint propio, van en la respuesta del PATCH y en el detalle. Los routers se registran en `app/main.py` con prefijo `/api/v1`.
- **Endpoints protegidos**: recibir `usuario: UsuarioActual` (`app/auth/dependencias.py`). Valida el `Authorization: Bearer` y devuelve el `Usuario` activo; sirve para todos los roles, así que las restricciones por rol y el filtro por `empresa_id` se aplican en cada endpoint/servicio.
- Recorridos: solo el rol `conductor` (dependencia `ConductorActual` en `app/recorridos/router.py`; otro rol → 403). Para los demás módulos del conductor hay una `ConductorActual` genérica en `app/auth/dependencias.py` (403 "Solo los conductores pueden usar esta función"); la de recorridos conserva su mensaje. Toda consulta filtra por `usuario_id` del conductor: un recorrido ajeno responde 404, igual que uno inexistente.
- Historial (HU-06): solo recorridos `finalizado`, del más reciente al más antiguo. Query `desde` (con zona horaria obligatoria: la app manda el inicio de "esta semana"/"este mes" según la zona del teléfono; filtra por `fecha_inicio`), `antes_de` (cursor: id del último de la página anterior) y `limite` (1–50, por defecto 20). Responde `{recorridos, siguiente, resumen}`; `siguiente` es nulo en la última página y `resumen` (`viajes`, `distancia_m`, `duracion_s` del periodo) solo viene en la primera. El cursor es por `id`: para un conductor coincide con el orden de `fecha_inicio`. El detalle responde 404 si el recorrido es ajeno, no existe o no está finalizado.
- Ruta (HU-08): `ruta` opcional en el `PATCH .../finalizar`, lista de `{lat, lon, fecha (con zona), velocidad_kmh}` (`PuntoRuta`), como máximo `MAXIMO_PUNTOS_RUTA` = 10 000 y en orden cronológico. Se guarda solo si el recorrido queda `finalizado` (descartado → nula). La columna es diferida: el historial y `/activo` no la cargan; el detalle la pide con `undefer` y responde `RecorridoDetalle`.
- Eventos y DriveScore (HU-15): `eventos` en el `PATCH .../finalizar` (`EventoEntrada`, `app/telemetria/schemas.py`), lista de `{tipo, fecha (con zona), lat, lon, velocidad_kmh, intensidad, duracion_s?, velocidad_maxima_kmh?}`; por defecto `[]` (una app sin eventos sigue funcionando), como máximo `MAXIMO_EVENTOS` = 2000. Validaciones: tipo conocido; el exceso exige `duracion_s` y `velocidad_maxima_kmh` y los demás tipos no los llevan (422 en `eventos.N`); cada `fecha` entre `fecha_inicio − 2 min` y `fecha_fin + 2 min` (`TOLERANCIA_RELOJ`; si no, 422 en `eventos`, validado en el servicio). Si el recorrido queda `finalizado`, guarda los eventos y el puntaje en el **mismo commit**; si queda `descartado`, ni eventos ni puntaje. La respuesta del PATCH (`RecorridoSalida`, también `/activo`) y el detalle (`RecorridoDetalle`) traen `drivescore` y `puntaje_frenadas`, `puntaje_aceleraciones`, `puntaje_giros`, `puntaje_velocidad` (nulos en curso, descartado o finalizado antes de HU-15). El historial (`RecorridoHistorial`) trae solo `drivescore`, sin las categorías (la app lo muestra en Inicio).
- Conteo y lista de eventos (HU-16, HU-29): el detalle (`RecorridoDetalle`) trae `eventos_por_tipo`, `{tipo: cantidad}` con los cuatro tipos (0 si no hubo; `contar_por_tipo` en `app/telemetria/service.py`), y `eventos` (`EventoSalida`: `tipo, fecha, lat, lon, velocidad_kmh, intensidad, duracion_s, velocidad_maxima_kmh`, ordenados por `fecha` e `id`) para el mapa del viaje en la app. Ambos salen de una sola consulta y son nulos si el recorrido no tiene DriveScore.
- Fórmula (`app/puntaje/service.py`, `calcular_puntaje`, pura; valores en `ParametrosPuntaje`):
  - Frenadas, aceleraciones y giros: `100 − resta × eventos × 10 / max(km, 5)`; resta por evento cada 10 km: **frenada 22, aceleración 18, giro 18**. Un viaje de menos de 5 km cuenta como de 5.
  - Velocidad: `100 − 2 × %` del tiempo del viaje en exceso (suma de `duracion_s` de los excesos, con tope en la duración del viaje).
  - DriveScore = 0,30 velocidad + 0,30 frenadas + 0,20 aceleraciones + 0,20 giros, con las categorías sin redondear. Todo limitado a 0–100 y redondeado al entero con 0,5 hacia arriba (`math.floor(x + 0.5)`, no `round`).
  - Ejemplo: 7,4 km, 1 frenada y 1 giro, sin exceso → frenadas 70, giros 76, **DriveScore 86**.
  - La calificación (Excelente 90–100, Muy bueno 75–89, Regular 60–74, Riesgoso < 60) la pone la app en cada viaje; el histórico la devuelve con los mismos rangos (`calificar`).
- Histórico (HU-17, `GET /puntaje/historico?desde=...`, solo conductores): periodo `[desde, ahora)`; `desde` con zona (la app manda la medianoche local), no futuro y como máximo 366 días atrás (422 en `desde`). Solo recorridos `finalizado` con `drivescore` del propio conductor. El periodo anterior es el de igual duración justo antes de `desde` (se trae en la misma consulta).
  - Lógica pura en `app/puntaje/service.py` (`resumir_historico` y sus funciones), con pruebas; `obtener_historico` hace las consultas.
  - Promedios (general, por categoría, de cada punto y `promedio_total`) ponderados por distancia y redondeados con `_redondear`. `tendencia` y la `diferencia` de cada categoría = actual − anterior; nulas si el periodo anterior no tiene viajes.
  - `puntos` según la duración (`agrupacion`): ≤ 8 días, uno por viaje; ≤ 31 días, uno por día con viajes; más, uno por semana. Los días y semanas son tramos de 24 h / 7 días contados desde `desde` (así no hace falta la zona); la `fecha` del punto es el inicio del tramo.
  - `mejor` / `peor`: mayor / menor DriveScore (empate → el más reciente); `peor` nulo con menos de 2 viajes.
  - `viajes_totales` y `promedio_total`: todos los viajes con puntaje del conductor, de cualquier fecha (la app los usa con menos de 3 viajes).
- Comparativo entre periodos (HU-20, `GET /puntaje/comparativo?periodo=semana|mes&desde=...`, solo conductores): periodos **calendario**; el actual es `[desde, ahora)` (lo que va) y el anterior, el periodo completo justo antes.
  - `desde` con la **zona del teléfono** (no en UTC): debe ser la medianoche de un lunes (semana) o de un día 1 (mes) en esa zona, y no futuro (si no, 422 en `desde`). El inicio anterior (`inicio_anterior`) es `desde − 7 días` o el día 1 del mes previo con la misma zona.
  - Solo recorridos `finalizado` con `drivescore` del propio conductor; una sola consulta trae los dos periodos con la cantidad de eventos de cada viaje (subconsulta sobre `eventos`).
  - **Mínimo `MINIMO_VIAJES_COMPARATIVO` = 2 viajes con puntaje en cada periodo**; si alguno no llega, `comparable = false` y las diferencias y destacados son nulos (`actual` y `anterior` vienen igual: la app muestra cuántos viajes lleva cada uno).
  - Cada periodo (`ResumenPeriodo`): `viajes`, `distancia_m`, `eventos`, `drivescore` y `categorias` (ponderados por distancia, como HU-17), `calificacion` y `eventos_por_10km` (un decimal, 0,5 hacia arriba); nulos sin viajes.
  - Cambios (`Cambio`: `diferencia` = actual − anterior y `resultado` `mejoro` | `empeoro` | `igual`): `drivescore` y cada categoría mejoran al subir; `eventos_por_10km`, al bajar. `viajes` y `distancia_m` van solo como diferencia, **sin sentido de mejora** (acordado: más viajes no es manejar mejor).
  - `mas_mejoro` / `mas_empeoro`: la categoría con la mayor subida / bajada; nula si ninguna subió / bajó. Empate → orden de riesgo (`CATEGORIAS_POR_RIESGO`: velocidad, frenadas, giros, aceleraciones).
  - Lógica pura (`comparar_periodos`, `resumir_periodo`, `comparar`, `destacadas`, `validar_inicio`) con pruebas; `obtener_comparativo` hace la consulta.
- Recomendación principal (HU-18, `app/recomendaciones/service.py`, reglas fijas sin IA):
  - Tipo: el de la categoría con menor puntaje (el mismo criterio que "Lo que más restó" de la app). **Empate → orden de riesgo** (`ORDEN_RIESGO`): `exceso_velocidad` > `frenada_brusca` > `giro_agresivo` > `aceleracion_severa` (la app desempata igual en `masResto`). Un tipo con eventos cuya categoría quedó en 100 por redondeo también cuenta, después de los que restaron. Sin eventos → `sin_eventos` (mensaje positivo). Descartado → ninguna.
  - Textos en `PLANTILLAS`: 3 variantes por tipo (y 3 positivas), cada una con título (≤ 40) y texto "una vez" / "varias veces" (`{n}`; el exceso también menciona el tiempo total, `{duracion}`, con `formatear_duracion`). Variante = `recorrido_id % 3`.
  - `generar_recomendaciones` (pura) devuelve **todas** ordenadas, una por tipo con eventos: la primera es la principal y las siguientes, las secundarias.
  - **Secundarias (HU-19)**: las de los demás tipos con eventos, en el mismo orden (menor puntaje; empate → riesgo). **Máximo `MAXIMO_SECUNDARIAS` = 2.** Nunca repiten el tipo de la principal (y por eso tampoco su texto). Sin secundarias si hubo eventos de un solo tipo, si no hubo eventos (la positiva va sola), si fue descartado o sin puntaje.
  - `filas` (pura) arma las filas a guardar: la principal (`orden` 0) y las secundarias (1, 2). Se guardan al finalizar, en el mismo commit que el puntaje.
  - Viajes anteriores (antes de HU-18, o finalizados con HU-18 y sin secundarias): `obtener_recomendaciones` las completa la primera vez que se consulta su detalle. `faltantes` (pura) decide qué falta por `orden`: **las guardadas no se tocan** y se omite una secundaria del mismo tipo que la principal guardada. Se insertan con `INSERT ... ON CONFLICT DO NOTHING` sobre el único (dos consultas simultáneas no chocan); si no falta nada, no escribe. Sin puntaje (antes de HU-15) → ninguna.
  - `recomendacion` (`RecomendacionSalida`: `tipo, titulo, texto`) y `recomendaciones_secundarias` (lista, `[]` si no hay) en la respuesta del PATCH (`RecorridoSalida`; nula / vacía en descartado y en `/activo`) y en el detalle (`RecorridoDetalle`). Las arma `agregar_recomendaciones` (`app/recorridos/service.py`). El historial no las trae.
- Códigos: 401 = no autenticado o credenciales inválidas; 403 = autenticado pero sin permiso (cuenta inactiva, rol incorrecto). En el login, el estado de la cuenta solo se revela si la contraseña es correcta, y un email inexistente se verifica contra `HASH_FICTICIO` para igualar tiempos.
- Cada `app/<modulo>/` (`auth`, `recorridos`, `telemetria`, `puntaje`, `recomendaciones`, `gamificacion`, `flotas`, `suscripciones`) sigue la convención `models.py` / `schemas.py` / `service.py` / `router.py`. Los errores de negocio se lanzan como `HTTPException` desde el servicio.
- Errores 422: `app/core/errores.py` los devuelve como `{"detail": "Datos inválidos", "errores": [{"campo", "mensaje"}]}`, en español y sin eco de los valores enviados. Un mensaje específico de un campo se escribe como `ValueError` en un `field_validator` del schema (se muestra tal cual); los tipos genéricos se traducen en `_MENSAJES`. Si la regla depende de la BD y se valida en el servicio, lanzar `error_de_campo(campo, mensaje)` para mantener el mismo formato.
- Registro y login comparten lógica en `app/auth/service.py` (`_crear_usuario`, `_iniciar_sesion` con el rol permitido como parámetro); no duplicarla. El registro de empresa inserta empresa + admin en un único `commit`.
- `Usuario.empresa` es una relación con `lazy="joined"` (en async no hay carga perezosa): toda consulta de `Usuario` trae su empresa con un JOIN.
- CORS: orígenes permitidos en `CORS_ORIGENES` (separados por comas). Solo lo necesita el panel web; la app móvil no.
- `app/core/security.py`: hash argon2 (pwdlib), creación y decodificación de JWT (PyJWT, payload `sub` = id como string, `rol`, `iat`, `exp`; expiración 7 días). No hay refresh token ni logout en el backend: la app borra el token.
- Pruebas: `pytest` (en `requirements.txt`); `python -m pytest` desde la raíz. Por ahora solo `tests/test_puntaje.py` (fórmula, histórico y comparativo), `tests/test_telemetria.py` (conteo por tipo), `tests/test_recorridos.py` (esquemas del historial y del detalle) y `tests/test_recomendaciones.py` (elección, textos y secundarias; importa `app.models` porque `filas` crea instancias del ORM), sin BD; los endpoints se prueban a mano con curl.

## Modo pruebas (temporal)

Herramienta para revisar en el panel web los viajes reales de los testers antes del despliegue. **No es parte del producto: se retira antes de producción.**

- Módulo `app/pruebas/`. El router se registra en `app/main.py` solo si `PRUEBAS_CORREO` tiene valor; vacío → las rutas no existen (404). Su única tabla es `validaciones_eventos` (`app/pruebas/models.py`, migración `5e7a9c3d1b20`, separada de las demás para poder revertirla sola).
- Acceso: una cuenta `admin_empresa` normal, compartida por el equipo, cuyo correo es `PRUEBAS_CORREO`. Cualquier otro usuario → 403 "Solo la cuenta de pruebas puede usar esta función". **Excepción deliberada** a la regla de filtrar por `empresa_id`: ve los viajes de todos los conductores.
- Endpoints (`/api/v1/pruebas`; `desde` opcional, con zona y no futuro, filtra por `fecha_inicio`):
  - `GET /acceso` → 204 (el panel lo usa para mostrar el modo pruebas).
  - `GET /resumen?desde=&usuario_id=`: `testers`, `testers_registrados` (conductores activos), `viajes`, `viajes_anterior` (periodo anterior de igual duración; nulo sin `desde`), `descartados`, `distancia_m`, `drivescore_promedio` (ponderado, `promedio_desde_sumas`) y `calificacion`, `eventos` y `eventos_por_10km` (un decimal, sobre la distancia de los viajes con puntaje; nulo si no hay). Todo cuenta solo los finalizados, salvo `descartados`.
  - `GET /recorridos?desde=&usuario_id=&eventos=con|sin&pagina=&limite=`: finalizados y descartados de todos, por `fecha_inicio` descendente, paginados **por número de página** (`{recorridos, total}`; el panel muestra "Mostrando X de N"). Cada item: `conductor {id, nombre, email}`, métricas, dispositivo, `drivescore`, `calificacion` y `eventos_por_tipo` (nulo sin puntaje). `eventos=sin` solo incluye viajes con puntaje (de los demás no se guardaron eventos).
  - `GET /recorridos/exportar` (mismos filtros, sin paginar): CSV UTF-8 con BOM (`COLUMNAS_CSV`, `fila_csv`).
  - `GET /recorridos/{id}`: detalle (`DetallePruebas`, extiende `RecorridoDetalle`) de un finalizado o descartado de cualquier conductor, con `conductor`, dispositivo y, en cada evento, `id` y `validacion`. Reutiliza `cargar_eventos` y `contar_eventos` de `app/recorridos/service.py`. En curso o inexistente → 404.
  - `PUT /eventos/{id}/validacion` `{resultado: correcto|falso}` (upsert) y `DELETE` (desmarcar, 204); evento inexistente → 404.
  - `GET /testers?desde=`: conductores activos (también sin viajes) con viajes, km, DriveScore promedio, eventos, eventos por 10 km y `ultimo_viaje` del periodo.
- Pruebas en `tests/test_pruebas.py`.
- **Para retirarlo:** una migración nueva que borre `validaciones_eventos` y el enum `resultado_validacion`; borrar `app/pruebas/` y `tests/test_pruebas.py`, su import en `app/models.py`, el import y el `include_router` de `app/main.py`, `pruebas_correo` de `app/core/config.py`, `PRUEBAS_CORREO` de `.env.example` y esta sección. El dispositivo de los recorridos **se queda** (es del modelo real).

## Entorno

- Python 3.14 (todo el equipo), virtualenv en `.venv/`.
- Ejecutar `uvicorn` y `alembic` **desde la raíz del backend**: `app/core/config.py` lee `.env` relativo al directorio actual.
- `requirements.txt` debe quedar en UTF-8: regenerarlo con `pip freeze > requirements.txt` desde Bash (en PowerShell 5.1 ese comando lo escribe en UTF-16 y Git lo trata como binario).

## Despliegue (Docker)

- Producción: una EC2 con `docker compose` (`deploy/docker-compose.yml`): `caddy` (HTTPS automático, único con puertos publicados) → `backend` (`/api/*`, `/health`, `/docs`) y `panel` (el resto); `db` es PostgreSQL 17 con el volumen `postgres_data`. Guía paso a paso en `docs/despliegue.md`.
- El compose construye el panel desde `../../drivesense-frontend`: los dos repos se clonan uno al lado del otro. Variables en `deploy/.env` (plantilla `deploy/.env.example`, ignorado por Git); el compose arma `DATABASE_URL` y `CORS_ORIGENES` a partir de ellas.
- `Dockerfile`: el contenedor ejecuta `alembic upgrade head` antes de uvicorn (2 workers), así que las migraciones se aplican en cada deploy. Una variable nueva del backend se agrega también al bloque `environment` del servicio `backend` y a `deploy/.env.example`.
- Scripts: `deploy/actualizar.sh` (pull de los dos repos y rebuild) y `deploy/backup.sh` (`pg_dump` en `deploy/backups/`).
- Probar en local: `.env` con `DOMINIO=localhost` y `docker compose up -d --build` desde `deploy/` (Caddy usa un certificado interno; `curl -k`). Borrar los datos con `docker compose down -v`.

## Base de datos y Alembic

- PostgreSQL 17, driver async `asyncpg`. `DATABASE_URL` usa el esquema `postgresql+asyncpg://`.
- `alembic/env.py` es la plantilla **async**; toma la URL de `settings.database_url` (la `sqlalchemy.url` de `alembic.ini` es un placeholder ignorado) y usa `Base.metadata` de `app/core/database.py`.
- **Todo modelo nuevo debe importarse en `app/models.py`** (agregador): lo importan `alembic/env.py` (para el autogenerate) y `app/main.py` (para que las FK entre módulos se resuelvan).
- Enums como tipos nativos de PostgreSQL (`Enum(..., name="...")`). Autogenerate no los elimina en `downgrade()`: añadir el `DROP TYPE` a mano (ver la primera migración).
- Revisar siempre la migración autogenerada antes de aplicarla (`alembic upgrade head`).
- `get_db()` entrega una `AsyncSession` con `expire_on_commit=False` y **no hace commit**: el servicio debe hacer `await session.commit()` explícitamente.

## Tablas

**empresas**: `id`, `nombre`, `tipo` (`flota` | `aseguradora`), `email_contacto`, `telefono` (NOT NULL), `activo`, `fecha_creacion`

**usuarios**: `id`, `empresa_id` (FK a empresas; nulo para conductor individual), `nombre`, `email` (único, guardado en minúsculas), `telefono` (NOT NULL), `contrasenia_hash`, `rol` (`conductor` | `admin_empresa`), `debe_cambiar_contrasenia`, `activo`, `fecha_creacion`
- Restricción: `rol = admin_empresa` exige `empresa_id` no nulo (CHECK `ck_usuarios_admin_requiere_empresa`).

**recorridos** (`app/recorridos/models.py`): `id`, `usuario_id` (FK), `empresa_id` (FK; empresa del conductor al iniciar, nula si es individual), `estado` (`en_curso` | `finalizado` | `descartado`), `fecha_inicio` (hora del servidor), `fecha_fin` (la envía la app; posterior al inicio y no futura, con 2 min de tolerancia), `distancia_m`, `duracion_s`, `velocidad_maxima_kmh`, `velocidad_promedio_kmh`, `lat_inicio`, `lon_inicio`, `lat_fin`, `lon_fin`, `ruta` (JSONB, nula; HU-08), `dispositivo_modelo` (100), `dispositivo_android` (20) y `version_app` (20) (nulos; la app los manda opcionalmente en el `POST /recorridos`), `fecha_creacion`
- Índice único parcial `uq_recorridos_usuario_en_curso`: un solo recorrido `en_curso` por usuario (la carrera se traduce a 409).
- CHECK `ck_recorridos_cerrado_con_metricas`: si no está `en_curso`, `fecha_fin` y las 4 métricas son obligatorias.
- Al finalizar, `duracion_s < 60` o `distancia_m < 200` → `descartado` (constantes en `app/recorridos/service.py`); las métricas se guardan igual.
- `drivescore`, `puntaje_frenadas`, `puntaje_aceleraciones`, `puntaje_giros`, `puntaje_velocidad` (`SmallInteger`, nulos; HU-15): solo en recorridos finalizados. CHECK `ck_recorridos_puntajes_0_100`.

**eventos** (`app/telemetria/models.py`, HU-15): `id`, `recorrido_id` (FK, índice), `tipo` (enum `tipo_evento`: `frenada_brusca` | `aceleracion_severa` | `giro_agresivo` | `exceso_velocidad`), `fecha` (hora del teléfono; en el exceso, el inicio del tramo), `lat`, `lon`, `velocidad_kmh` (antes de la maniobra; en el giro, durante; en el exceso, al empezar), `intensidad` (m/s² en maniobras; m/s sobre el límite en el exceso), `duracion_s` y `velocidad_maxima_kmh` (solo en el exceso), `fecha_creacion`
- CHECK `ck_eventos_exceso_con_duracion`: el exceso exige `duracion_s` y `velocidad_maxima_kmh`; los demás tipos los tienen nulos.
- Solo de recorridos finalizados: un descartado no guarda eventos.

**recomendaciones** (`app/recomendaciones/models.py`, HU-18): `id`, `recorrido_id` (FK), `orden` (0 = principal; 1 y 2 = secundarias, HU-19), `tipo` (enum `tipo_recomendacion`: los 4 de `tipo_evento` + `sin_eventos`), `titulo` (60), `texto` (300), `fecha_creacion`
- Único `uq_recomendaciones_recorrido_orden` (`recorrido_id`, `orden`); también hace de índice por recorrido.
- Se guarda el texto ya armado: cambiar las plantillas no altera los viajes anteriores.

Ids `Integer` autoincrementales. Tablas implementadas en `app/flotas/models.py`, `app/auth/models.py`, `app/recorridos/models.py`, `app/telemetria/models.py` y `app/recomendaciones/models.py`.

Gamificación y suscripciones **aún no están definidas**: no crearlas sin acordarlo primero.

## Reglas de trabajo

- Credenciales siempre en `.env` (ignorado por Git); toda variable nueva se documenta en `.env.example`.
- Cambios de esquema solo mediante migraciones de Alembic, nunca a mano en la BD.
- Consultas del panel empresarial: filtrar siempre por el `empresa_id` del admin autenticado.
- Usuarios: baja lógica (`activo = false`), nunca `DELETE`.
- Avanzar paso a paso: implementar solo lo que se pida, sin adelantar funcionalidades.

## Pendientes

- Cierre automático de recorridos abandonados en `en_curso`: por el índice único parcial, el conductor no puede iniciar otro hasta finalizarlo. Por ahora la app lo retoma con `GET /recorridos/activo`.
- Si una empresa está inactiva, su admin y sus conductores no deberían poder iniciar sesión. Resolver cuando se defina la gestión de empresas.
