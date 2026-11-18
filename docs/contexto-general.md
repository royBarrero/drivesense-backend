# DriveSense — contexto general

App móvil que convierte el smartphone en un centro de telemetría vehicular sin hardware adicional: usa GPS, acelerómetro y giroscopio para evaluar hábitos de conducción, detectar maniobras de riesgo y calcular un **DriveScore (0-100) por viaje**.

- Proyecto universitario grupal, metodología Scrum.
- App **solo Android** (iOS fuera de alcance).
- Usuarios: conductores particulares, pequeñas/medianas flotas y aseguradoras.
- Modelo freemium: Gratuito / Premium / Empresas.
- Idioma del proyecto (código de dominio, tablas, commits, documentación): español. En identificadores se evita la ñ (`contrasenia`).

## Repositorios

La carpeta raíz que contiene los tres repos **no es un repo**; cada subcarpeta es un repo Git independiente.

Este archivo vive en `drivesense-backend/docs/contexto-general.md` para quedar versionado. En un equipo nuevo, después de clonar los tres repos en una misma carpeta, crear en esa carpeta un `CLAUDE.md` con una sola línea:

```
@drivesense-backend/docs/contexto-general.md
```

Los `CLAUDE.md` de cada repo sí están versionados en su repo.

| Carpeta | Stack | Estado |
|---|---|---|
| `drivesense-backend` | Python 3.14, FastAPI async, SQLAlchemy 2 async + asyncpg, PostgreSQL 17, Alembic, pydantic-settings | HU-01 a HU-06, la ruta de HU-08, el listado y registro de conductores de la flota (HU-24), el perfil de desempeño de un conductor (HU-25), el historial de eventos de riesgo de un conductor (HU-26), la exportación del reporte de desempeño en PDF y CSV (HU-27), el tablero de indicadores de la flota (pantalla Reportes), HU-15 (tabla `eventos` y DriveScore), los conteos de eventos de HU-16, el histórico de HU-17, el comparativo entre periodos (HU-20), el nivel y los logros (HU-21), los desafíos semanales y su recompensa (HU-22, HU-23), los eventos con posición del detalle (HU-29), las recomendaciones principal y secundarias (HU-18, HU-19) y el plan de suscripción de la empresa con sus límites (HU-28) implementados, más el **modo pruebas temporal** (`app/pruebas/`) (ver su CLAUDE.md) |
| `drivesense-mobile` | Flutter 3.41.6 / Dart 3.11.4, sensors_plus, geolocator, Riverpod — app del conductor | Base de la app, HU-01, HU-02, HU-04 a HU-17 (los eventos se envían al finalizar), HU-18 y HU-19 (consejos del viaje), HU-20 (comparar periodos), HU-21 (nivel y logros), HU-22 y HU-23 (desafíos semanales y su recompensa) y HU-29 (mapa del viaje); envía el teléfono y la versión de la app al iniciar un viaje (ver su CLAUDE.md y `docs/diseno.md`) |
| `drivesense-frontend` | React 19 + Vite + Tailwind v4 + Shadcn/ui — panel web B2B para admins de empresa | Landing, login y registro de empresa (HU-03), panel con menú lateral e Inicio, sección Plan (HU-28: plan actual, comparativo y cambio con pago simulado; funciones bloqueadas según el plan), sección Conductores (HU-24: listado, resumen, alta y contraseña temporal; HU-25: perfil del conductor y detalle de sus viajes; HU-26: pestaña de eventos de riesgo con patrones, mapa e historial; HU-27: botón y diálogo para exportar el reporte del conductor y de la flota), pantalla Reportes con el tablero de la flota, más el **modo pruebas temporal** (`features/pruebas/`) (ver su CLAUDE.md y `docs/diseno.md`) |

