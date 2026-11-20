# Guía de la app DriveSense (conductores)

DriveSense convierte el teléfono en un medidor de manejo: con el GPS, el acelerómetro y el giroscopio registra cada viaje, detecta maniobras de riesgo y le pone un DriveScore de 0 a 100. La app es solo para Android. Abajo tiene una barra con tres pestañas: **Inicio**, **Viajes** y **Perfil**.

## Cuenta
- **Crear una cuenta**: en la pantalla de inicio de sesión, toca "Crear cuenta" (abajo, junto a "¿No tienes cuenta?") y completa nombre completo, correo, teléfono y contraseña (al menos 8 caracteres, con una letra y un número). Luego toca "Crear cuenta".
- **Iniciar sesión**: escribe tu correo y contraseña y toca "Iniciar sesión". Las cuentas de administrador de empresa no entran a la app: usan el panel web.
- **Panel web**: lo usa el administrador de una empresa para registrar a sus conductores, ver sus puntajes y descargar reportes. Un conductor no entra al panel y desde la app no se hace nada de eso.
- **Conductor de una flota**: tu empresa crea tu cuenta y te entrega una contraseña temporal. Inicia sesión con tu correo y esa contraseña; la primera vez se te pide cambiarla.
- **Olvidé mi contraseña**: la app no tiene recuperación de contraseña. Si eres conductor de una flota, pide al administrador de tu empresa una contraseña temporal nueva.
- **Cerrar sesión**: en Perfil, toca "Cerrar sesión". No se puede cerrar sesión durante un viaje: primero finalízalo.

## Iniciar un viaje
1. En Inicio, toca el botón "Iniciar recorrido" (también está en Viajes cuando todavía no tienes viajes).
2. Elige cómo quieres viajar en "¿Cómo quieres viajar?":
   - **Recorrido libre**: registra el viaje sin ruta.
   - **Con destino**: abre el Copiloto para elegir a dónde vas y ver la ruta antes de salir (ver "Copiloto").
3. La primera vez la app pide permisos en "Antes de empezar":
   - **Ubicación** (permitir solo mientras se usa la app): sin ella no se puede medir el viaje. Si la negaste, la app ofrece "Abrir ajustes" para activarla. Si la ubicación del teléfono está apagada, toca "Activar ubicación".
   - **Notificaciones**: para ver los avisos de eventos con la pantalla apagada. Si las niegas, el viaje se registra igual.
4. Pon el teléfono en un soporte y maneja. Puedes apagar la pantalla o usar otras apps: el viaje se sigue registrando (aparece la notificación "DriveSense está registrando tu viaje"). Si cierras la app desde las apps recientes, el registro se detiene hasta que la abras y toques "Continuar viaje".

## Durante el viaje
- La pantalla del viaje muestra la velocidad actual, el tiempo, la distancia y la velocidad máxima, y los eventos del viaje por tipo.
- **Eventos de riesgo** que detecta la app: frenada brusca, aceleración severa, giro agresivo y exceso de velocidad (más de 60 km/h durante 5 segundos o más). Cuando ocurre uno, el teléfono vibra, suena (si "Sonido de avisos" está activado) y se muestra un aviso con el color del evento.
- Para volver a Inicio sin terminar el viaje, toca "Volver"; el viaje sigue. Para regresar, toca "Volver al viaje" en Inicio.
- Durante un viaje no se puede iniciar otro.

## Finalizar un viaje
1. En la pantalla del viaje, toca "Finalizar viaje" y confirma en "¿Finalizar el viaje?" con "Finalizar" (o "Seguir registrando" si fue sin querer).
2. Se abre el resumen: "Viaje guardado", el mapa de la ruta con los eventos, los contadores de eventos, el DriveScore del viaje, tus consejos y las cifras.
- Los viajes de menos de 1 minuto o de menos de 200 metros no se guardan ("El viaje fue muy corto y no se guardó").
- **Finalización automática**: si el auto queda detenido 3 minutos seguidos, la app finaliza el viaje sola y te avisa con una notificación.
- **Sin internet**: el viaje se guarda en el teléfono y se envía solo cuando vuelva la conexión. Mientras tanto el DriveScore aparece como "Calculando tu DriveScore". No puedes iniciar otro viaje hasta que se envíe.
- **Viaje interrumpido** (por ejemplo, la app se cerró): al abrirla, Inicio ofrece "Continuar viaje" o "Finalizar".

