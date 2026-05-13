# -*- coding: utf-8 -*-
"""
sensor.py - Agente sensor para PC del empleado
Detecta si el equipo esta dentro o fuera de la red de la empresa,
escanea redes WiFi cercanas para geolocalizacion y envia heartbeats al servidor.
"""

import requests
import socket
import uuid
import time
import os
import sys
import json
import platform
import subprocess
CREATE_NO_WINDOW = 0x08000000  # Ocultar ventanas CMD en Windows
import re
import traceback
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIGURACION BASE (se puede sobrescribir
#  con sensor_config.json junto al .exe/.py)
# ─────────────────────────────────────────────
DEFAULT_SERVER_URL = "https://mountable-heroics-doorpost.ngrok-free.dev"
DEFAULT_PING_INTERVAL = 30
DEFAULT_EMPRESA_RED = "192.168.80."
DEFAULT_WIFI_EMPRESAS = [
    "Comite IA",
    "MC emegencia",
    "Mundo Charro Administracion",
    "Mundo_Charro",
    "Mundo Charro Tecnologias",
    "MundoCharroExpansiones",
    "Mundo Charro VISITA",
    "DIRECCION MUNDO CHARRO",
    "Mundo Charro Invitados"
]
# ─────────────────────────────────────────────

INVALID_IDS = {
    "",
    "to be filled by o.e.m.",
    "default string",
    "none",
    "null",
    "system serial number",
}

LOG_DIR = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), "SensorEquipo")
LOG_FILE = os.path.join(LOG_DIR, "sensor.log")

SERVER_URL = DEFAULT_SERVER_URL
PING_INTERVAL = DEFAULT_PING_INTERVAL
EMPRESA_RED = DEFAULT_EMPRESA_RED
WIFI_EMPRESAS = list(DEFAULT_WIFI_EMPRESAS)

# ─────────────────────────────────────────────
#  HEADERS para ngrok (evita pantalla de
#  advertencia ERR_NGROK_6024)
# ─────────────────────────────────────────────
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "true"
}


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", errors="ignore") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def cargar_config():
    """
    Carga configuración opcional desde sensor_config.json.
    """
    global SERVER_URL, PING_INTERVAL, EMPRESA_RED, WIFI_EMPRESAS

    config_path = os.path.join(_base_dir(), "sensor_config.json")
    if not os.path.exists(config_path):
        log("Config no encontrada; usando valores por defecto.")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        SERVER_URL = str(cfg.get("SERVER_URL", DEFAULT_SERVER_URL)).strip() or DEFAULT_SERVER_URL
        PING_INTERVAL = int(cfg.get("PING_INTERVAL", DEFAULT_PING_INTERVAL))
        if PING_INTERVAL < 10:
            PING_INTERVAL = 10

        EMPRESA_RED = str(cfg.get("EMPRESA_RED", DEFAULT_EMPRESA_RED)).strip() or DEFAULT_EMPRESA_RED
        wifi_cfg = cfg.get("WIFI_EMPRESAS", DEFAULT_WIFI_EMPRESAS)
        if isinstance(wifi_cfg, list) and wifi_cfg:
            WIFI_EMPRESAS = [str(x).strip() for x in wifi_cfg if str(x).strip()]
        else:
            WIFI_EMPRESAS = list(DEFAULT_WIFI_EMPRESAS)

        log(f"Config cargada desde: {config_path}")
    except Exception as e:
        log(f"Error cargando config ({config_path}): {e}. Usando defaults.")


def _normalizar_id(value):
    v = (value or "").strip().strip('"').strip("'")
    if not v:
        return ""
    if v.lower() in INVALID_IDS:
        return ""
    return v


def _powershell(cmd):
    output = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", cmd],
        text=True,
        stderr=subprocess.DEVNULL,
        encoding="utf-8",
        errors="ignore"
    ,
                creationflags=CREATE_NO_WINDOW
            )
    return output.strip()


