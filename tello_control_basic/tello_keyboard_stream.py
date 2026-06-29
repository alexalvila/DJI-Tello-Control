# Librería estándar para comunicación UDP. El DJI Tello se controla enviando comandos de texto por UDP.
import socket
# Permite ejecutar varias tareas en paralelo: leer respuestas del Tello; leer telemetría; enviar comandos rc continuamente; leer video continuamente
import threading
# Se usa para pausas pequeñas y control de tiempos.
import time
# Cola segura entre hilos. Aquí guardamos respuestas recibidas del Tello.
import queue
# OpenCV se usa para recibir y procesar el video del Tello.
import cv2
# Pygame se usa para crear la ventana, mostrar el video y leer el teclado.
import pygame

# Dirección IP fija del Tello cuando estamos conectados a su WiFi.
TELLO_IP = "192.168.10.1"
# Puerto UDP al que se envían los comandos SDK; command, takeoff, land, rc, streamon, etc.
CMD_PORT = 8889
# Puerto local donde el Tello envía la telemetría: batería, altura, orientación, etc.
STATE_PORT = 8890
# Puerto local desde el que nuestro ordenador envía comandos.
LOCAL_CMD_PORT = 9000

# URL UDP del stream de video.
# El Tello envía video H.264 al puerto 11111 cuando se activa con "streamon".
# overrun_nonfatal=1 evita que OpenCV/FFmpeg cierre el stream si el buffer se llena.
# fifo_size grande ayuda a mantener el stream estable.
VIDEO_URL = "udp://@0.0.0.0:11111?overrun_nonfatal=1&fifo_size=50000000"

# Tamaño de la ventana donde mostramos el video.
WINDOW_W = 640
WINDOW_H = 480
# FPS a los que pintamos en pantalla. No cambia el FPS real enviado por el Tello, solo limita el renderizado local.
RENDER_FPS = 20

# Velocidad lineal para los comandos rc.
RC_SPEED = 35
# Velocidad de giro sobre el eje vertical. Se envía dentro del comando rc.
YAW_SPEED = 45



# Clase dedicada a leer el video del Tello en un hilo separado. Lee continuamente y guarda sólo el último frame recibido
class LatestFrameReader:
    def __init__(self, url, width, height):
        self.url = url  # URL del stream UDP.
        self.width = width  # Tamaño al que redimensionaremos cada frame.
        self.height = height    # Tamaño al que redimensionaremos cada frame.
        self.running = True # Bandera para mantener vivo o detener el hilo.
        self.latest_frame = None    # Aquí se guarda el último frame disponible. Al inicio no hay ninguno.
        self.lock = threading.Lock()    # Lock para proteger latest_frame.
        self.thread = threading.Thread(target=self._loop, daemon=True)  # Hilo que ejecutará continuamente el método _loop.

    # Inicia el hilo de lectura de video.
    def start(self):
        self.thread.start()

    # Abre el stream de video usando OpenCV y FFmpeg.
    def _open_capture(self):
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)    # CAP_FFMPEG fuerza a OpenCV a usar FFmpeg como backend.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # CAP_PROP_BUFFERSIZE intenta limitar el buffer interno para reducir latencia (no acumular demasiados frames)
        return cap

    # Bucle principal del hilo de video. Lee frames continuamente, si falla el stream > 2 segundos, cierra y vuelve abrir la captura
    def _loop(self):
        cap = self._open_capture()
        last_ok = time.time()   # Momento del último frame recibido correctamente.

        while self.running:
            ret, frame = cap.read() # Intenta leer y decodificar un frame.
            # Si no se ha recibido frame válido...
            if not ret:
                # Si llevamos más de 2 segundos sin frames, reiniciamos la captura.
                if time.time() - last_ok > 2:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    # Pequeña pausa antes de reabrir el stream.
                    time.sleep(0.2)
                    cap = self._open_capture()
                    last_ok = time.time()
                # Pausa muy pequeña para no consumir CPU innecesariamente.
                time.sleep(0.005)
                continue
            # Si hemos recibido frame, actualizamos el tiempo del último frame correcto.
            last_ok = time.time()
            # Reducimos el tamaño del frame para que renderizarlo sea más ligero.
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # OpenCV trabaja en BGR, pero Pygame espera RGB.
            # Guardamos el último frame disponible de forma segura.
            with self.lock:
                self.latest_frame = frame
        # Cuando se detiene el hilo, liberamos la captura.
        try:
            cap.release()
        except Exception:
            pass
    
    # Devuelve el último frame disponible.
    def get_frame(self):
        with self.lock:
            return self.latest_frame

    # Detiene el hilo de lectura de video.
    def stop(self):
        self.running = False

