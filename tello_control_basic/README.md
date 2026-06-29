# DJI Tello Keyboard Controller with Low-Latency Video Stream

Control de un dron DJI Tello desde Python usando el teclado, con visualización del stream de video en tiempo real y reducción de latencia mediante lectura continua del último frame disponible.

Este proyecto permite:

- Conectarse al DJI Tello mediante su red WiFi.
- Enviar comandos SDK al dron por UDP.
- Controlar el movimiento en tiempo real con teclado.
- Ver el stream de video en una ventana de Pygame.
- Recibir telemetría básica como batería, altura y sensor TOF.
- Reiniciar el stream de video si se pierde.
- Reducir el retraso del video usando un hilo dedicado que mantiene siempre el último frame recibido.

---

## Funcionamiento general

El DJI Tello se comunica usando UDP:

| Función | Puerto | Descripción |
|---|---:|---|
| Comandos SDK | 8889 | Envío de comandos como `command`, `takeoff`, `land`, `rc`, `streamon` |
| Estado / telemetría | 8890 | Recepción de datos como batería, altura, velocidad, orientación |
| Video | 11111 | Stream de video H.264 activado con `streamon` |

El programa usa varios hilos para que todo funcione en paralelo:

- Un hilo recibe respuestas del Tello.
- Un hilo recibe telemetría.
- Un hilo envía comandos `rc` continuamente.
- Un hilo lee el video continuamente y guarda solo el frame más reciente.
- El hilo principal gestiona la ventana, el teclado y el renderizado.

La clase más importante para reducir latencia es `LatestFrameReader`, que lee el stream de video en segundo plano y evita que se acumulen frames antiguos.

---

## Requisitos

### Hardware

- DJI Tello o Tello compatible con SDK.
- Ordenador conectado directamente al WiFi del Tello.
- Teclado.

### Software

- Python 3.8 o superior.
- OpenCV.
- Pygame.

---

## Instalación

Clona el repositorio:

```bash
git clone https://github.com/tu-usuario/tu-repositorio.git
cd tu-repositorio
```

Instala las dependencias:

```bash
pip install opencv-python pygame
```

---

## Uso

1. Enciende el DJI Tello.
2. Conecta tu ordenador a la red WiFi del Tello.
3. Ejecuta el script:

```bash
python tello_keyboard_stream.py
```

Al arrancar, el programa:

1. Entra en modo SDK enviando `command`.
2. Activa el stream con `streamon`.
3. Abre una ventana con el video.
4. Permite controlar el dron con el teclado.

---

## Controles

| Tecla | Acción |
|---|---|
| `SPACE` | Despegar |
| `L` | Aterrizar |
| `Q` | Aterrizar y salir |
| `ESC` | Emergencia |
| `W` | Avanzar |
| `S` | Retroceder |
| `A` | Mover a la izquierda |
| `D` | Mover a la derecha |
| Flecha arriba | Subir |
| Flecha abajo | Bajar |
| Flecha izquierda | Girar a la izquierda |
| Flecha derecha | Girar a la derecha |
| `R` | Reiniciar stream de video |
| `1` | Flip izquierda |
| `2` | Flip derecha |
| `3` | Flip adelante |
| `4` | Flip atrás |

---

## Configuración principal

Estos valores se pueden modificar al inicio del script:

```python
TELLO_IP = "192.168.10.1"
CMD_PORT = 8889
STATE_PORT = 8890
LOCAL_CMD_PORT = 9000

VIDEO_URL = "udp://@0.0.0.0:11111?overrun_nonfatal=1&fifo_size=50000000"

WINDOW_W = 640
WINDOW_H = 480
RENDER_FPS = 20

RC_SPEED = 35
YAW_SPEED = 45
```

### Parámetros importantes

`WINDOW_W` y `WINDOW_H` definen el tamaño de la ventana de video.

Reducir estos valores puede mejorar el rendimiento:

```python
WINDOW_W = 480
WINDOW_H = 360
```

`RENDER_FPS` limita cuántas veces por segundo se pinta la ventana. No cambia el FPS real del video enviado por el Tello.

```python
RENDER_FPS = 20
```

`RC_SPEED` controla la velocidad de movimiento lineal.

```python
RC_SPEED = 35
```

`YAW_SPEED` controla la velocidad de giro.

```python
YAW_SPEED = 45
```

---

## Reducción de latencia del video

El video del Tello puede llegar con retraso si el programa acumula frames antiguos.

Para reducir ese retraso, el proyecto usa la clase `LatestFrameReader`.

En lugar de leer video directamente dentro del bucle principal, se crea un hilo separado que lee continuamente frames del stream UDP y guarda solo el último:

```python
self.latest_frame = frame
```

Después, la ventana de Pygame solo pinta el frame más reciente disponible:

```python
frame = self.video.get_frame()
```

Esto ayuda a que el video vaya lo más cerca posible del tiempo real.