def get_device_id():
    id_file = os.path.join(os.path.expanduser("~"), ".sensor_device_id")
    if os.path.exists(id_file):
        with open(id_file) as f:
            return f.read().strip()
    device_id = str(uuid.uuid4())
    with open(id_file, "w") as f:
        f.write(device_id)
    return device_id


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def get_wifi_ssid():
    try:
        sistema = platform.system()
        if sistema == "Windows":
            output = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"], text=True, stderr=subprocess.DEVNULL
            ,
                creationflags=CREATE_NO_WINDOW
            )
            for line in output.split("\n"):
                if "SSID" in line and "BSSID" not in line:
                    return ":".join(line.split(":")[1:]).strip()
        elif sistema == "Darwin":
            output = subprocess.check_output(
                ["/System/Library/PrivateFrameworks/Apple80211.framework/"
                 "Versions/Current/Resources/airport", "-I"], text=True
            ,
                creationflags=CREATE_NO_WINDOW
            )
            for line in output.split("\n"):
                if " SSID:" in line:
                    return line.split(":")[1].strip()
        elif sistema == "Linux":
            output = subprocess.check_output(["iwgetid", "-r"], text=True,
                creationflags=CREATE_NO_WINDOW
            )
            return output.strip()
    except Exception:
        pass
    return ""


def scan_wifi_networks():
    """
    Escanea todas las redes WiFi cercanas y devuelve lista de
    {macAddress, signalStrength} para Google Geolocation API.
    """
    redes_map = {}
    try:
        if platform.system() != "Windows":
            return []

        output = subprocess.check_output(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            text=True, stderr=subprocess.DEVNULL, encoding="utf-8", errors="ignore"
        ,
                creationflags=CREATE_NO_WINDOW
            )

        bssid = None

        for line in output.split("\n"):
            line = line.strip()

            # Detectar BSSID
            bssid_match = re.search(r'BSSID\s+\d+\s*:\s*([0-9a-fA-F:]{17})', line)
            if bssid_match:
                bssid = bssid_match.group(1).upper()
                continue

            # Detectar señal — acepta "Señal", "Senal", "Signal" con o sin tilde
            if bssid and re.search(r'(\d+)%', line):
                line_lower = line.lower()
                # Normalizar tildes para comparacion
                line_norm = line_lower.replace("\xe3", "e").replace("\xf1", "n")
                if any(k in line_norm for k in ["se", "signal", "sign"]):
                    pct_match = re.search(r'(\d+)%', line)
                    if pct_match:
                        pct = int(pct_match.group(1))
                        signal = int((pct / 2) - 100)
                        prev = redes_map.get(bssid)
                        if prev is None or signal > prev["signalStrength"]:
                            redes_map[bssid] = {
                                "macAddress": bssid,
                                "signalStrength": signal
                            }
                        bssid = None

    except Exception as e:
        print(f"Error escaneando WiFi: {e}")

    redes = [r for r in redes_map.values() if r["signalStrength"] >= -85]
    redes.sort(key=lambda x: x["signalStrength"], reverse=True)
    return redes[:12]


def esta_en_red_empresa(ip, ssid):
    if ip.startswith(EMPRESA_RED):
        return True
    if ssid and ssid in WIFI_EMPRESAS:
        return True
    return False


def get_hostname():
    return socket.gethostname()


