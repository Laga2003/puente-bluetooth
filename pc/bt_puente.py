"""Puente Bluetooth para Windows: captura texto, lo envía al móvil y escribe la respuesta."""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import pyperclip
from pynput import keyboard

from puente_core import Bridge, Pace, open_link

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_CONFIG = {
    "mac": "",
    "delay_ms": 80,
    "auto_pace": True,
    "key_capture": "insert",
    "key_type": "delete",
    "key_stop": "space",
}
VK_CODES = {
    "insert": 0x2D,
    "delete": 0x2E,
    "space": 0x20,
    "pause": 0x13,
    "scroll_lock": 0x91,
    **{f"f{number}": 0x6F + number for number in range(1, 13)},
}
KEY_LABELS = {"insert": "Insert", "delete": "Supr", "space": "Espacio", "pause": "Pausa", "scroll_lock": "Bloq Despl"}
MAX_LOG_LINES = 300
UI_REFRESH_MS = 120


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            config.update(json.load(file))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2)
    except OSError:
        pass


def paired_devices():
    """Lista (nombre, MAC) de los dispositivos emparejados en Windows."""
    if sys.platform != "win32":
        return []
    import winreg

    devices = []
    path = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                raw = winreg.EnumKey(root, index)
                mac = ":".join(raw[i:i + 2] for i in range(0, 12, 2)).upper()
                try:
                    with winreg.OpenKey(root, raw) as item:
                        name = winreg.QueryValueEx(item, "Name")[0].split(b"\0")[0].decode("utf-8", "replace")
                except OSError:
                    name = "(sin nombre)"
                devices.append((name, mac))
    except OSError:
        pass
    return devices


class Keys:
    """Teclas globales y simulación de teclado.

    Cada tecla solo se reserva mientras su acción tiene sentido. El resto del
    tiempo llega a la ventana activa y funciona con normalidad.
    """

    MODIFIERS = {keyboard.Key.ctrl, keyboard.Key.alt, keyboard.Key.shift, keyboard.Key.cmd}
    KEY_DOWN = (0x100, 0x104)
    KEY_UP = (0x101, 0x105)
    INJECTED_FLAGS = 0x12

    def __init__(self, bindings, is_locked, unlock_vk):
        """bindings: lista de (código de tecla, función que indica si está activa, acción).

        Mientras is_locked() sea cierto se bloquean las pulsaciones físicas,
        salvo la tecla unlock_vk.
        """
        self.is_locked = is_locked
        self.unlock_vk = unlock_vk
        self.controller = keyboard.Controller()
        self.held = set()
        self.swallowed = set()
        self.bindings = {vk: (is_active, action) for vk, is_active, action in bindings}
        self.listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            win32_event_filter=self._filter,
        )

    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()

    def _on_press(self, key):
        if key is not None:
            self.held.add(self.listener.canonical(key))

    def _on_release(self, key):
        if key is not None:
            self.held.discard(self.listener.canonical(key))

    def _filter(self, msg, data):
        # Las pulsaciones simuladas por el programa, como los espacios que escribe, se ignoran.
        if not data.flags & self.INJECTED_FLAGS and self._should_swallow(msg, data.vkCode):
            self.listener.suppress_event()
        return True

    def _should_swallow(self, msg, vk):
        if msg in self.KEY_UP:
            if vk in self.swallowed:
                self.swallowed.discard(vk)
                return True
            return False
        if msg not in self.KEY_DOWN:
            return False
        if vk in self.swallowed:
            return True  # repetición por mantener la tecla pulsada
        locked = self.is_locked()
        if locked and vk != self.unlock_vk:
            return True
        binding = self.bindings.get(vk)
        if binding is None or (self.held & self.MODIFIERS and not locked):
            return False
        is_active, action = binding
        if not is_active():
            return False
        self.swallowed.add(vk)
        # El gancho de teclado no debe bloquearse: la acción va en su propio hilo.
        threading.Thread(target=action, daemon=True).start()
        return True

    def wait_for_release(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.held & self.MODIFIERS and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.05)

    def copy_selection(self):
        self.wait_for_release()
        previous = pyperclip.paste()
        pyperclip.copy("")
        with self.controller.pressed(keyboard.Key.ctrl):
            self.controller.tap("c")
        deadline = time.monotonic() + 1.0
        text = ""
        while not text and time.monotonic() < deadline:
            time.sleep(0.03)
            text = pyperclip.paste()
        pyperclip.copy(previous)
        return text.strip()

    def type_char(self, char):
        # Windows no puede enviar con pynput caracteres fuera del plano básico (emojis).
        if sys.platform == "win32" and ord(char[0]) > 0xFFFF:
            return
        self.controller.type(char)


