#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DropΩ — передача файлов по локальной сети (UI на CustomTkinter).
Python 3. Нужен:  pip install customtkinter
Запуск:  python drop.py

Тема следует за системой; акцентный цвет берётся из Windows
(Персонализация -> Цвета -> Акцентный цвет).
Если рядом с exe/скриптом лежит icon.ico — он используется как логотип окна.
"""

import socket
import struct
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import http.server
import urllib.parse
import mimetypes

import customtkinter as ctk

DISCOVER_REQ = b"FILESHARE_DISCOVER"
DISCOVER_RESP = b"FILESHARE_RESPONSE "
CHUNK = 65536

# Глобальный акцентный цвет (заполняется в apply_accent_theme)
ACCENT_HEX = "#1f6aa5"
ACCENT_COLORS = (ACCENT_HEX, ACCENT_HEX)   # (light, dark)


# ---------------- Иконка / акцент / система ----------------

def get_icon_path():
    """Ищет icon.ico рядом с exe (или со скриптом). Возвращает путь или None."""
    if getattr(sys, "frozen", False):
        # запущено как .exe (PyInstaller и т.п.) — ищем рядом с exe
        base_dir = os.path.dirname(sys.executable)
    else:
        # запущено как .py — ищем рядом со скриптом
        base_dir = os.path.dirname(os.path.abspath(__file__))
    icon = os.path.join(base_dir, "icon.ico")
    return icon if os.path.exists(icon) else None


def get_windows_accent_color():
    """Возвращает (r, g, b) акцентного цвета Windows или None."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\DWM")
        value, _ = winreg.QueryValueEx(key, "AccentColor")   # формат AABBGGRR
        winreg.CloseKey(key)
        r = value & 0xFF
        g = (value >> 8) & 0xFF
        b = (value >> 16) & 0xFF
        return (r, g, b)
    except Exception:
        pass
    try:
        # запасной вариант — старый ключ (0xAARRGGBB)
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\DWM")
        value, _ = winreg.QueryValueEx(key, "ColorizationColor")
        winreg.CloseKey(key)
        b = value & 0xFF
        g = (value >> 8) & 0xFF
        r = (value >> 16) & 0xFF
        return (r, g, b)
    except Exception:
        return None


def rgb_to_hex(rgb):
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def lighten(rgb, amount):
    r, g, b = rgb
    return (
        int(min(255, r + (255 - r) * amount)),
        int(min(255, g + (255 - g) * amount)),
        int(min(255, b + (255 - b) * amount)),
    )


def apply_accent_theme():
    """Применяет акцентный цвет Windows к теме CustomTkinter."""
    global ACCENT_HEX, ACCENT_COLORS
    accent = get_windows_accent_color()
    if accent is None:
        # не Windows или не удалось прочитать — оставляем встроенную тему
        return

    hex_accent = rgb_to_hex(accent)
    hover_light = rgb_to_hex(lighten(accent, 0.15))
    hover_dark = rgb_to_hex(lighten(accent, 0.08))

    ACCENT_HEX = hex_accent
    ACCENT_COLORS = (hex_accent, hex_accent)

    try:
        theme = ctk.ThemeManager.theme
    except AttributeError:
        theme = {}

    def setc(widget, key, light, dark):
        if widget in theme and key in theme[widget]:
            theme[widget][key] = [light, dark]

    # Кнопки
    setc("CTkButton", "fg_color", hex_accent, hex_accent)
    setc("CTkButton", "hover_color", hover_light, hover_dark)
    setc("CTkButton", "border_color", hex_accent, hex_accent)
    # Прогрессбар
    setc("CTkProgressbar", "progress_color", hex_accent, hex_accent)
    # Свитч / слайдер
    setc("CTkSwitch", "progress_color", hex_accent, hex_accent)
    setc("CTkSlider", "progress_color", hex_accent, hex_accent)
    # Радиокнопки / чекбоксы
    setc("CTkRadioButton", "fg_color", hex_accent, hex_accent)
    setc("CTkRadioButton", "border_color", hex_accent, hex_accent)
    setc("CTkRadioButton", "border_color_checked", hex_accent, hex_accent)
    setc("CTkCheckBox", "fg_color", hex_accent, hex_accent)
    setc("CTkCheckBox", "border_color", hex_accent, hex_accent)
    setc("CTkCheckBox", "checkmark_color", hex_accent, hex_accent)
    # Сегмент-контрол
    setc("CTkSegmentedButton", "selected_color", hex_accent, hex_accent)
    setc("CTkSegmentedButton", "selected_hover_color", hover_light, hover_dark)