def get_serial_number():
    """
    Obtiene identificador de hardware para relacionarlo con inventario.
    Retorna (valor, fuente).
    """
    try:
        sistema = platform.system()
        if sistema == "Windows":
            # Intento 1: WMIC
            try:
                output = subprocess.check_output(
                    ["wmic", "bios", "get", "serialnumber"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    encoding="utf-8",
                    errors="ignore"
                ,
                creationflags=CREATE_NO_WINDOW
            )
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                for line in lines:
                    if line.lower() != "serialnumber":
                        value = _normalizar_id(line)
                        if value:
                            return value, "bios_wmic"
            except Exception:
                pass

            # Intento 2: PowerShell/CIM
            try:
                serial = _powershell("(Get-CimInstance Win32_BIOS).SerialNumber")
                value = _normalizar_id(serial.splitlines()[0] if serial else "")
                if value:
                    return value, "bios_cim"
            except Exception:
                pass

            # Intento 3: Serial de tarjeta madre
            try:
                board = _powershell("(Get-CimInstance Win32_BaseBoard).SerialNumber")
                value = _normalizar_id(board.splitlines()[0] if board else "")
                if value:
                    return value, "baseboard_cim"
            except Exception:
                pass

            # Intento 4: UUID del equipo
            try:
                uuid_value = _powershell("(Get-CimInstance Win32_ComputerSystemProduct).UUID")
                value = _normalizar_id(uuid_value.splitlines()[0] if uuid_value else "")
                if value:
                    return value, "uuid_cim"
            except Exception:
                pass

        elif sistema == "Linux":
            if os.path.exists("/sys/class/dmi/id/product_serial"):
                with open("/sys/class/dmi/id/product_serial", "r", encoding="utf-8", errors="ignore") as f:
                    value = _normalizar_id(f.read())
                    if value:
                        return value, "bios_file"
        elif sistema == "Darwin":
            output = subprocess.check_output(
                ["system_profiler", "SPHardwareDataType"],
                text=True,
                stderr=subprocess.DEVNULL
            ,
                creationflags=CREATE_NO_WINDOW
            )
            for line in output.splitlines():
                if "Serial Number" in line:
                    value = _normalizar_id(line.split(":")[-1])
                    if value:
                        return value, "bios_system_profiler"
    except Exception:
        pass
    return "", "none"


def send_ping(device_id, serial_number, serial_source, ip, ssid, dentro, wifi_networks):
    payload = {
        "device_id":     device_id,
        "serial_number": serial_number,
        "serial_source": serial_source,
        "hostname":      get_hostname(),
        "ip":            ip,
        "ssid":          ssid,
        "dentro":        dentro,
        "sistema":       platform.system(),
        "timestamp":     datetime.utcnow().isoformat(),
        "wifi_networks": wifi_networks
    }
    try:
        resp = requests.post(
            f"{SERVER_URL}/api/ping",
            json=payload,
            timeout=10,
            headers=HEADERS       # <-- FIX: header ngrok-skip-browser-warning
        )
        estado = "DENTRO  OK" if dentro else "FUERA   !!"
        log(f"{estado} | IP: {ip} | SSID: {ssid or 'N/A'} | Redes: {len(wifi_networks)} | HTTP {resp.status_code}")
    except requests.exceptions.ConnectionError:
        log(f"Sin conexion al servidor - reintentando en {PING_INTERVAL}s")
    except Exception as e:
        log(f"Error enviando ping: {e}")


def main():
    cargar_config()
    device_id = get_device_id()
    serial_number, serial_source = get_serial_number()
    log(f"Sensor iniciado - Device ID: {device_id}")
    log(f"Identificador HW: {serial_number or 'no detectado'} ({serial_source})")
    log(f"Servidor: {SERVER_URL}")
    log(f"Intervalo: {PING_INTERVAL}s")

    while True:
        try:
            if not serial_number:
                serial_number, serial_source = get_serial_number()

            ip            = get_local_ip()
            ssid          = get_wifi_ssid()
            dentro        = esta_en_red_empresa(ip, ssid)
            wifi_networks = scan_wifi_networks()

            log(f"Redes encontradas: {len(wifi_networks)}")
            send_ping(device_id, serial_number, serial_source, ip, ssid, dentro, wifi_networks)
        except Exception as e:
            log(f"Error ciclo principal: {e}")
            log(traceback.format_exc())
        time.sleep(PING_INTERVAL)


if __name__ == "__main__":
    main()