## Ver mis viajes (historial)
1. Toca la pestaña **Viajes**.
2. Elige el periodo arriba: "Esta semana", "Este mes" o "Todos".
3. Toca un viaje para ver su detalle: el mapa de la ruta con los eventos, el DriveScore con lo que más restó y el puntaje por categoría, los eventos por tipo, los consejos, las horas de salida y llegada, el destino (si tenía) y las cifras.
4. En el detalle, "Ver mapa completo" abre el mapa a pantalla completa: puedes filtrar por tipo de evento y tocar un evento para verlo. "Ver la ruta entera" vuelve a mostrar todo el recorrido.
- En Inicio, "Esta semana" muestra cuántos viajes, kilómetros y tiempo llevas esta semana, y "Mis viajes" abre la pestaña Viajes. "Último viaje" abre el detalle de tu viaje más reciente.

## Mi DriveScore (puntajes)
- **DriveScore de un viaje**: de 0 a 100, calculado con cuatro categorías: velocidad, frenadas, aceleraciones y giros. Calificación: Excelente (90-100), Muy bueno (75-89), Regular (60-74) y Riesgoso (menos de 60). Lo ves en el resumen al terminar y en el detalle de cada viaje.
- **Tu promedio**: en Inicio, el anillo del encabezado muestra tu DriveScore de los últimos 7 días. Tócalo para abrir **Mi DriveScore**.
- **Mi DriveScore**: elige el periodo ("7 días", "30 días" o "3 meses") y ve tu promedio con la tendencia, el gráfico, el promedio por categoría y tu mejor viaje y el viaje más bajo (tócalos para abrirlos).
- **Comparar periodos**: al final de Mi DriveScore, toca "Comparar periodos" y elige "Semana" o "Mes". Muestra tu DriveScore, cada categoría y tu actividad frente al periodo anterior, con lo que más mejoró y lo que más empeoró. Hacen falta al menos 2 viajes en cada periodo.
- Si todavía no tienes viajes, el anillo dice "Tu DriveScore aparecerá después de tu primer viaje".

## Consejos
- Al terminar un viaje recibes un consejo principal según lo que más restó y, si hubo otros eventos, hasta dos más en "También puedes mejorar". Los que tienen la cápsula "Con IA" los redactó la inteligencia artificial con los datos de tu viaje.
- Están en el resumen del viaje, en su detalle y, el del último viaje, en Inicio ("Consejo").

## Desafíos semanales
- En Inicio, "Desafíos de la semana" muestra tus 3 desafíos de la semana y cuánto te falta; "Ver todos" abre la pantalla de desafíos con el detalle y el tiempo que queda.
- Se completan solos al finalizar un viaje que cumple la meta; no hay que reclamarlos. Cada uno da puntos y, si completas los 3, ganas un bono semanal de 100 puntos.
- Cada lunes empiezan desafíos nuevos.

## Nivel y logros
- En Perfil, la tarjeta de nivel muestra tu nivel, tus puntos y cuánto te falta para el siguiente. Ganas puntos con cada viaje (más si manejas mejor y si es más largo), con los desafíos y con el bono semanal. El nivel nunca baja.
- Toca "Logros" en esa tarjeta para ver los desbloqueados (con su fecha) y los pendientes con su avance.
- Al terminar un viaje, el resumen avisa si subiste de nivel, desbloqueaste un logro o completaste un desafío.

## Copiloto (viaje con destino)
1. Toca "Iniciar recorrido" y elige "Con destino".
2. Elige el destino tocando o manteniendo pulsado el mapa, o con "Buscar destino" (escribe un lugar, calle o barrio y busca; aparecen lugares de Bolivia, primero los de Santa Cruz).
3. Se muestra la ruta en auto con la distancia y el tiempo. "Ver pasos" muestra las indicaciones. "Quitar destino" lo borra.
4. Toca "Iniciar con destino". Durante el viaje, el Copiloto te guía por voz ("En 100 metros, gira a la derecha…"), avisa al llegar y recalcula si te sales de la ruta.
- "Silenciar voz" calla la guía solo en ese viaje. Si no hay ruta, puedes "Hacer un recorrido libre".

## Modo aprendizaje
- Da consejos breves por voz después de cada evento de riesgo (por ejemplo, después de una frenada fuerte). Funciona en todos los viajes, con o sin destino.
- Se activa con el interruptor "Modo aprendizaje" en Perfil, en el Copiloto antes de iniciar o en la pantalla del viaje. Viene apagado.

## Ajustes en Perfil
- **Sonido de avisos**: activa o silencia el tono de los eventos; la vibración se mantiene.
- **Notificaciones desactivadas**: si negaste el permiso, aparece este aviso con "Activar" para ir a los ajustes del teléfono.
- **Ayuda**: abre este asistente.

## Asistente de ayuda
- Se abre con el botón "?" del encabezado de Inicio o con "Ayuda" en Perfil. Responde dudas sobre cómo usar la app; no puede ver tus viajes ni tus puntajes.