# Clase principal de control del Tello. Gestiona: conexión SDK; envío de comandos UDP; recepción de respuestas; recepción de telemetría; control con teclado; ventana de video
class TelloKeyboardStream:
    def __init__(self):
        self.running = True # Bandera general para mantener el programa ejecutándose.
        self.flying = False # Estado local de vuelo (saber si hemos hecho takeoff)

        self.responses = queue.Queue()  # Cola donde el hilo de recepción guarda respuestas del Tello.
        self.state = {} # Diccionario con la última telemetría recibida.
        self.pressed = set()    # Conjunto de teclas actualmente pulsadas.

        self.send_lock = threading.Lock()   # Lock para proteger el socket de comandos.
        self.key_lock = threading.Lock()    # Lock para proteger el conjunto de teclas pulsadas.
        self.state_lock = threading.Lock()  # Lock para proteger el diccionario de telemetría.

        self.rc_paused = threading.Event()  # Evento para pausar temporalmente los comandos rc (ya que el comando rc se manda continuamente).
        self.video = None   # Aquí guardaremos el lector de video.

        # Socket UDP para comandos. Se enlaza a LOCAL_CMD_PORT para poder recibir respuestas.
        self.cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cmd_sock.bind(("", LOCAL_CMD_PORT))
        self.cmd_sock.settimeout(0.1)

        # Socket UDP para telemetría. # El Tello envía estado al puerto 8890.
        self.state_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.state_sock.bind(("", STATE_PORT))
        self.state_sock.settimeout(0.1)

    # Inicia los hilos secundarios: 1-recepción de respuestas a comandos; 2-recepción de telemetría; 3-envío continuo de comandos rc
    def start_threads(self):
        threading.Thread(target=self._recv_responses, daemon=True).start()
        threading.Thread(target=self._recv_state, daemon=True).start()
        threading.Thread(target=self._rc_loop, daemon=True).start()

    # Hilo que recibe respuestas del Tello.
    def _recv_responses(self):
        while self.running:
            try:
                data, _ = self.cmd_sock.recvfrom(2048)
                msg = data.decode("utf-8", errors="ignore").strip() # Convertimos bytes a texto.
                # Si hay mensaje, lo metemos en la cola de respuestas.
                if msg:
                    self.responses.put(msg)
            # No pasa nada: el timeout evita que el hilo se quede bloqueado.
            except socket.timeout:
                pass
            # Suele ocurrir cuando cerramos el socket al salir.
            except OSError:
                break

    # Hilo que recibe la telemetría del Tello (envío del Tello: pitch:0;roll:0;yaw:0;vgx:0;vgy:0;vgz:0;bat:89;...)
    def _recv_state(self):
        while self.running:
            try:
                data, _ = self.state_sock.recvfrom(4096)
                msg = data.decode("utf-8", errors="ignore").strip()

                parsed = {}
                # Separamos cada campo por ";"
                for part in msg.split(";"):
                    # Cada campo viene como clave:valor.
                    if ":" in part:
                        k, v = part.split(":", 1)
                        parsed[k] = v
                # Actualizamos el estado de forma segura.
                with self.state_lock:
                    self.state.update(parsed)

            except socket.timeout:
                pass
            except OSError:
                break

    # Vacía la cola de respuestas antiguas.
    def _drain_responses(self):
        while True:
            try:
                self.responses.get_nowait()
            except queue.Empty:
                break

    # Envía un comando al Tello.
    def send(self, command, wait=True, timeout=7):
        self.rc_paused.set()    # Pausamos rc para evitar que sus respuestas interfieran.
        time.sleep(0.06)    # Pequeña pausa para asegurar que el hilo rc se detiene.
        self._drain_responses() # Limpiamos respuestas antiguas.

        # Enviamos el comando al puerto 8889 del Tello.
        with self.send_lock:
            self.cmd_sock.sendto(
                command.encode("utf-8"),
                (TELLO_IP, CMD_PORT)
            )
        # Si no queremos esperar respuesta, terminamos aquí.
        if not wait:
            self.rc_paused.clear()
            return None

        deadline = time.time() + timeout
        response = "timeout"

        # Esperamos una respuesta hasta que se agote el timeout.
        while time.time() < deadline:
            try:
                response = self.responses.get(timeout=0.2)
                break
            except queue.Empty:
                pass

        self.rc_paused.clear()  # Reanudamos rc.
        return response

    # Envía un comando sin esperar respuesta.
    def send_nowait(self, command):
        with self.send_lock:
            try:
                self.cmd_sock.sendto(
                    command.encode("utf-8"),
                    (TELLO_IP, CMD_PORT)
                )
            # Si el socket ya está cerrado, ignoramos el error.
            except OSError:
                pass

    # Hilo que envía continuamente comandos rc al Tello.
    def _rc_loop(self):
        while self.running:
            # Si estamos enviando un comando importante, pausamos rc.
            if self.rc_paused.is_set():
                time.sleep(0.05)
                continue

            # Valores iniciales: dron quieto.
            lr = 0
            fb = 0
            ud = 0
            yaw = 0

            # Copiamos las teclas pulsadas de forma segura.
            with self.key_lock:
                keys = set(self.pressed)

            # Movimiento adelante/atrás.
            if pygame.K_w in keys:
                fb = RC_SPEED
            if pygame.K_s in keys:
                fb = -RC_SPEED
            # Movimiento lateral.
            if pygame.K_a in keys:
                lr = -RC_SPEED
            if pygame.K_d in keys:
                lr = RC_SPEED
            # Subir/bajar.
            if pygame.K_UP in keys:
                ud = RC_SPEED
            if pygame.K_DOWN in keys:
                ud = -RC_SPEED
            # Giro izquierda/derecha.
            if pygame.K_LEFT in keys:
                yaw = -YAW_SPEED
            if pygame.K_RIGHT in keys:
                yaw = YAW_SPEED

            self.send_nowait(f"rc {lr} {fb} {ud} {yaw}")    # Enviamos el comando rc.
            time.sleep(0.05)    # 20 veces por segundo aproximadamente.

    # Inicializa la comunicación con el Tello. Pasos: 1-iniciar hilos de recepción; 2-entrar en modo SDK con "command"; 3-activar video con "streamon"
    def connect(self):
        self.start_threads()

        print("Entrando en SDK mode...")
        r = self.send("command", wait=True, timeout=7)
        print("command:", r)

        if r != "ok":
            raise RuntimeError("No respondió OK a 'command'. Revisa que sigues conectado al WiFi del Tello.")

        print("Activando stream...")
        r = self.send("streamon", wait=True, timeout=7)
        print("streamon:", r)

        if r != "ok":
            raise RuntimeError("No respondió OK a 'streamon'.")

        time.sleep(1.0) # Damos un pequeño margen para que empiece a llegar video.

    # Despega el dron si todavía no está volando.
    def takeoff(self):
        if not self.flying:
            print("takeoff...")
            r = self.send("takeoff", wait=True, timeout=12)
            print("respuesta:", r)
            if r == "ok":
                self.flying = True

    # Aterriza el dron si está volando.
    def land(self):
        if self.flying:
            print("land...")
            r = self.send("land", wait=True, timeout=12)
            print("respuesta:", r)
            self.flying = False

    # Parada de emergencia (corta los motores inmediatamente).
    def emergency(self):
        print("emergency")
        self.send_nowait("emergency")
        self.flying = False

    # Ejecuta un flip si el dron está volando (varía la dirección mediante 1,2,3,4).
    def flip(self, direction):
        if self.flying:
            r = self.send(f"flip {direction}", wait=True, timeout=7)
            print("flip:", r)

    # Reinicia el stream de video.
    def restart_video_stream(self):
        print("Reiniciando stream...")
        self.send("streamoff", wait=True, timeout=2)
        time.sleep(0.3)
        r = self.send("streamon", wait=True, timeout=5)
        print("streamon:", r)

    # Dibuja texto encima del video: batería; altura; distancia TOF; ayuda de controles
    def draw_overlay(self, screen, font):
        with self.state_lock:
            bat = self.state.get("bat", "?")
            h = self.state.get("h", "?")
            tof = self.state.get("tof", "?")

        lines = [
            f"Battery: {bat}%   Height: {h}cm   TOF: {tof}",
            "W/S adelante-atras | A/D izquierda-derecha | Flechas up/down altura | Flechas left/right yaw",
            "SPACE takeoff | L land | Q land+salir | ESC emergency | R reiniciar stream | 1/2/3/4 flips",
        ]

        y = 8
        for line in lines:
            # Renderizamos el texto.
            surf = font.render(line, True, (255, 255, 255))
            # Creamos un fondo negro semitransparente para que el texto se lea mejor.
            bg = pygame.Surface((surf.get_width() + 12, surf.get_height() + 6))
            bg.set_alpha(140)
            bg.fill((0, 0, 0))
            # Dibujamos fondo y texto.
            screen.blit(bg, (6, y - 3))
            screen.blit(surf, (12, y))
            y += surf.get_height() + 7

    # Bucle principal del programa. Se encarga de crear la ventana; iniciar el lector de video; leer eventos de teclado; pintar el último frame; dibujar la interfaz encima
    def run(self):
        pygame.init()
        # Creamos la ventana.
        screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
        pygame.display.set_caption("DJI Tello low-latency keyboard + stream")
        font = pygame.font.SysFont("Arial", 15) # Fuente para los textos de ayuda.
        clock = pygame.time.Clock() # Reloj para limitar FPS de render.

        # Iniciamos el lector de video en hilo aparte.
        self.video = LatestFrameReader(VIDEO_URL, WINDOW_W, WINDOW_H)
        self.video.start()

        last_frame_ok = False   # Indica si alguna vez hemos recibido video correctamente.

        try:
            while self.running:
                # Procesamos eventos de Pygame.
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False

                    elif event.type == pygame.KEYDOWN:
                        # Guardamos la tecla como pulsada.
                        with self.key_lock:
                            self.pressed.add(event.key)
                        # Teclas de acciones puntuales.
                        if event.key == pygame.K_SPACE:
                            self.takeoff()
                        elif event.key == pygame.K_l:
                            self.land()
                        elif event.key == pygame.K_q:
                            self.land()
                            self.running = False
                        elif event.key == pygame.K_ESCAPE:
                            self.emergency()
                            self.running = False
                        elif event.key == pygame.K_r:
                            self.restart_video_stream()
                        elif event.key == pygame.K_1:
                            self.flip("l")
                        elif event.key == pygame.K_2:
                            self.flip("r")
                        elif event.key == pygame.K_3:
                            self.flip("f")
                        elif event.key == pygame.K_4:
                            self.flip("b")
                    # Cuando se suelta una tecla, la quitamos del conjunto.
                    elif event.type == pygame.KEYUP:
                        with self.key_lock:
                            self.pressed.discard(event.key)

                frame = self.video.get_frame()  # Pedimos el último frame disponible.

                if frame is not None:
                    last_frame_ok = True
                    surface = pygame.surfarray.make_surface(frame.swapaxes(0, 1))   # Pygame necesita que el array tenga los ejes en orden ancho x alto.
                    screen.blit(surface, (0, 0))    # Dibujamos el frame en pantalla.
                else:
                    screen.fill((20, 20, 20))   # Si todavía no hay video, pintamos pantalla oscura con mensaje.

                    if last_frame_ok:
                        msg = "Video perdido. Pulsa R para reiniciar stream."
                    else:
                        msg = "Esperando video UDP 11111..."

                    surf = font.render(msg, True, (255, 255, 255))
                    screen.blit(surf, (30, 30))

                self.draw_overlay(screen, font) # Dibujamos telemetría y ayuda encima del video.
                pygame.display.flip()   # Actualizamos la ventana.
                clock.tick(RENDER_FPS)  # Limitamos los FPS de render.
        # Pase lo que pase, intentamos cerrar todo limpiamente.
        finally:
            self.cleanup()

    # Cierra el programa de forma segura.
    def cleanup(self):
        print("Cerrando...")
        self.running = False
        # Pausamos rc para que no siga mandando comandos durante el cierre.
        self.rc_paused.set()
        # Orden de parada: detener movimiento.
        self.send_nowait("rc 0 0 0 0")
        time.sleep(0.1)
        # Si está volando, intentamos aterrizar.
        if self.flying:
            self.send_nowait("land")
            time.sleep(0.5)
        # Apagamos el stream.
        self.send_nowait("streamoff")
        time.sleep(0.2)
        # Paramos el hilo de video.
        if self.video:
            self.video.stop()
        # Cerramos socket de comandos.
        try:
            self.cmd_sock.close()
        except Exception:
            pass
        # Cerramos socket de telemetría.
        try:
            self.state_sock.close()
        except Exception:
            pass
        # Cerramos Pygame.
        pygame.quit()

# Punto de entrada del programa. Solo se ejecuta si lanzamos este archivo directamente con Python.
if __name__ == "__main__":
    app = TelloKeyboardStream()
    app.connect()
    app.run()