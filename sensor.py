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
import ctypes
import winreg
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIGURACION BASE (se puede sobrescribir
#  con sensor_config.json junto al .exe/.py)
# ─────────────────────────────────────────────
DEFAULT_SERVER_URL = "https://w-5tf5.onrender.com"
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
#  HEADERS base para llamadas HTTP
# ─────────────────────────────────────────────
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "true"
}

USBSTOR_REG_PATH = r"SYSTEM\CurrentControlSet\Services\USBSTOR"
USB_STORAGE_ENABLE_VALUE = 3
USB_STORAGE_DISABLE_VALUE = 4
DEVICE_INSTALL_RESTRICTIONS_PATH = r"SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions"
DENY_DEVICE_CLASSES_PATH = DEVICE_INSTALL_RESTRICTIONS_PATH + r"\DenyDeviceClasses"
WPD_CLASS_GUID = "{EEC5AD98-8080-425F-922A-DABF3DE3F69A}"


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


def is_windows_admin():
    if platform.system() != "Windows":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_usb_storage_blocked():
    if platform.system() != "Windows":
        return None, "solo_windows"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, USBSTOR_REG_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "Start")
        return int(value) == USB_STORAGE_DISABLE_VALUE, ""
    except Exception as e:
        return None, str(e)


def set_usb_storage_blocked(blocked):
    if platform.system() != "Windows":
        return None, "solo_windows"
    if not is_windows_admin():
        current, current_error = get_usb_storage_blocked()
        return current, current_error or "requiere_permisos_administrador"
    try:
        value = USB_STORAGE_DISABLE_VALUE if blocked else USB_STORAGE_ENABLE_VALUE
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, USBSTOR_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, value)
        return bool(blocked), ""
    except Exception as e:
        current, current_error = get_usb_storage_blocked()
        return current, str(e) or current_error


def get_portable_devices_blocked():
    if platform.system() != "Windows":
        return None, "solo_windows"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DENY_DEVICE_CLASSES_PATH, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, index)
                    if str(value).upper() == WPD_CLASS_GUID:
                        return True, ""
                    index += 1
                except OSError:
                    break
        return False, ""
    except FileNotFoundError:
        return False, ""
    except Exception as e:
        return None, str(e)


def set_portable_devices_blocked(blocked):
    if platform.system() != "Windows":
        return None, "solo_windows"
    if not is_windows_admin():
        current, current_error = get_portable_devices_blocked()
        return current, current_error or "requiere_permisos_administrador"
    try:
        if blocked:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, DEVICE_INSTALL_RESTRICTIONS_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "DenyDeviceClasses", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DenyDeviceClassesRetroactive", 0, winreg.REG_DWORD, 1)
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, DENY_DEVICE_CLASSES_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "1", 0, winreg.REG_SZ, WPD_CLASS_GUID)
            return True, ""

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DENY_DEVICE_CLASSES_PATH, 0, winreg.KEY_ALL_ACCESS) as key:
            names_to_delete = []
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                    if str(value).upper() == WPD_CLASS_GUID:
                        names_to_delete.append(name)
                    index += 1
                except OSError:
                    break
            for name in names_to_delete:
                winreg.DeleteValue(key, name)
        return False, ""
    except FileNotFoundError:
        return False, ""
    except Exception as e:
        current, current_error = get_portable_devices_blocked()
        return current, str(e) or current_error


def set_current_wpd_devices_enabled(enabled):
    if platform.system() != "Windows":
        return None, "solo_windows"
    if not is_windows_admin():
        return None, "requiere_permisos_administrador"
    try:
        action = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"
        cmd = (
            "$devices = Get-PnpDevice -Class WPD -ErrorAction SilentlyContinue; "
            "foreach ($device in $devices) { "
            f"  {action} -InstanceId $device.InstanceId -Confirm:$false -ErrorAction SilentlyContinue "
            "}; "
            "$devices.Count"
        )
        output = _powershell(cmd)
        count = int(output) if str(output).strip().isdigit() else 0
        return count, ""
    except Exception as e:
        return None, str(e)