# ---------------- Сетевые утилиты ----------------

def get_local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if not ip.startswith("127."):
            ips.add(ip)
        s.close()
    except Exception:
        pass
    return sorted(ips)


def get_broadcast_addresses():
    addrs = {"255.255.255.255"}
    for ip in get_local_ips():
        parts = ip.split(".")
        if len(parts) == 4:
            a, b, c, _ = parts
            addrs.add(f"{a}.{b}.{c}.255")
            addrs.add(f"{a}.{b}.255.255")
            addrs.add(f"{a}.255.255.255")
    return sorted(addrs)


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Соединение закрыто")
        data += chunk
    return data


def send_json(sock, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_json(sock):
    length = struct.unpack(">I", recv_exact(sock, 4))[0]
    if length > 10 * 1024 * 1024:
        raise ConnectionError("Некорректный размер заголовка")
    return json.loads(recv_exact(sock, length).decode("utf-8"))


def human_size(n):
    size = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            if unit == "Б":
                return f"{int(size)} Б"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} Б"


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{base} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


# ---------------- Отправитель (TCP) ----------------

class Sender:
    def __init__(self, port, files, on_status=None, on_progress=None):
        self.port = port
        self.files = list(files)
        self.on_status = on_status
        self.on_progress = on_progress
        self._stop = threading.Event()
        self.tcp_sock = None
        self.udp_sock = None

    def start(self):
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_sock.bind(("0.0.0.0", self.port))
        self.tcp_sock.listen(10)
        self.tcp_sock.settimeout(1.0)

        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp_sock.bind(("0.0.0.0", self.port))
        self.udp_sock.settimeout(1.0)

        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._udp_loop, daemon=True).start()

    def _file_info(self):
        return [{"name": os.path.basename(p), "size": os.path.getsize(p)} for p in self.files]

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, addr = self.tcp_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()

    def _udp_loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.udp_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if data == DISCOVER_REQ:
                info = {"host": socket.gethostname(), "files": self._file_info()}
                try:
                    self.udp_sock.sendto(
                        DISCOVER_RESP + json.dumps(info, ensure_ascii=False).encode("utf-8"), addr)
                except OSError:
                    pass

    def _handle_client(self, conn, addr):
        try:
            files_info = self._file_info()
            send_json(conn, {"host": socket.gethostname(), "files": files_info})
            if recv_exact(conn, 2) != b"OK":
                return
            if self.on_status:
                self.on_status(f"Клиент {addr[0]} начал загрузку")
            total = sum(f["size"] for f in files_info)
            sent = 0
            for p in self.files:
                with open(p, "rb") as f:
                    while not self._stop.is_set():
                        chunk = f.read(CHUNK)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                        sent += len(chunk)
                        if self.on_progress:
                            self.on_progress(sent, total)
            if self.on_status:
                self.on_status("Передача завершена")
        except Exception as e:
            if self.on_status:
                self.on_status(f"Ошибка передачи: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        for s in (self.tcp_sock, self.udp_sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


# ---------------- Отправитель (HTTP) ----------------

class HttpSharer:
    def __init__(self, port, files, on_status=None):
        self.port = port
        self.files = list(files)
        self.on_status = on_status
        self.httpd = None
        self.name_map = self._build_name_map(files)

    @staticmethod
    def _build_name_map(files):
        name_map = {}
        used = set()
        for p in files:
            base = os.path.basename(p)
            name = base
            i = 1
            while name in used:
                stem, ext = os.path.splitext(base)
                name = f"{stem}_{i}{ext}"
                i += 1
            used.add(name)
            name_map[name] = p
        return name_map

    def start(self):
        name_map = self.name_map

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                raw = urllib.parse.unquote(self.path)
                path = raw.split("?", 1)[0].lstrip("/")
                if path in ("", "index.html"):
                    self._serve_index()
                    return
                if path in name_map:
                    self._serve_file(path)
                else:
                    self.send_error(404, "File not found")

            def _serve_index(self):
                parts = [
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>",
                    "<title>Файлы</title>",
                    "<style>body{font-family:sans-serif;max-width:760px;margin:40px auto;"
                    "padding:0 16px;color:#222}"
                    "h2{border-bottom:2px solid #1a73e8;padding-bottom:8px}"
                    "a{display:block;padding:10px 0;text-decoration:none;color:#1a73e8;"
                    "border-bottom:1px solid #eee}"
                    "a:hover{text-decoration:underline}.size{color:#888;margin-left:10px}"
                    "</style></head><body><h2>📂 Доступные файлы</h2>",
                ]
                for name, path in name_map.items():
                    size = human_size(os.path.getsize(path))
                    q = urllib.parse.quote(name)
                    parts.append(
                        f'<div><a href="/{q}">📄 {name}</a>'
                        f'<span class="size">{size}</span></div>')
                parts.append("<p style='color:#888;margin-top:20px'>Нажмите на файл, чтобы скачать.</p></body></html>")
                body = "".join(parts).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _serve_file(self, name):
                fp = name_map[name]
                size = os.path.getsize(fp)
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                ascii_name = name.encode("ascii", "ignore").decode("ascii") or "file"
                disp = (f"attachment; filename=\"{ascii_name}\"; "
                        f"filename*=UTF-8''{urllib.parse.quote(name)}")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", disp)
                self.end_headers()
                with open(fp, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if self.on_status:
            self.on_status("HTTP-сервер запущен")

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


# ---------------- Получатель ----------------

def discover(port, timeout=2.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)
    for b in get_broadcast_addresses():
        try:
            sock.sendto(DISCOVER_REQ, (b, port))
        except OSError:
            pass
    results = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        if data.startswith(DISCOVER_RESP):
            try:
                payload = json.loads(data[len(DISCOVER_RESP):].decode("utf-8"))
            except ValueError:
                continue
            payload["ip"] = addr[0]
            results[addr[0]] = payload
    sock.close()
    return list(results.values())


def receive_files(host, port, save_dir, on_status=None, on_progress=None):
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(60)
    try:
        info = recv_json(sock)
        files = info.get("files", [])
        sock.sendall(b"OK")
        total = sum(f["size"] for f in files)
        done = 0
        count = 0
        for f in files:
            name = os.path.basename(str(f["name"])).strip() or "file"
            dest = unique_path(os.path.join(save_dir, name))
            if on_status:
                on_status(f"Сохранение: {name}")
            with open(dest, "wb") as fh:
                remaining = f["size"]
                while remaining > 0:
                    chunk = sock.recv(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("Соединение разорвано")
                    fh.write(chunk)
                    remaining -= len(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            count += 1
        return count
    finally:
        sock.close()


# ---------------- UI ----------------

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DropΩ - Передача файлов по локальной сети")
        self.geometry("400x520")

        # логотип из icon.ico, если файл лежит рядом с exe/скриптом
        icon = get_icon_path()
        if icon:
            try:
                self.iconbitmap(icon)
            except Exception:
                pass  # не удалось загрузить иконку — оставляем стандартную

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=10, pady=10)

        self.frames = {}
        for F in (MenuFrame, ShareFrame, ReceiveFrame):
            frame = F(self.container, self)
            self.frames[F.__name__] = frame
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.show("MenuFrame")

    def show(self, name):
        self.frames[name].tkraise()


class MenuFrame(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        ctk.CTkLabel(self, text="Выберите действие",
                     font=ctk.CTkFont(size=14)).pack(pady=5)

        ctk.CTkButton(self, text="Поделиться файлами", width=280, height=44,
                      command=lambda: app.show("ShareFrame")).pack(pady=8)
        ctk.CTkButton(self, text="Получить файлы", width=280, height=44,
                      command=lambda: app.show("ReceiveFrame")).pack(pady=8)

        ctk.CTkLabel(self, text="Устройства должны быть в одной локальной сети",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(pady=12)

        ctk.CTkLabel(self, text="Тема оформления",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(8, 4))
        self.seg = ctk.CTkSegmentedButton(
            self, values=["Система", "Светлая", "Тёмная"],
            command=self._on_appearance)
        self.seg.set("Система")
        self.seg.pack(pady=5)

    def _on_appearance(self, value):
        mapping = {"Система": "system", "Светлая": "light", "Тёмная": "dark"}
        ctk.set_appearance_mode(mapping[value])


class ShareFrame(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.files = []
        self.sender = None
        self.http_sharer = None

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(top, text="← Назад", width=80,
                      command=self._back).pack(side="left")

        ctk.CTkLabel(self, text="1. Выберите файлы для передачи").pack(anchor="w")
        ctk.CTkButton(self, text="Выбрать файлы…", width=150, height=32,
                      command=self._choose_files).pack(anchor="w", pady=6)

        self.files_box = ctk.CTkTextbox(self, height=90, state="disabled", wrap="none")
        self.files_box.pack(fill="x", pady=5)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=5)
        ctk.CTkLabel(row, text="2. Порт:").pack(side="left")
        self.port_var = tk.StringVar(value="5000")
        ctk.CTkEntry(row, textvariable=self.port_var, width=80).pack(side="left", padx=8)

        self.start_btn = ctk.CTkButton(self, text="Начать раздачу (TCP, через программу)",
                                       height=34, command=self._start)
        self.start_btn.pack(pady=5)
        self.http_btn = ctk.CTkButton(self, text="Поделиться по HTTP (через браузер)",
                                      height=34, command=self._start_http)
        self.http_btn.pack(pady=5)
        self.stop_btn = ctk.CTkButton(self, text="Остановить", command=self._stop,
                                      height=34, state="disabled", fg_color="#c0392b",
                                      hover_color="#a93226")
        self.stop_btn.pack(pady=5)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=6)

        self.status = ctk.CTkLabel(self, text="", text_color=("#0a7a3a", "#4ade80"),
                                   wraplength=360, justify="left")
        self.status.pack(anchor="w", pady=5)

    def _back(self):
        self._stop()
        self.app.show("MenuFrame")

    def _choose_files(self):
        paths = filedialog.askopenfilenames(title="Выберите файлы")
        if paths:
            self.files = list(paths)
            self.files_box.configure(state="normal")
            self.files_box.delete("1.0", "end")
            for p in self.files:
                self.files_box.insert("end",
                    f"{os.path.basename(p)}  ({human_size(os.path.getsize(p))})\n")
            self.files_box.configure(state="disabled")

    def _port(self):
        try:
            p = int(self.port_var.get())
            if not (1 <= p <= 65535):
                raise ValueError
            return p
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректный порт (1–65535)")
            return None

    def _require_files_and_port(self):
        if not self.files:
            messagebox.showwarning("Нет файлов", "Сначала выберите файлы")
            return None
        return self._port()

    def _start(self):
        port = self._require_files_and_port()
        if port is None:
            return
        self._stop()
        self.progress.set(0)
        self.sender = Sender(port, self.files, self._status, self._progress)
        try:
            self.sender.start()
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось запустить сервер: {e}")
            self.sender = None
            return
        self._set_running()
        ips = ", ".join(get_local_ips()) or "не определены"
        self.status.configure(text=f"Раздача (TCP) запущена. Порт: {port}\nIP этого устройства: {ips}")

    def _start_http(self):
        port = self._require_files_and_port()
        if port is None:
            return
        self._stop()
        self.http_sharer = HttpSharer(port, self.files, self._status)
        try:
            self.http_sharer.start()
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось запустить HTTP-сервер: {e}")
            self.http_sharer = None
            return
        self._set_running()
        ips = get_local_ips()
        links = "\n".join(f"  http://{ip}:{port}/" for ip in ips)
        if not links:
            links = f"  http://127.0.0.1:{port}/"
        self.status.configure(text=f"HTTP-раздача запущена. Откройте в браузере:\n{links}")
        messagebox.showinfo("HTTP-раздача",
                            "Файлы доступны по адресу (откройте в браузере):\n\n" + links)

    def _set_running(self):
        self.start_btn.configure(state="disabled")
        self.http_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

    def _stop(self):
        if self.sender:
            self.sender.stop()
            self.sender = None
        if self.http_sharer:
            self.http_sharer.stop()
            self.http_sharer = None
        self.start_btn.configure(state="normal")
        self.http_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _status(self, text):
        self.after(0, lambda: self.status.configure(text=text))

    def _progress(self, done, total):
        val = done / total if total else 0
        self.after(0, lambda: self.progress.set(val))


class ReceiveFrame(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.save_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        self.sender_buttons = []

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(top, text="← Назад", width=80,
                      command=self._back).pack(side="left")

        ctk.CTkLabel(self, text="Как найти отправителя?").pack(anchor="w")
        self.mode_var = tk.StringVar(value="auto")
        ctk.CTkRadioButton(self, text="Найти автоматически (по порту)",
                           variable=self.mode_var, value="auto",
                           command=self._toggle_mode).pack(anchor="w", padx=4, pady=3)
        ctk.CTkRadioButton(self, text="Ввести IP и порт вручную",
                           variable=self.mode_var, value="manual",
                           command=self._toggle_mode).pack(anchor="w", padx=4, pady=3)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=6)
        ctk.CTkLabel(row, text="Порт:").pack(side="left")
        self.port_var = tk.StringVar(value="5000")
        ctk.CTkEntry(row, textvariable=self.port_var, width=80).pack(side="left", padx=8)

        self.ip_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.ip_row, text="IP:").pack(side="left")
        self.ip_var = tk.StringVar()
        ctk.CTkEntry(self.ip_row, textvariable=self.ip_var, width=150).pack(side="left", padx=8)

        self.action_btn = ctk.CTkButton(self, text="Найти отправителей",
                                        height=34, command=self._on_action)
        self.action_btn.pack(pady=6)

        self.senders_frame = ctk.CTkScrollableFrame(self, height=110)
        self.senders_frame.pack(fill="x", pady=5)

        srow = ctk.CTkFrame(self, fg_color="transparent")
        srow.pack(fill="x", pady=6)
        ctk.CTkButton(srow, text="Папка…", width=90, height=30,
                      command=self._choose_dir).pack(side="left")
        self.save_label = ctk.CTkLabel(srow, text=self.save_dir,
                                       text_color="gray", font=ctk.CTkFont(size=11))
        self.save_label.pack(side="left", padx=8)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=6)

        self.status = ctk.CTkLabel(self, text="", text_color=("#0a7a3a", "#4ade80"),
                                   wraplength=360, justify="left")
        self.status.pack(anchor="w", pady=5)

        self._toggle_mode()

    def _back(self):
        self.app.show("MenuFrame")

    def _toggle_mode(self):
        if self.mode_var.get() == "manual":
            self.ip_row.pack(fill="x", pady=5)
            self.action_btn.configure(text="Подключиться")
            self.senders_frame.pack_forget()
        else:
            self.ip_row.pack_forget()
            self.action_btn.configure(text="Найти отправителей")
            self.senders_frame.pack(fill="x", pady=5, before=self.action_btn)

    def _port(self):
        try:
            p = int(self.port_var.get())
            if not (1 <= p <= 65535):
                raise ValueError
            return p
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректный порт (1–65535)")
            return None

    def _choose_dir(self):
        d = filedialog.askdirectory(title="Папка сохранения")
        if d:
            self.save_dir = d
            self.save_label.configure(text=d)

    def _on_action(self):
        port = self._port()
        if port is None:
            return
        if self.mode_var.get() == "manual":
            host = self.ip_var.get().strip()
            if not host:
                messagebox.showwarning("Нет IP", "Введите IP-адрес отправителя")
                return
            self._start_receive(host, port)
        else:
            self.status.configure(text="Поиск отправителей…")
            self.action_btn.configure(state="disabled")
            threading.Thread(target=self._discover_bg, args=(port,), daemon=True).start()

    def _discover_bg(self, port):
        err = None
        try:
            results = discover(port)
        except Exception as e:
            results = []
            err = str(e)

        def update():
            self.action_btn.configure(state="normal")
            if err:
                self.status.configure(text=f"Ошибка: {err}")
                return
            for b in self.sender_buttons:
                b.destroy()
            self.sender_buttons.clear()
            if not results:
                self.status.configure(text="Отправители не найдены. Проверьте порт и сеть.")
            else:
                for r in results:
                    files = r.get("files", [])
                    total = sum(f.get("size", 0) for f in files)
                    text = (f"📥 {r['ip']}  ·  {r.get('host', '')}  ·  "
                            f"файлов: {len(files)}  ·  {human_size(total)}")
                    btn = ctk.CTkButton(
                        self.senders_frame, text=text, anchor="w", height=32,
                        command=lambda ip=r["ip"], p=port: self._start_receive(ip, p))
                    btn.pack(fill="x", padx=2, pady=2)
                    self.sender_buttons.append(btn)
                self.status.configure(text=f"Найдено отправителей: {len(results)}")

        self.after(0, update)

    def _start_receive(self, host, port):
        self.action_btn.configure(state="disabled")
        for b in self.sender_buttons:
            b.configure(state="disabled")
        self.progress.set(0)
        self.status.configure(text=f"Подключение к {host}:{port}…")
        threading.Thread(target=self._receive_bg, args=(host, port), daemon=True).start()

    def _receive_bg(self, host, port):
        err = None
        count = 0

        def status(t):
            self.after(0, lambda: self.status.configure(text=t))

        def prog(done, total):
            val = done / total if total else 0
            self.after(0, lambda: self.progress.set(val))

        try:
            count = receive_files(host, port, self.save_dir, status, prog)
        except Exception as e:
            err = str(e)

        def update():
            self.action_btn.configure(state="normal")
            for b in self.sender_buttons:
                b.configure(state="normal")
            if err:
                self.status.configure(text=f"Ошибка: {err}")
            else:
                self.status.configure(text=f"Готово! Получено файлов: {count}")

        self.after(0, update)


def main():
    ctk.set_appearance_mode("system")          # тема следует за системой
    ctk.set_default_color_theme("blue")        # базовая тема (потом перекрашиваем акцентом)
    apply_accent_theme()                       # акцентный цвет из Windows
    App().mainloop()


if __name__ == "__main__":
    main()