class App:
    def __init__(self):
        self.config = load_config()
        self.events = queue.Queue()
        self.delay_ms = self.config["delay_ms"]
        self.auto_pace = bool(self.config["auto_pace"])
        self.pace = Pace()
        self.latencies = []
        self.pauses = []
        self.log_lines = 0
        self.connecting = False

        self.bridge = Bridge(
            on_event=lambda kind, data: self.events.put((kind, data)),
            type_char=self._type_char,
            get_pause=self._next_pause,
        )
        self.keys = Keys([
            (self._key_code("key_capture"), lambda: self.bridge.connected, self.on_capture),
            (self._key_code("key_type"), lambda: self.bridge.has_pending or self.bridge.typing, self.on_type),
            (self._key_code("key_stop"), lambda: self.bridge.typing, self.bridge.stop_typing),
        ], is_locked=lambda: self.bridge.typing, unlock_vk=self._key_code("key_stop"))

        self.root = tk.Tk()
        self.root.title("Puente Bluetooth")
        self.root.geometry("440x560")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.keys.start()
        self.root.after(UI_REFRESH_MS, self._drain_events)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        self.devices = paired_devices()
        ttk.Label(frame, text="Móvil emparejado (o escribe su MAC)").grid(row=0, column=0, columnspan=2, sticky="w")
        self.device_box = ttk.Combobox(frame, values=[f"{name}  [{mac}]" for name, mac in self.devices])
        self.device_box.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.device_box.set(next((f"{n}  [{m}]" for n, m in self.devices if m == self.config["mac"]), self.config["mac"]))
        self.connect_button = ttk.Button(frame, text="Conectar", command=self.on_connect)
        self.connect_button.grid(row=1, column=1)

        self.state_var = tk.StringVar(value="Sin conectar")
        ttk.Label(frame, textvariable=self.state_var, wraplength=410).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        hint = (f"Capturar: {self._pretty('key_capture')}    "
                f"Escribir: {self._pretty('key_type')}    "
                f"Parar: {self._pretty('key_stop')}")
        ttk.Label(frame, text=hint).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.delay_label = ttk.Label(frame, text=f"Pausa base entre letras: {self.delay_ms} ms")
        self.delay_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        scale = ttk.Scale(frame, from_=20, to=500, value=self.delay_ms, command=self.on_delay)
        scale.grid(row=5, column=0, columnspan=2, sticky="ew")

        self.auto_var = tk.BooleanVar(value=self.auto_pace)
        ttk.Checkbutton(frame, text="Ritmo automático e irregular", variable=self.auto_var,
                        command=self.on_auto_toggle).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.stats_var = tk.StringVar(value="Latencia (ida y vuelta): sin datos")
        ttk.Label(frame, textvariable=self.stats_var, wraplength=410).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 4))

        self.log = tk.Text(frame, height=16, width=40, state="disabled", font=("Consolas", 9))
        self.log.grid(row=8, column=0, columnspan=2, sticky="nsew")
        frame.rowconfigure(8, weight=1)

    def _key_name(self, setting):
        name = str(self.config[setting]).lower()
        return name if name in VK_CODES else DEFAULT_CONFIG[setting]

    def _key_code(self, setting):
        return VK_CODES[self._key_name(setting)]

    def _pretty(self, setting):
        name = self._key_name(setting)
        return KEY_LABELS.get(name, name.upper())

    def _next_pause(self, rtt_ms):
        base_s = self.delay_ms / 1000
        return self.pace.next_pause(base_s, rtt_ms) if self.auto_pace else base_s

    def on_auto_toggle(self):
        self.auto_pace = bool(self.auto_var.get())

    def _selected_mac(self):
        text = self.device_box.get().strip()
        if "[" in text and text.endswith("]"):
            text = text[text.rindex("[") + 1:-1]
        return text

    def on_delay(self, value):
        self.delay_ms = int(float(value))
        self.delay_label.config(text=f"Pausa base entre letras: {self.delay_ms} ms")

    def on_connect(self):
        if self.connecting:
            return
        if self.bridge.connected:
            self.bridge.close()
            return
        mac = self._selected_mac()
        self.connecting = True
        self.connect_button.state(["disabled"])
        self.state_var.set("Conectando…")

        def work():
            try:
                self.bridge.attach(open_link(mac))
                self.config["mac"] = mac
            except (OSError, ValueError) as error:
                self.events.put(("connect_failed", str(error)))
            self.events.put(("connect_done", None))

        threading.Thread(target=work, daemon=True).start()

    def on_capture(self):
        if not self.bridge.connected:
            self.events.put(("error", "Conecta primero con el móvil"))
            return
        self.bridge.stop_typing()
        text = self.keys.copy_selection()
        if not text:
            self.events.put(("error", "No había texto seleccionado"))
            return
        try:
            self.bridge.send_prompt(text)
        except OSError as error:
            self.events.put(("error", str(error)))

    def on_type(self):
        self.keys.wait_for_release()
        self.pace.reset()
        self.bridge.start_typing()

    def _type_char(self, char):
        self.keys.type_char(char)

    def _append_log(self, lines):
        self.log.config(state="normal")
        self.log.insert("end", "".join(lines))
        self.log_lines += len(lines)
        if self.log_lines > MAX_LOG_LINES:
            self.log.delete("1.0", f"{self.log_lines - MAX_LOG_LINES + 1}.0")
            self.log_lines = MAX_LOG_LINES
        self.log.config(state="disabled")
        self.log.see("end")

    def _update_stats(self):
        values = sorted(self.latencies)
        p95 = values[min(len(values) - 1, int(len(values) * 0.95))]
        self.stats_var.set(
            f"Letras: {len(values)}   media: {sum(values) / len(values):.1f} ms   "
            f"mín: {values[0]:.1f}   máx: {values[-1]:.1f}   p95: {p95:.1f}   "
            f"pausa media: {sum(self.pauses) / len(self.pauses):.0f} ms"
        )

    def _drain_events(self):
        lines = []
        latency_changed = False
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "latency":
                seq, char, rtt, pause = data
                shown = {"\n": "⏎", "\t": "⇥", " ": "␠"}.get(char, char)
                lines.append(f"#{seq + 1:<5} {shown:<2} {rtt:8.1f} ms  pausa {pause:5.0f} ms\n")
                self.latencies.append(rtt)
                self.pauses.append(pause)
                latency_changed = True
            elif kind == "typing_started":
                self.latencies = []
                self.pauses = []
                self.state_var.set(f"Escribiendo… Teclado bloqueado: pulsa {self._pretty('key_stop')} para parar")
            elif kind == "typing_finished":
                labels = {"finished": "Escritura terminada", "stopped": "Escritura parada", "error": "Escritura interrumpida"}
                self.state_var.set(labels[data])
            elif kind == "connected":
                self.state_var.set("Conectado. Selecciona un texto y pulsa el atajo de capturar")
                self.connect_button.config(text="Desconectar")
                save_config(self.config)
            elif kind == "disconnected":
                self.state_var.set("Desconectado")
                self.connect_button.config(text="Conectar")
            elif kind == "connect_failed":
                self.state_var.set(f"Error al conectar: {data}")
            elif kind == "connect_done":
                self.connecting = False
                self.connect_button.state(["!disabled"])
            elif kind == "prompt_sent":
                preview = data if len(data) <= 60 else data[:60] + "…"
                self.state_var.set(f"Enviado al móvil: {preview}")
            elif kind == "status":
                self.state_var.set(data)
            elif kind == "ready":
                self.state_var.set(f"Respuesta lista ({data} letras). Haz clic donde quieras y pulsa el atajo de escribir")
            elif kind == "error":
                self.state_var.set(data)
        if lines:
            self._append_log(lines)
        if latency_changed:
            self._update_stats()
        self.root.after(UI_REFRESH_MS, self._drain_events)

    def on_close(self):
        self.config["delay_ms"] = self.delay_ms
        self.config["auto_pace"] = self.auto_pace
        save_config(self.config)
        self.keys.stop()
        self.bridge.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