def count_usb_storage_devices():
    if platform.system() != "Windows":
        return None
    try:
        cmd = (
            "Get-CimInstance Win32_DiskDrive | "
            "Where-Object { $_.InterfaceType -eq 'USB' } | "
            "Measure-Object | Select-Object -ExpandProperty Count"
        )
        output = _powershell(cmd)
        return int(output.strip() or "0")
    except Exception:
        return None


def get_usb_policy(device_id):
    try:
        resp = requests.get(
            f"{SERVER_URL}/api/equipos/{device_id}/usb-policy",
            timeout=8,
            headers=HEADERS
        )
        if not resp.ok:
            return None
        data = resp.json()
        return data.get("block_usb_storage")
    except Exception as e:
        log(f"No se pudo consultar politica USB: {e}")
        return None


def aplicar_usb_policy(device_id):
    policy = get_usb_policy(device_id)
    errors = []
    previous_blocked, previous_error = get_usb_storage_blocked()
    previous_portable_blocked, previous_portable_error = get_portable_devices_blocked()
    previous_real_blocked = bool(previous_blocked) or bool(previous_portable_blocked)
    if policy is not None:
        if previous_error:
            errors.append(f"storage_previo:{previous_error}")
        if previous_portable_error:
            errors.append(f"portable_previo:{previous_portable_error}")
        if previous_real_blocked != bool(policy):
            errors.append(f"estado_manual_previo:{'bloqueado' if previous_real_blocked else 'habilitado'};politica:{'bloquear' if bool(policy) else 'habilitar'}")
        blocked, error = set_usb_storage_blocked(bool(policy))
        portable_blocked, portable_error = set_portable_devices_blocked(bool(policy))
        wpd_count, wpd_error = set_current_wpd_devices_enabled(not bool(policy))
    else:
        blocked, error = get_usb_storage_blocked()
        portable_blocked, portable_error = get_portable_devices_blocked()
        wpd_count, wpd_error = None, ""
    if error:
        errors.append(f"storage:{error}")
    if portable_error:
        errors.append(f"portable:{portable_error}")
    if wpd_error:
        errors.append(f"wpd_actual:{wpd_error}")
    devices = count_usb_storage_devices()
    return {
        "usb_storage_blocked": bool(blocked) or bool(portable_blocked),
        "usb_storage_devices": devices,
        "usb_block_error": "; ".join(errors)
    }


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


def send_ping(device_id, serial_number, serial_source, ip, ssid, dentro, wifi_networks, usb_status):
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
        "wifi_networks": wifi_networks,
        "usb_storage_blocked": usb_status.get("usb_storage_blocked"),
        "usb_storage_devices": usb_status.get("usb_storage_devices"),
        "usb_block_error": usb_status.get("usb_block_error", "")
    }
    try:
        resp = requests.post(
            f"{SERVER_URL}/api/ping",
            json=payload,
            timeout=10,
            headers=HEADERS
        )
        estado = "DENTRO  OK" if dentro else "FUERA   !!"
        usb_txt = f"USB blocked={usb_status.get('usb_storage_blocked')} devices={usb_status.get('usb_storage_devices')}"
        if usb_status.get("usb_block_error"):
            usb_txt += f" error={usb_status.get('usb_block_error')}"
        log(f"{estado} | IP: {ip} | SSID: {ssid or 'N/A'} | Redes: {len(wifi_networks)} | {usb_txt} | HTTP {resp.status_code}")
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
            usb_status    = aplicar_usb_policy(device_id)

            log(f"Redes encontradas: {len(wifi_networks)}")
            send_ping(device_id, serial_number, serial_source, ip, ssid, dentro, wifi_networks, usb_status)
        except Exception as e:
            log(f"Error ciclo principal: {e}")
            log(traceback.format_exc())
        time.sleep(PING_INTERVAL)


if __name__ == "__main__":
    main()