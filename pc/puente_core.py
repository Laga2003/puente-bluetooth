"""Protocolo y lógica del puente Bluetooth. Sin interfaz ni teclado, para poder probarlo aparte.

Cada mensaje es: 4 bytes con la longitud (big-endian) + JSON en UTF-8.
Para escribir, el PC pide las letras de una en una ("next") y el móvil contesta
con una sola letra ("char"). Así cada letra hace un viaje real por Bluetooth y el
tiempo de ida y vuelta se mide con un único reloj, el del PC.
"""
import json
import os
import queue
import random
import socket
import struct
import sys
import threading
import time
import uuid

SERVICE_UUID = "7c1d6f2a-4b7e-4f0e-9a55-2e6b8c3d1a90"
PROTOCOL_VERSION = 1
MAX_FRAME = 1024 * 1024
REPLY_TIMEOUT_S = 5.0


def encode_frame(message):
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(data)) + data


def _read_exact(sock, size):
    chunks = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise ConnectionError("el móvil ha cerrado la conexión")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def read_frame(sock):
    size = struct.unpack(">I", _read_exact(sock, 4))[0]
    if size > MAX_FRAME:
        raise ConnectionError(f"mensaje demasiado grande ({size} bytes)")
    return json.loads(_read_exact(sock, size).decode("utf-8"))


def build_sockaddr_bth(mac, service_uuid=SERVICE_UUID):
    """SOCKADDR_BTH de Windows con puerto 0: Windows busca el canal por el UUID."""
    import ctypes

    class SOCKADDR_BTH(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("family", ctypes.c_uint16),
            ("address", ctypes.c_uint64),
            ("service", ctypes.c_ubyte * 16),
            ("port", ctypes.c_uint32),
        ]

    service = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(service_uuid).bytes_le)
    address = int(mac.replace(":", "").replace("-", ""), 16)
    return SOCKADDR_BTH(32, address, service, 0)  # 32 = AF_BTH


def open_link(mac):
    """Abre el enlace. PUENTE_TCP=host:puerto sustituye Bluetooth para hacer pruebas."""
    test_target = os.environ.get("PUENTE_TCP")
    if test_target:
        host, port = test_target.rsplit(":", 1)
        return socket.create_connection((host, int(port)), timeout=10)
    if sys.platform != "win32":
        raise OSError("La conexión Bluetooth de este programa es solo para Windows")

    import ctypes

    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    address = build_sockaddr_bth(mac)
    ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)
    ws2.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    ws2.connect.restype = ctypes.c_int
    if ws2.connect(sock.fileno(), ctypes.byref(address), ctypes.sizeof(address)) != 0:
        error = ctypes.get_last_error()
        sock.close()
        raise OSError(error, "No se pudo conectar. ¿Móvil emparejado y servidor iniciado en la app?")
    return sock


class Pace:
    """Ritmo automático e irregular entre letras.

    Combina tres cosas: fases de distinta velocidad, variación aleatoria en cada
    letra y un freno que aumenta la pausa si la latencia sube.
    """

    PHASES = ((0.4, 0.3), (1.0, 0.5), (2.2, 0.2))  # (factor de pausa, probabilidad)
    MIN_PAUSE_S = 0.02
    MAX_PAUSE_S = 1.5

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        self._factor = 1.0
        self._letters_left = 0
        self._average_rtt = None
        self._load = 1.0

    def next_pause(self, base_s, rtt_ms):
        if self._letters_left <= 0:
            factors, weights = zip(*self.PHASES)
            self._factor = self.rng.choices(factors, weights)[0]
            self._letters_left = self.rng.randint(8, 30)
        self._letters_left -= 1

        if self._average_rtt is None:
            self._average_rtt = rtt_ms
        congested = rtt_ms > self._average_rtt * 1.5
        self._load = min(4.0, self._load * 1.25) if congested else max(1.0, self._load * 0.97)
        self._average_rtt = self._average_rtt * 0.9 + rtt_ms * 0.1

        pause = base_s * self._factor * self.rng.uniform(0.6, 1.4) * self._load
        return min(self.MAX_PAUSE_S, max(self.MIN_PAUSE_S, pause))