- Autenticación JWT compartida entre app, panel web y backend.
- Despliegue: una EC2 de AWS con Docker Compose (Caddy con HTTPS, API, panel y PostgreSQL), dominio con sslip.io. El compose y la guía viven en el backend (`deploy/`, `docs/despliegue.md`); la app se reparte como APK release apuntando a ese dominio.
- La captura de sensores, el filtrado de señal y la detección de eventos ocurren **principalmente en la app móvil**; el backend recibe y persiste los resultados.
- **IA**: las recomendaciones del viaje (HU-18, HU-19) las elige el backend con reglas fijas y las redacta Claude (Claude Haiku 5.5, API de Anthropic, `ANTHROPIC_API_KEY` en el `.env` del backend) con los datos del viaje; sin clave o si la IA falla, se usan plantillas fijas. La app y el panel marcan las redactadas por la IA con la cápsula "Con IA".

## Módulos e historias de usuario

| Módulo | Módulo(s) backend | HU |
|---|---|---|
| 1. Usuarios y Recorridos | `auth`, `recorridos` | HU-01 registro conductor (correo/contraseña) · HU-02 login conductor · HU-03 registro/login cuenta empresarial · HU-04 iniciar recorrido con velocidad/tiempo/distancia en vivo · HU-05 finalizar recorrido · HU-06 historial de viajes · HU-29 ver la ruta y los eventos del viaje en el mapa |
| 2. Telemetría y Detección de Eventos | `telemetria` | HU-07 capturar acelerómetro/giroscopio · HU-08 capturar GPS · HU-09 filtrar señal (baches, movimiento del teléfono) · HU-10 frenada brusca · HU-11 aceleración severa · HU-12 giro agresivo · HU-13 exceso de velocidad · HU-14 eventos de riesgo en tiempo real |
| 3. DriveScore | `puntaje` | HU-15 calcular DriveScore al finalizar · HU-16 desglose por categoría (velocidad, frenados, aceleraciones, giros) · HU-17 histórico del DriveScore |
| 4. Recomendaciones | `recomendaciones` | HU-18 recomendación principal según patrones de riesgo · HU-19 recomendaciones secundarias |
| 5. Progreso y Gamificación | `gamificacion` | HU-20 reporte comparativo entre periodos · HU-21 nivel y logros · HU-22 desafíos semanales · HU-23 completar desafío y recibir recompensa |
| 6. Gestión de Flotas (panel web) | `flotas`, `suscripciones` | HU-24 listado de conductores de la empresa · HU-25 DriveScore y perfil de riesgo de un conductor · HU-26 historial de eventos de un conductor · HU-27 exportar reporte de desempeño · HU-28 gestionar plan de suscripción |

Pendientes de agregar al backlog:
- Conductor cambia su contraseña temporal en el primer inicio de sesión.

## Usuarios, roles y multitenancy (definido)

- **Multitenant con base compartida**: `empresa_id` separa los datos de cada empresa. Toda consulta del panel empresarial debe filtrar por el `empresa_id` del admin autenticado.
- **Conductor individual**: se registra solo desde la app; rol `conductor`, sin empresa.
- **Conductor de flota**: su cuenta la crea el admin de la empresa con una contraseña temporal única; debe cambiarla en su primer inicio de sesión. Pertenece a una sola empresa y no necesita cuenta personal adicional.
- **Admin de empresa** (`admin_empresa`): se registra desde el panel web y en ese paso se crea su empresa. Solo usa el panel web; si quiere conducir, usa una cuenta de conductor distinta.
- Las **aseguradoras** se modelan como un tipo de empresa.
- **Baja lógica**: los usuarios se desactivan (`activo = false`), nunca se borran, para conservar su historial.

## Reglas de trabajo

- Avanzar paso a paso: implementar solo lo que se pida, sin adelantar funcionalidades.
- No inventar tablas, endpoints ni reglas de negocio no definidas; acordarlas primero.

## Forma de trabajo

Aplica a todos los repos.

- Antes de implementar, mostrar el plan y esperar aprobación.
- Seguir las convenciones y patrones que ya existen en cada repo (errores, validaciones, estructura, nombres, tokens de diseño); no pedir que se repitan en cada tarea.
- Las reglas de negocio y decisiones que no estén claras se preguntan, no se inventan.
- Al terminar: lint/análisis y tests en verde, probar los casos principales y de error (curl en el backend, el A34 en la app, el navegador en el panel), borrar los datos de prueba y resumir qué se probó.
- No hacer commit salvo que se pida.