---

## Estructura del código

```text
tello_keyboard_stream.py
├── Configuración de red y video
├── LatestFrameReader
│   ├── Abre el stream UDP
│   ├── Lee frames continuamente
│   ├── Redimensiona el video
│   └── Guarda el último frame disponible
└── TelloKeyboardStream
    ├── Inicializa sockets UDP
    ├── Entra en modo SDK
    ├── Activa el stream
    ├── Lee teclado con Pygame
    ├── Envía comandos rc
    ├── Recibe respuestas
    ├── Recibe telemetría
    ├── Muestra video
    └── Cierra todo de forma segura
```

---

## Clases principales

### `LatestFrameReader`

Se encarga del video.

Funciones principales:

| Método | Descripción |
|---|---|
| `start()` | Inicia el hilo de lectura de video |
| `_open_capture()` | Abre el stream UDP con OpenCV |
| `_loop()` | Lee frames continuamente |
| `get_frame()` | Devuelve el último frame recibido |
| `stop()` | Detiene el hilo |

Esta clase es clave para evitar que se acumulen frames viejos.

### `TelloKeyboardStream`

Se encarga del control general del dron.

Funciones principales:

| Método | Descripción |
|---|---|
| `connect()` | Entra en modo SDK y activa el stream |
| `send()` | Envía comandos y espera respuesta |
| `send_nowait()` | Envía comandos sin esperar respuesta |
| `_rc_loop()` | Envía comandos de movimiento continuamente |
| `takeoff()` | Despega |
| `land()` | Aterriza |
| `emergency()` | Ejecuta parada de emergencia |
| `flip()` | Ejecuta flips |
| `restart_video_stream()` | Reinicia el video |
| `run()` | Ejecuta la ventana y el bucle principal |
| `cleanup()` | Cierra sockets, stream y ventana |

---

## Comando `rc`

El movimiento en tiempo real se realiza con el comando:

```text
rc izquierda_derecha adelante_atras arriba_abajo giro
```

Cada valor puede ir normalmente de `-100` a `100`.

Ejemplo:

```text
rc 0 35 0 0
```

Este comando mueve el dron hacia adelante.

En el script se envía aproximadamente 20 veces por segundo:

```python
time.sleep(0.05)
```

Esto permite un control más fluido que enviar comandos separados como `forward`, `back`, `left` o `right`.

---

## Seguridad

Usar un dron puede ser peligroso. Ten en cuenta lo siguiente:

- Prueba primero sin hélices si es posible.
- Vuela en un espacio abierto.
- Mantén distancia de personas, animales y objetos.
- Comprueba la batería antes de despegar.
- Ten siempre preparada la tecla `ESC` para emergencia.
- No vueles cerca de carreteras, aeropuertos o zonas restringidas.
- Si pierdes video o control, aterriza inmediatamente.

La tecla `ESC` envía:

```text
emergency
```

Este comando corta los motores de forma inmediata. Solo debe usarse si es necesario.

---

## Solución de problemas

### No aparece video

Comprueba que:

- Estás conectado al WiFi del Tello.
- El comando `streamon` responde `ok`.
- Ninguna otra aplicación está usando el stream del Tello.
- La app oficial del Tello está cerrada.
- El firewall no bloquea UDP en el puerto `11111`.

También puedes pulsar `R` para reiniciar el stream.

### El dron no responde

Comprueba que:

- Estás conectado al WiFi del Tello.
- La IP del Tello es `192.168.10.1`.
- El puerto de comandos es `8889`.
- El comando `command` responde `ok`.

### El video tiene retraso

Puedes probar:

```python
WINDOW_W = 480
WINDOW_H = 360
RENDER_FPS = 15
```

También ayuda:

- Acercarse al Tello.
- Cerrar otras aplicaciones que usen WiFi.
- Cerrar grabadores de pantalla.
- Cerrar la app oficial del Tello.
- Evitar VPNs o redes compartidas.

### El video se congela

Pulsa:

```text
R
```

Esto ejecuta:

```text
streamoff
streamon
```

---

## Dependencias

```text
opencv-python
pygame
```

Instalación:

```bash
pip install opencv-python pygame
```

---

## Posibles mejoras futuras

- Grabar video en archivo.
- Guardar logs de telemetría.
- Añadir joystick.
- Añadir control por gamepad.
- Mostrar velocidad, pitch, roll y yaw en pantalla.
- Añadir modo de vuelo autónomo.
- Añadir detección de objetos con OpenCV.
- Añadir seguimiento visual.
- Añadir configuración desde archivo `.json` o `.yaml`.

---

## Licencia

Puedes usar este proyecto con la licencia que prefieras.

Ejemplo recomendado:

```text
MIT License
```

---

## Autor

Proyecto desarrollado para controlar un DJI Tello desde Python con teclado y stream de video de baja latencia.