class Bridge:
    """Mantiene la conexión con el móvil y escribe la respuesta letra a letra.

    on_event(tipo, datos) se llama desde hilos secundarios.
    type_char(c) escribe una letra en la ventana activa.
    get_pause(rtt_ms) devuelve la pausa antes de la siguiente letra, en segundos.
    """

    def __init__(self, on_event, type_char, get_pause):
        self.on_event = on_event
        self.type_char = type_char
        self.get_pause = get_pause
        self._sock = None
        self._send_lock = threading.Lock()
        self._replies = queue.Queue()
        self._stop_typing = threading.Event()
        self._typing_thread = None
        self._state_lock = threading.Lock()
        self._next_prompt_id = 0
        self._response_id = None
        self._response_len = 0
        self._position = 0

    @property
    def connected(self):
        return self._sock is not None

    @property
    def typing(self):
        thread = self._typing_thread
        return thread is not None and thread.is_alive()

    @property
    def has_pending(self):
        """Hay una respuesta recibida que aún no se ha escrito entera."""
        return self._response_id is not None and self._position < self._response_len

    def attach(self, sock):
        """Hace el saludo sobre un socket ya abierto y empieza a escuchar."""
        sock.settimeout(REPLY_TIMEOUT_S)
        sock.sendall(encode_frame({"t": "hello", "v": PROTOCOL_VERSION}))
        reply = read_frame(sock)
        if reply.get("t") != "hello_ok":
            sock.close()
            raise ConnectionError("el dispositivo no ha respondido como la app del móvil")
        sock.settimeout(None)
        self._sock = sock
        threading.Thread(target=self._read_loop, args=(sock,), daemon=True).start()
        self.on_event("connected", None)

    def close(self):
        self.stop_typing()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # despierta al hilo que está esperando datos
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _send(self, message):
        sock = self._sock
        if sock is None:
            raise ConnectionError("sin conexión con el móvil")
        with self._send_lock:
            sock.sendall(encode_frame(message))

    def send_prompt(self, text):
        self.stop_typing()
        with self._state_lock:
            self._next_prompt_id += 1
            prompt_id = self._next_prompt_id
            self._response_id = None
            self._response_len = 0
            self._position = 0
        self._send({"t": "prompt", "id": prompt_id, "text": text})
        self.on_event("prompt_sent", text)

    def _read_loop(self, sock):
        try:
            while True:
                message = read_frame(sock)
                kind = message.get("t")
                if kind in ("char", "end"):
                    self._replies.put(message)
                elif kind == "ready":
                    self._on_ready(message)
                elif kind == "status":
                    self.on_event("status", message.get("text", ""))
                elif kind == "error":
                    self.on_event("error", message.get("text", "error en el móvil"))
        except (OSError, ValueError, struct.error):
            pass
        if self._sock is sock:
            self._sock = None
        self._replies.put(None)
        try:
            sock.close()
        except OSError:
            pass
        self.on_event("disconnected", None)

    def _on_ready(self, message):
        with self._state_lock:
            if message.get("id") != self._next_prompt_id:
                return  # respuesta a un enunciado ya sustituido por otro
            self._response_id = message["id"]
            self._response_len = message.get("len", 0)
            self._position = 0
        self.on_event("ready", self._response_len)

    def start_typing(self):
        """Empieza o continúa la escritura. Si ya había terminado, vuelve al principio."""
        if self.typing:
            return False
        with self._state_lock:
            if self._response_id is None:
                self.on_event("error", "Aún no hay respuesta que escribir")
                return False
            if self._position >= self._response_len:
                self._position = 0
        self._stop_typing.clear()
        self._typing_thread = threading.Thread(target=self._type_loop, daemon=True)
        self._typing_thread.start()
        return True

    def stop_typing(self):
        self._stop_typing.set()
        thread = self._typing_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=REPLY_TIMEOUT_S + 1)

    def _drain_replies(self):
        while True:
            try:
                self._replies.get_nowait()
            except queue.Empty:
                return

    def _await_reply(self, response_id, seq):
        deadline = time.perf_counter() + REPLY_TIMEOUT_S
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("el móvil no ha contestado a tiempo")
            reply = self._replies.get(timeout=remaining)
            if reply is None:
                raise ConnectionError("conexión perdida")
            if reply.get("id") == response_id and (reply.get("t") == "end" or reply.get("seq") == seq):
                return reply

    def _type_loop(self):
        with self._state_lock:
            response_id = self._response_id
        self._drain_replies()
        self.on_event("typing_started", None)
        reason = "stopped"
        try:
            while not self._stop_typing.is_set():
                seq = self._position
                started = time.perf_counter()
                self._send({"t": "next", "id": response_id, "seq": seq})
                reply = self._await_reply(response_id, seq)
                rtt_ms = (time.perf_counter() - started) * 1000
                if reply["t"] == "end":
                    reason = "finished"
                    break
                char = reply["c"]
                self.type_char(char)
                self._position = seq + 1
                pause_s = self.get_pause(rtt_ms)
                self.on_event("latency", (seq, char, rtt_ms, pause_s * 1000))
                if self._position >= self._response_len:
                    reason = "finished"
                    break
                self._stop_typing.wait(pause_s)
        except (OSError, queue.Empty, TimeoutError) as error:
            reason = "error"
            self.on_event("error", f"Escritura interrumpida: {error or 'sin respuesta del móvil'}")
        self.on_event("typing_finished", reason)
