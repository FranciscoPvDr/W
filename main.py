# -*- coding: utf-8 -*-
"""
backend/main.py - Servidor FastAPI
Recibe pings del sensor, guarda en SQLite, geolocalizacion via Google API
y expone endpoints para el dashboard y app Android.

Instalar:
    pip install fastapi uvicorn sqlalchemy python-jose passlib[bcrypt] python-multipart httpx

Ejecutar:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Usuario admin por defecto:
    usuario: admin
    password: admin123
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Float, Integer, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta
import time
from typing import Optional, List
from jose import JWTError, jwt
from passlib.context import CryptContext
import httpx
import uuid
import os

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None

# ── Configuracion ─────────────────────────────────────────────────────────
SECRET_KEY         = "cambia-esta-clave-secreta-en-produccion-2024"
ALGORITHM          = "HS256"
TOKEN_EXPIRE_HOURS = 8
GOOGLE_API_KEY     = os.getenv("GOOGLE_API_KEY", "")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
OFFICE_LAT = float(os.getenv("OFFICE_LAT", "0") or 0)
OFFICE_LNG = float(os.getenv("OFFICE_LNG", "0") or 0)
MAX_GEO_ACCURACY_M = float(os.getenv("MAX_GEO_ACCURACY_M", "250") or 250)
OFFLINE_ALERT_MINUTES = int(os.getenv("OFFLINE_ALERT_MINUTES", "10") or 10)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

app = FastAPI(title="Monitor de Equipos")


FIRESTORE_CACHE_TTL_SECONDS = 120
_firestore_cache = {}


def _cache_get(key: str):
    item = _firestore_cache.get(key)
    if not item:
        return None
    if time.time() - item["time"] > FIRESTORE_CACHE_TTL_SECONDS:
        _firestore_cache.pop(key, None)
        return None
    return item["value"]


def _cache_set(key: str, value):
    _firestore_cache[key] = {"time": time.time(), "value": value}


def _cache_clear(*keys: str):
    for key in keys:
        _firestore_cache.pop(key, None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Base de datos ──────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./equipos.db")
# Render usa postgres:// pero SQLAlchemy necesita postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://"):
    engine = create_engine(DATABASE_URL)
else:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Base = declarative_base()
Session = sessionmaker(bind=engine)

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")


def _resolver_ruta_credenciales():
    env_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path
    # Fallback: primer JSON de service account en el directorio actual
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for name in os.listdir(base_dir):
        if name.endswith(".json") and "firebase-adminsdk" in name.lower():
            candidate = os.path.join(base_dir, name)
            if os.path.exists(candidate):
                return candidate
    return ""


def _init_firebase():
    """Inicializa Firebase Admin si hay credenciales configuradas."""
    if firebase_admin is None:
        print("Firebase Admin no instalado. Instala: pip install firebase-admin")
        return None

    # Opcion 1: credenciales como JSON en variable de entorno (para Render/cloud)
    creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        try:
            import tempfile, json as _json
            creds_dict = _json.loads(creds_json)
            if not firebase_admin._apps:
                cred = credentials.Certificate(creds_dict)
                firebase_admin.initialize_app(cred)
            print("Firebase Admin inicializado desde variable de entorno.")
            return firestore.client()
        except Exception as e:
            print(f"Error inicializando Firebase desde env var: {e}")
            return None

    # Opcion 2: ruta a archivo JSON
    creds_path = _resolver_ruta_credenciales()
    if not creds_path:
        print("FIREBASE_CREDENTIALS_PATH no definido (y sin JSON local). Sync Firestore desactivado.")
        return None
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(creds_path)
            firebase_admin.initialize_app(cred)
        print(f"Firebase Admin inicializado con: {creds_path}")
        return firestore.client()
    except Exception as e:
        print(f"No se pudo inicializar Firebase Admin: {e}")
        return None


firebase_db = _init_firebase()


def _get_firebase_db():
    global firebase_db
    if firebase_db is None:
        firebase_db = _init_firebase()
    return firebase_db


class PingLog(Base):
    __tablename__ = "ping_logs"
    id        = Column(String, primary_key=True)
    device_id = Column(String, index=True)
    serial_number = Column(String, index=True, nullable=True)
    hostname  = Column(String)
    ip        = Column(String)
    ssid      = Column(String)
    dentro    = Column(Boolean)
    sistema   = Column(String)
    timestamp = Column(DateTime)
    lat       = Column(Float, nullable=True)
    lng       = Column(Float, nullable=True)
    accuracy  = Column(Float, nullable=True)
    usb_storage_blocked = Column(Boolean, nullable=True)
    usb_storage_devices = Column(Integer, nullable=True)
    usb_block_error = Column(String, nullable=True)


class Equipo(Base):
    __tablename__ = "equipos"
    device_id   = Column(String, primary_key=True)
    serial_number = Column(String, index=True, nullable=True)
    hostname    = Column(String)
    ip          = Column(String)
    ssid        = Column(String)
    dentro      = Column(Boolean, default=True)
    sistema     = Column(String)
    ultimo_ping = Column(DateTime)
    primer_ping = Column(DateTime)
    lat         = Column(Float, nullable=True)
    lng         = Column(Float, nullable=True)
    accuracy    = Column(Float, nullable=True)
    usb_storage_blocked = Column(Boolean, nullable=True)
    usb_storage_policy = Column(Boolean, nullable=True)
    usb_storage_devices = Column(Integer, nullable=True)
    usb_block_error = Column(String, nullable=True)
    usb_updated_at = Column(DateTime, nullable=True)


class Usuario(Base):
    __tablename__ = "usuarios"
    id       = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, index=True)
    password = Column(String)
    nombre   = Column(String)
    role     = Column(String, default="ingeniero")
    activo   = Column(Boolean, default=True)


Base.metadata.create_all(engine)


def _ensure_sqlite_columns():
    """
    Asegura columnas nuevas en SQLite sin requerir migrador externo.
    """
    with engine.connect() as conn:
        equipos_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(equipos)"))}
        if "serial_number" not in equipos_cols:
            conn.execute(text("ALTER TABLE equipos ADD COLUMN serial_number TEXT"))
        if "usb_storage_blocked" not in equipos_cols:
            conn.execute(text("ALTER TABLE equipos ADD COLUMN usb_storage_blocked BOOLEAN"))
        if "usb_storage_policy" not in equipos_cols:
            conn.execute(text("ALTER TABLE equipos ADD COLUMN usb_storage_policy BOOLEAN"))
        if "usb_storage_devices" not in equipos_cols:
            conn.execute(text("ALTER TABLE equipos ADD COLUMN usb_storage_devices INTEGER"))
        if "usb_block_error" not in equipos_cols:
            conn.execute(text("ALTER TABLE equipos ADD COLUMN usb_block_error TEXT"))
        if "usb_updated_at" not in equipos_cols:
            conn.execute(text("ALTER TABLE equipos ADD COLUMN usb_updated_at DATETIME"))

        ping_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(ping_logs)"))}
        if "serial_number" not in ping_cols:
            conn.execute(text("ALTER TABLE ping_logs ADD COLUMN serial_number TEXT"))
        if "usb_storage_blocked" not in ping_cols:
            conn.execute(text("ALTER TABLE ping_logs ADD COLUMN usb_storage_blocked BOOLEAN"))
        if "usb_storage_devices" not in ping_cols:
            conn.execute(text("ALTER TABLE ping_logs ADD COLUMN usb_storage_devices INTEGER"))
        if "usb_block_error" not in ping_cols:
            conn.execute(text("ALTER TABLE ping_logs ADD COLUMN usb_block_error TEXT"))
        usuarios_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usuarios)"))}
        if "role" not in usuarios_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN role TEXT DEFAULT 'ingeniero'"))
        conn.execute(text("UPDATE usuarios SET role = 'super_admin' WHERE username = 'admin' AND (role IS NULL OR role = '')"))
        conn.commit()


_ensure_sqlite_columns()


# ── Crear admin por defecto ────────────────────────────────────────────────
def crear_admin():
    db = Session()
    try:
        existe = db.query(Usuario).filter_by(username="admin").first()
        if not existe:
            admin = Usuario(
                id       = str(uuid.uuid4()),
                username = "admin",
                password = pwd_context.hash("admin123"),
                nombre   = "Administrador",
                role     = "super_admin",
                activo   = True
            )
            db.add(admin)
            db.commit()
            print("Usuario admin creado - user: admin / pass: admin123")
    finally:
        db.close()

crear_admin()


# ── Modelos Pydantic ───────────────────────────────────────────────────────
class WifiNetwork(BaseModel):
    macAddress:     str
    signalStrength: int


class PingRequest(BaseModel):
    device_id:     str
    serial_number: Optional[str] = ""
    hostname:      str
    ip:            str
    ssid:          Optional[str] = ""
    dentro:        bool
    sistema:       Optional[str] = ""
    timestamp:     str
    wifi_networks: Optional[List[WifiNetwork]] = []
    usb_storage_blocked: Optional[bool] = None
    usb_storage_devices: Optional[int] = None
    usb_block_error: Optional[str] = ""


class UsbPolicyUpdate(BaseModel):
    block_usb_storage: bool


class UsuarioCreate(BaseModel):
    username: str
    password: str
    nombre:   str
    role: Optional[str] = "ingeniero"


class UsuarioRoleUpdate(BaseModel):
    role: str


class AssetCreate(BaseModel):
    numInventario: Optional[str] = ""
    serie: Optional[str] = ""
    tipo: Optional[str] = "Laptop"
    marca: Optional[str] = ""
    modelo: Optional[str] = ""
    asignado: Optional[str] = ""
    departamento: Optional[str] = ""
    puesto: Optional[str] = ""
    subtipo: Optional[str] = ""
    notas: Optional[str] = ""
    fechaCompra: Optional[str] = ""


class AssetAsignacion(BaseModel):
    asignado: Optional[str] = ""
    departamento: Optional[str] = ""
    puesto: Optional[str] = ""


class EmpleadoCreate(BaseModel):
    numEmpleado: Optional[str] = ""
    nombre: str
    apellidoPaterno: Optional[str] = ""
    apellidoMaterno: Optional[str] = ""
    correo: Optional[str] = ""
    departamento: Optional[str] = ""
    puesto: Optional[str] = ""
    telefono: Optional[str] = ""
    fechaIngreso: Optional[str] = ""
    activo: Optional[bool] = True


class EmpleadosBulk(BaseModel):
    empleados: List[EmpleadoCreate]


class SolicitudCasaCreate(BaseModel):
    equipoId: str
    equipoNombre: Optional[str] = ""
    numInventario: Optional[str] = ""
    solicitanteEmail: Optional[str] = ""
    solicitanteNombre: Optional[str] = ""
    dias: Optional[int] = 0
    motivo: Optional[str] = ""
    notas: Optional[str] = ""
    fechaRetornoTexto: Optional[str] = ""


class MovimientoCreate(BaseModel):
    equipoId: str
    equipoNombre: Optional[str] = ""
    tipo: str
    persona: Optional[str] = ""
    departamento: Optional[str] = ""
    puesto: Optional[str] = ""
    motivo: Optional[str] = ""
    notas: Optional[str] = ""
    registradoPor: Optional[str] = ""
    enCasa: Optional[bool] = False


class SolicitudCasaDecision(BaseModel):
    autorizadoPor: Optional[str] = ""
    autorizada: bool


class SolicitudCasaSalida(BaseModel):
    guardia: Optional[str] = ""


class CambiarPassword(BaseModel):
    password_actual: str
    password_nueva:  str


def inferir_ubicacion(dentro: bool, ssid: Optional[str]) -> str:
    """Construye una ubicación legible para app móvil."""
    if dentro:
        return f"Oficina ({ssid})" if ssid else "Oficina"
    return f"Fuera de oficina ({ssid})" if ssid else "Fuera de oficina"


def _normalizar_serie(value: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _buscar_equipo_por_serie_normalizada(db, serie: str):
    serie_norm = _normalizar_serie(serie)
    if not serie_norm:
        return None
    equipos = db.query(Equipo).filter(Equipo.serial_number.isnot(None)).order_by(Equipo.ultimo_ping.desc()).all()
    for equipo in equipos:
        if _normalizar_serie(equipo.serial_number) == serie_norm:
            return equipo
    return None


def _buscar_asset_supabase_por_serie(serie: str):
    serie_norm = _normalizar_serie(serie)
    if not serie_norm:
        return None
    try:
        rows = _supabase_request("GET", "equipos", params={"select": "*"}) or []
    except Exception as e:
        print(f"No se pudo buscar asset por serie en Supabase: {e}")
        return None
    for row in rows:
        if _normalizar_serie(row.get("serie")) == serie_norm:
            return row
    return None


def _resolver_doc_firestore(equipo: Equipo, serial_number: Optional[str] = ""):
    """
    Busca documento por serie de inventario; si no existe, usa device_id.
    """
    firebase_client = _get_firebase_db()
    if not firebase_client:
        return equipo.device_id
    try:
        serie = (serial_number or "").strip()
        if serie:
            docs = (
                firebase_client.collection("equipos")
                .where("serie", "==", serie)
                .limit(1)
                .stream()
            )
            for doc in docs:
                return doc.id
    except Exception as e:
        print(f"No se pudo buscar por serie en Firestore: {e}")
    return equipo.device_id


def _buscar_doc_firestore_por_serie(serie: str):
    """Devuelve (doc_id, data) del primer match por serie, o (None, None)."""
    firebase_client = _get_firebase_db()
    if not firebase_client or not serie:
        return None, None
    try:
        docs = (
            firebase_client.collection("equipos")
            .where("serie", "==", serie)
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc.id, doc.to_dict()
    except Exception as e:
        print(f"Error buscando serie en Firestore: {e}")
    return None, None


def sync_equipo_to_firestore(equipo: Equipo, serial_number: Optional[str] = ""):
    """Sincroniza un equipo de SQLite hacia Firestore colección `equipos`."""
    firebase_client = _get_firebase_db()
    if not firebase_client:
        return

    ultimo_ping_iso = equipo.ultimo_ping.isoformat() if equipo.ultimo_ping else None
    primer_ping_iso = equipo.primer_ping.isoformat() if equipo.primer_ping else None
    ubicacion = inferir_ubicacion(equipo.dentro, equipo.ssid)
    doc_id = _resolver_doc_firestore(equipo, serial_number)
    serie = (serial_number or equipo.serial_number or "").strip()

    payload = {
        "deviceId": equipo.device_id,
        "hostname": equipo.hostname or "",
        "ip": equipo.ip or "",
        "ssid": equipo.ssid or "",
        "dentro": bool(equipo.dentro),
        "sistema": equipo.sistema or "",
        "ubicacion": ubicacion,
        "geo": {
            "lat": equipo.lat,
            "lng": equipo.lng,
            "accuracy": equipo.accuracy,
        },
        "lat": equipo.lat,
        "lng": equipo.lng,
        "accuracy": equipo.accuracy,
        "ultimoPing": ultimo_ping_iso,
        "primerPing": primer_ping_iso,
        "actualizadoEn": datetime.utcnow().isoformat(),
        "usbStorageBlocked": equipo.usb_storage_blocked,
        "usbStoragePolicy": equipo.usb_storage_policy,
        "usbStorageDevices": equipo.usb_storage_devices,
        "usbBlockError": equipo.usb_block_error or "",
        "usbUpdatedAt": equipo.usb_updated_at.isoformat() if equipo.usb_updated_at else None,
    }
    if serie:
        payload["serie"] = serie

    try:
        firebase_client.collection("equipos").document(doc_id).set(payload, merge=True)
    except Exception as e:
        print(f"Error sincronizando equipo {equipo.device_id} a Firestore: {e}")


def _obtener_asignacion_firestore(equipo: Equipo):
    """
    Obtiene datos de asignación desde Firestore, priorizando match por serie.
    """
    serie = (equipo.serial_number or "").strip()
    asset = _buscar_asset_supabase_por_serie(serie)
    if asset:
        return {
            "asignado": asset.get("asignado", "") or "",
            "departamento": asset.get("departamento", "") or "",
            "puesto": asset.get("puesto", "") or "",
            "numInventario": asset.get("num_inventario", "") or "",
            "inventariado": True,
        }

    firebase_client = _get_firebase_db()
    if not firebase_client:
        return {}

    doc_data = None
    if serie:
        _, serie_doc_data = _buscar_doc_firestore_por_serie(serie)
        doc_data = serie_doc_data

    if not doc_data:
        try:
            fallback = firebase_client.collection("equipos").document(equipo.device_id).get()
            if fallback.exists:
                doc_data = fallback.to_dict()
        except Exception:
            doc_data = None

    if not doc_data:
        return {}

    return {
        "asignado": doc_data.get("asignado", "") or "",
        "departamento": doc_data.get("departamento", "") or "",
        "puesto": doc_data.get("puesto", "") or "",
        "numInventario": doc_data.get("numInventario", "") or "",
        "inventariado": bool(doc_data.get("numInventario") or doc_data.get("asignado") or doc_data.get("tipo")),
    }


def _parse_iso_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _equipo_firestore_a_resultado(doc_id: str, data: dict, limite_offline: datetime):
    ultimo_ping_dt = _parse_iso_datetime(data.get("ultimoPing") or data.get("ultimo_ping") or data.get("actualizadoEn"))
    online = ultimo_ping_dt >= limite_offline if ultimo_ping_dt else False
    geo = data.get("geo") if isinstance(data.get("geo"), dict) else {}
    return {
        "device_id": data.get("deviceId") or data.get("device_id") or doc_id,
        "serial_number": data.get("serie") or data.get("serial_number") or "",
        "numInventario": _safe_firestore_text(data.get("numInventario")),
        "asignado": _safe_firestore_text(data.get("asignado")),
        "departamento": _safe_firestore_text(data.get("departamento")),
        "puesto": _safe_firestore_text(data.get("puesto")),
        "inventariado": bool(data.get("inventariado") or data.get("numInventario") or data.get("asignado") or data.get("tipo")),
        "hostname": data.get("hostname", "") or "",
        "ip": data.get("ip", "") or "",
        "ssid": data.get("ssid", "") or "",
        "dentro": bool(data.get("dentro", False)),
        "online": online,
        "sistema": data.get("sistema", "") or "",
        "ultimo_ping": ultimo_ping_dt.isoformat() if ultimo_ping_dt else None,
        "lat": data.get("lat", geo.get("lat")),
        "lng": data.get("lng", geo.get("lng")),
        "accuracy": data.get("accuracy", geo.get("accuracy")),
        "usb_storage_blocked": data.get("usbStorageBlocked"),
        "usb_storage_policy": data.get("usbStoragePolicy"),
        "usb_storage_devices": data.get("usbStorageDevices"),
        "usb_block_error": data.get("usbBlockError", "") or "",
        "usb_updated_at": data.get("usbUpdatedAt"),
    }


def _eliminar_doc_firestore_equipo(equipo: Equipo):
    firebase_client = _get_firebase_db()
    if not firebase_client:
        return
    try:
        serie = (equipo.serial_number or "").strip()
        if serie:
            docs = firebase_client.collection("equipos").where("serie", "==", serie).stream()
            for doc in docs:
                doc.reference.delete()
        firebase_client.collection("equipos").document(equipo.device_id).delete()
    except Exception as e:
        print(f"No se pudo eliminar equipo de Firestore: {e}")


def _nombre_completo_empleado(data: dict) -> str:
    nombre_completo = (data.get("nombreCompleto") or "").strip()
    if nombre_completo:
        return nombre_completo
    partes = [
        data.get("nombre", ""),
        data.get("apellidoPaterno", ""),
        data.get("apellidoMaterno", ""),
    ]
    return " ".join(str(p).strip() for p in partes if str(p).strip())


def _payload_empleado(data: EmpleadoCreate) -> dict:
    nombre_completo = " ".join(
        p.strip() for p in [data.nombre, data.apellidoPaterno or "", data.apellidoMaterno or ""] if p.strip()
    )
    return {
        "activo": bool(data.activo),
        "apellidoMaterno": (data.apellidoMaterno or "").strip(),
        "apellidoPaterno": (data.apellidoPaterno or "").strip(),
        "correo": (data.correo or "").strip(),
        "departamento": (data.departamento or "").strip(),
        "fechaIngreso": (data.fechaIngreso or "").strip(),
        "nombre": data.nombre.strip(),
        "nombreCompleto": nombre_completo,
        "numEmpleado": (data.numEmpleado or "").strip(),
        "puesto": (data.puesto or "").strip(),
        "telefono": (data.telefono or "").strip(),
    }


def _safe_firestore_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _supabase_headers(prefer: str = "return=representation"):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=400, detail="Supabase no configurado")
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _supabase_url(table: str) -> str:
    if not SUPABASE_URL:
        raise HTTPException(status_code=400, detail="Supabase no configurado")
    return f"{SUPABASE_URL}/rest/v1/{table}"


def _supabase_request(method: str, table: str, params=None, json=None, prefer: str = "return=representation"):
    try:
        with httpx.Client(timeout=30) as client:
            res = client.request(method, _supabase_url(table), headers=_supabase_headers(prefer), params=params, json=json)
        if res.status_code >= 400:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        if not res.text:
            return None
        return res.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error conectando a Supabase: {e}")


def _asset_row_to_api(row: dict) -> dict:
    return {
        "id": _safe_firestore_text(row.get("id")),
        "numInventario": _safe_firestore_text(row.get("num_inventario")),
        "serie": _safe_firestore_text(row.get("serie")),
        "tipo": _safe_firestore_text(row.get("tipo")),
        "subtipo": _safe_firestore_text(row.get("subtipo")),
        "marca": _safe_firestore_text(row.get("marca")),
        "modelo": _safe_firestore_text(row.get("modelo")),
        "asignado": _safe_firestore_text(row.get("asignado")),
        "departamento": _safe_firestore_text(row.get("departamento")),
        "puesto": _safe_firestore_text(row.get("puesto")),
        "ubicacion": _safe_firestore_text(row.get("ubicacion")),
        "hostname": _safe_firestore_text(row.get("hostname")),
        "ip": _safe_firestore_text(row.get("ip")),
        "ssid": _safe_firestore_text(row.get("ssid")),
        "deviceId": _safe_firestore_text(row.get("device_id")),
        "lat": row.get("lat"),
        "lng": row.get("lng"),
        "accuracy": row.get("accuracy"),
        "estado": _safe_firestore_text(row.get("estado")),
        "online": bool(row.get("online", False)),
        "dentro": row.get("dentro", True),
        "enCasa": bool(row.get("en_casa", False)),
        "notas": _safe_firestore_text(row.get("notas")),
        "fechaCompra": _safe_firestore_text(row.get("fecha_compra")),
    }


def _empleado_row_to_api(row: dict) -> dict:
    return {
        "id": _safe_firestore_text(row.get("num_empleado") or row.get("id")),
        "numEmpleado": _safe_firestore_text(row.get("num_empleado")),
        "nombre": _safe_firestore_text(row.get("nombre")),
        "apellidoPaterno": _safe_firestore_text(row.get("apellido_paterno")),
        "apellidoMaterno": _safe_firestore_text(row.get("apellido_materno")),
        "nombreCompleto": _safe_firestore_text(row.get("nombre_completo")),
        "correo": _safe_firestore_text(row.get("correo")),
        "departamento": _safe_firestore_text(row.get("departamento")),
        "puesto": _safe_firestore_text(row.get("puesto")),
        "telefono": _safe_firestore_text(row.get("telefono")),
        "fechaIngreso": _safe_firestore_text(row.get("fecha_ingreso")),
        "activo": row.get("activo", True),
    }


def _movimiento_row_to_api(row: dict) -> dict:
    return {
        "id": _safe_firestore_text(row.get("id")),
        "equipoId": _safe_firestore_text(row.get("equipo_id")),
        "equipoNombre": _safe_firestore_text(row.get("equipo_nombre")),
        "tipo": _safe_firestore_text(row.get("tipo")),
        "motivo": _safe_firestore_text(row.get("motivo")),
        "persona": _safe_firestore_text(row.get("persona")),
        "puesto": _safe_firestore_text(row.get("puesto")),
        "departamento": _safe_firestore_text(row.get("departamento")),
        "notas": _safe_firestore_text(row.get("notas")),
        "registradoPor": _safe_firestore_text(row.get("registrado_por")),
        "fecha": _safe_firestore_text(row.get("fecha")),
    }


def _solicitud_row_to_api(row: dict) -> dict:
    return {
        "id": _safe_firestore_text(row.get("id")),
        "equipoId": _safe_firestore_text(row.get("equipo_id")),
        "equipoNombre": _safe_firestore_text(row.get("equipo_nombre")),
        "numInventario": _safe_firestore_text(row.get("num_inventario")),
        "solicitanteEmail": _safe_firestore_text(row.get("solicitante_email")),
        "solicitanteNombre": _safe_firestore_text(row.get("solicitante_nombre") or row.get("persona_salida")),
        "dias": row.get("dias") or 0,
        "fechaSolicitud": _safe_firestore_text(row.get("fecha_solicitud")),
        "fechaInicio": _safe_firestore_text(row.get("fecha_inicio")),
        "fechaFin": _safe_firestore_text(row.get("fecha_fin")),
        "motivo": _safe_firestore_text(row.get("motivo")),
        "estado": _safe_firestore_text(row.get("estado") or "pendiente"),
        "autorizadoPor": _safe_firestore_text(row.get("autorizado_por")),
        "salidaConfirmadaPor": _safe_firestore_text(row.get("salida_confirmada_por")),
        "notas": _safe_firestore_text(row.get("notas")),
    }


def _asset_payload_supabase(data: AssetCreate) -> dict:
    asset_id = (data.numInventario or data.serie or str(uuid.uuid4())).strip()
    return {
        "id": asset_id,
        "num_inventario": (data.numInventario or "").strip() or None,
        "serie": (data.serie or "").strip() or None,
        "tipo": (data.tipo or "Laptop").strip() or None,
        "subtipo": (data.subtipo or "").strip() or None,
        "marca": (data.marca or "").strip() or None,
        "modelo": (data.modelo or "").strip() or None,
        "asignado": (data.asignado or "").strip() or None,
        "departamento": (data.departamento or "").strip() or None,
        "puesto": (data.puesto or "").strip() or None,
        "notas": (data.notas or "").strip() or None,
        "fecha_compra": (data.fechaCompra or "").strip() or None,
        "actualizado_en": datetime.utcnow().isoformat(),
    }


def _empleado_payload_supabase(data: EmpleadoCreate) -> dict:
    payload = _payload_empleado(data)
    return {
        "num_empleado": payload["numEmpleado"] or str(uuid.uuid4()),
        "nombre": payload["nombre"],
        "apellido_paterno": payload["apellidoPaterno"] or None,
        "apellido_materno": payload["apellidoMaterno"] or None,
        "nombre_completo": payload["nombreCompleto"] or None,
        "correo": payload["correo"] or None,
        "telefono": payload["telefono"] or None,
        "departamento": payload["departamento"] or None,
        "puesto": payload["puesto"] or None,
        "fecha_ingreso": payload["fechaIngreso"] or None,
        "activo": payload["activo"],
    }


# ── Helpers JWT ────────────────────────────────────────────────────────────
def verificar_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def crear_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_usuario_actual(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Token invalido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")
    db = Session()
    try:
        usuario = db.query(Usuario).filter_by(username=username, activo=True).first()
        if not usuario:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return {"username": usuario.username, "nombre": usuario.nombre}
    finally:
        db.close()


def exigir_super_admin(usuario):
    if usuario["username"] != "admin":
        raise HTTPException(status_code=403, detail="Solo el super admin puede realizar esta acción")


# ── Geolocalización Google ─────────────────────────────────────────────────
async def geolocate(wifi_networks: list):
    """Llama a Google Geolocation API con lista de redes WiFi."""
    if not wifi_networks or not GOOGLE_API_KEY:
        return None, None, None
    try:
        payload = {
            "considerIp": False,
            "wifiAccessPoints": [
                {"macAddress": n.macAddress, "signalStrength": n.signalStrength}
                for n in wifi_networks
            ]
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://www.googleapis.com/geolocation/v1/geolocate?key={GOOGLE_API_KEY}",
                json=payload
            )
            if resp.status_code == 200:
                data = resp.json()
                lat      = data["location"]["lat"]
                lng      = data["location"]["lng"]
                accuracy = data.get("accuracy", 0)
                return lat, lng, accuracy
    except Exception as e:
        print(f"Error geolocalización: {e}")
    return None, None, None


# ── Endpoints de autenticacion ─────────────────────────────────────────────

@app.post("/api/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    db = Session()
    try:
        usuario = db.query(Usuario).filter_by(username=form.username, activo=True).first()
        if not usuario or not verificar_password(form.password, usuario.password):
            raise HTTPException(status_code=401, detail="Usuario o contrasena incorrectos")
        token = crear_token({"sub": usuario.username, "nombre": usuario.nombre, "role": usuario.role or "ingeniero"})
        return {"access_token": token, "token_type": "bearer", "nombre": usuario.nombre, "username": usuario.username, "role": usuario.role or "ingeniero"}
    finally:
        db.close()


@app.get("/api/me")
def me(usuario=Depends(get_usuario_actual)):
    return usuario


@app.post("/api/usuarios")
def crear_usuario(data: UsuarioCreate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    db = Session()
    try:
        existe = db.query(Usuario).filter_by(username=data.username).first()
        if existe:
            raise HTTPException(status_code=400, detail="El usuario ya existe")
        nuevo = Usuario(
            id       = str(uuid.uuid4()),
            username = data.username,
            password = pwd_context.hash(data.password),
            nombre   = data.nombre,
            role     = data.role if data.role in ["guardia", "ingeniero", "super_admin"] else "ingeniero",
            activo   = True
        )
        db.add(nuevo)
        db.commit()
        return {"ok": True, "mensaje": f"Usuario {data.username} creado"}
    finally:
        db.close()


@app.get("/api/usuarios")
def listar_usuarios(usuario=Depends(get_usuario_actual)):
    db = Session()
    try:
        usuarios = db.query(Usuario).filter_by(activo=True).all()
        return {"usuarios": [{"username": u.username, "nombre": u.nombre, "role": u.role or "ingeniero"} for u in usuarios]}
    finally:
        db.close()


@app.patch("/api/usuarios/{username}/role")
def cambiar_rol_usuario(username: str, data: UsuarioRoleUpdate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    if data.role not in ["guardia", "ingeniero", "super_admin"]:
        raise HTTPException(status_code=400, detail="Rol inválido")
    db = Session()
    try:
        u = db.query(Usuario).filter_by(username=username, activo=True).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        u.role = data.role
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.delete("/api/usuarios/{username}")
def eliminar_usuario(username: str, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    if username == "admin":
        raise HTTPException(status_code=400, detail="No se puede eliminar el admin")
    db = Session()
    try:
        u = db.query(Usuario).filter_by(username=username).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        u.activo = False
        db.commit()
        return {"ok": True, "mensaje": f"Usuario {username} eliminado"}
    finally:
        db.close()


@app.get("/api/assets")
def listar_assets(usuario=Depends(get_usuario_actual)):
    cached = _cache_get("assets")
    if cached is not None:
        return cached
    rows = _supabase_request("GET", "equipos", params={"select": "*"}) or []
    resultado = {"assets": [_asset_row_to_api(row) for row in rows], "omitidos": []}
    _cache_set("assets", resultado)
    return resultado


@app.post("/api/assets")
def crear_asset(data: AssetCreate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    payload = _asset_payload_supabase(data)
    rows = _supabase_request("POST", "equipos", params={"on_conflict": "id"}, json=payload, prefer="resolution=merge-duplicates,return=representation") or []
    _cache_clear("assets")
    return {"ok": True, "id": (rows[0].get("id") if rows else payload["id"])}


@app.patch("/api/assets/{asset_id}")
def editar_asset(asset_id: str, data: AssetCreate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    payload = _asset_payload_supabase(data)
    payload.pop("id", None)
    _supabase_request("PATCH", "equipos", params={"id": f"eq.{asset_id}"}, json=payload)
    _cache_clear("assets")
    return {"ok": True, "id": asset_id}


@app.delete("/api/assets/{asset_id}")
def eliminar_asset(asset_id: str, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    _supabase_request("DELETE", "equipos", params={"id": f"eq.{asset_id}"}, prefer="return=minimal")
    _cache_clear("assets")
    return {"ok": True}


@app.get("/api/empleados")
def listar_empleados(usuario=Depends(get_usuario_actual)):
    cached = _cache_get("empleados")
    if cached is not None:
        return cached
    rows = _supabase_request("GET", "empleados", params={"select": "*"}) or []
    resultado = {"empleados": [_empleado_row_to_api(row) for row in rows]}
    _cache_set("empleados", resultado)
    return resultado


@app.post("/api/empleados")
def crear_empleado(data: EmpleadoCreate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    payload = _empleado_payload_supabase(data)
    rows = _supabase_request("POST", "empleados", params={"on_conflict": "num_empleado"}, json=payload, prefer="resolution=merge-duplicates,return=representation") or []
    _cache_clear("empleados")
    return {"ok": True, "id": (rows[0].get("num_empleado") if rows else payload["num_empleado"])}


@app.patch("/api/empleados/{empleado_id}")
def editar_empleado(empleado_id: str, data: EmpleadoCreate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    payload = _empleado_payload_supabase(data)
    payload.pop("num_empleado", None)
    _supabase_request("PATCH", "empleados", params={"num_empleado": f"eq.{empleado_id}"}, json=payload)
    _cache_clear("empleados")
    return {"ok": True, "id": empleado_id}


@app.post("/api/empleados/bulk")
def guardar_empleados_bulk(data: EmpleadosBulk, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    filas = []
    errores = []
    for idx, empleado in enumerate(data.empleados, start=1):
        try:
            payload = _empleado_payload_supabase(empleado)
            if not payload["nombre"]:
                errores.append({"fila": idx, "error": "Nombre requerido"})
                continue
            filas.append(payload)
        except Exception as e:
            errores.append({"fila": idx, "error": str(e)})
    if filas:
        try:
            _supabase_request("POST", "empleados", params={"on_conflict": "num_empleado"}, json=filas, prefer="resolution=merge-duplicates,return=representation")
            _cache_clear("empleados")
        except Exception as e:
            errores.append({"fila": "bulk", "error": str(e)})
    return {"ok": len(errores) == 0, "guardados": len(filas) if len(errores) == 0 else 0, "errores": errores}


@app.patch("/api/assets/{asset_id}/asignacion")
def actualizar_asignacion_asset(asset_id: str, data: AssetAsignacion, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    payload = {
        "asignado": (data.asignado or "").strip() or None,
        "departamento": (data.departamento or "").strip() or None,
        "puesto": (data.puesto or "").strip() or None,
        "actualizado_en": datetime.utcnow().isoformat(),
    }
    _supabase_request("PATCH", "equipos", params={"id": f"eq.{asset_id}"}, json=payload)
    _cache_clear("assets")
    return {"ok": True, "id": asset_id}


@app.get("/api/mobile/assets")
def mobile_listar_assets():
    rows = _supabase_request("GET", "equipos", params={"select": "*"}) or []
    return {"assets": [_asset_row_to_api(row) for row in rows], "omitidos": []}


@app.get("/api/mobile/movimientos")
def mobile_listar_movimientos(equipo_id: Optional[str] = None):
    params = {"select": "*", "order": "fecha.desc.nullslast"}
    if equipo_id:
        params["equipo_id"] = f"eq.{equipo_id}"
    rows = _supabase_request("GET", "movimientos", params=params) or []
    return {"movimientos": [_movimiento_row_to_api(row) for row in rows]}


@app.post("/api/mobile/movimientos")
def mobile_crear_movimiento(data: MovimientoCreate):
    payload = {
        "id": str(uuid.uuid4()),
        "equipo_id": data.equipoId,
        "equipo_nombre": data.equipoNombre or "",
        "tipo": data.tipo,
        "motivo": data.motivo or "",
        "persona": data.persona or "",
        "puesto": data.puesto or "",
        "departamento": data.departamento or "",
        "notas": data.notas or "",
        "registrado_por": data.registradoPor or "",
        "fecha": datetime.utcnow().isoformat(),
    }
    rows = _supabase_request("POST", "movimientos", json=payload) or []
    nuevo_estado = "Disponible"
    asignado = ""
    departamento = ""
    puesto = ""
    if data.tipo == "salida":
        if data.motivo == "Enviar a reparación":
            nuevo_estado = "En reparación"
        elif data.motivo == "Préstamo externo":
            nuevo_estado = "En préstamo"
        else:
            nuevo_estado = "Asignado"
        asignado = data.persona or ""
        departamento = data.departamento or ""
        puesto = data.puesto or ""
    _supabase_request(
        "PATCH",
        "equipos",
        params={"id": f"eq.{data.equipoId}"},
        json={
            "estado": nuevo_estado,
            "asignado": asignado,
            "departamento": departamento,
            "puesto": puesto,
            "en_casa": bool(data.enCasa),
            "actualizado_en": datetime.utcnow().isoformat(),
        },
    )
    return {"ok": True, "id": rows[0].get("id") if rows else payload["id"]}


@app.get("/api/mobile/solicitudes-casa")
def mobile_listar_solicitudes_casa(estado: Optional[str] = None):
    params = {"select": "*", "order": "fecha_solicitud.desc.nullslast"}
    if estado:
        params["estado"] = f"eq.{estado}"
    rows = _supabase_request("GET", "solicitudes_casa", params=params) or []
    return {"solicitudes": [_solicitud_row_to_api(row) for row in rows]}


@app.post("/api/mobile/solicitudes-casa")
def mobile_crear_solicitud_casa(data: SolicitudCasaCreate):
    ahora = datetime.utcnow()
    payload = {
        "id": str(uuid.uuid4()),
        "equipo_id": data.equipoId,
        "equipo_nombre": data.equipoNombre or "",
        "num_inventario": data.numInventario or "",
        "persona_salida": data.solicitanteNombre or "",
        "solicitante_nombre": data.solicitanteNombre or "",
        "solicitante_email": data.solicitanteEmail or "",
        "dias": data.dias or 0,
        "motivo": data.motivo or "",
        "notas": data.notas or "",
        "estado": "pendiente",
        "fecha_retorno_texto": data.fechaRetornoTexto or "",
        "fecha_solicitud": ahora.isoformat(),
        "fecha_inicio": ahora.isoformat(),
        "fecha_fin": (ahora + timedelta(days=data.dias or 0)).isoformat(),
    }
    rows = _supabase_request("POST", "solicitudes_casa", json=payload) or []
    return {"ok": True, "id": rows[0].get("id") if rows else payload["id"]}


@app.patch("/api/mobile/solicitudes-casa/{solicitud_id}/decision")
def mobile_decidir_solicitud_casa(solicitud_id: str, data: SolicitudCasaDecision):
    payload = {
        "estado": "aprobada" if data.autorizada else "rechazada",
        "autorizado_por": data.autorizadoPor or "",
    }
    _supabase_request("PATCH", "solicitudes_casa", params={"id": f"eq.{solicitud_id}"}, json=payload)
    return {"ok": True, "id": solicitud_id}


@app.patch("/api/mobile/solicitudes-casa/{solicitud_id}/confirmar-salida")
def mobile_confirmar_salida_solicitud_casa(solicitud_id: str, data: SolicitudCasaSalida):
    rows = _supabase_request("GET", "solicitudes_casa", params={"select": "*", "id": f"eq.{solicitud_id}", "limit": "1"}) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    solicitud = rows[0]
    guardia = data.guardia or ""
    _supabase_request(
        "PATCH",
        "solicitudes_casa",
        params={"id": f"eq.{solicitud_id}"},
        json={
            "estado": "salida_confirmada",
            "salida_confirmada_por": guardia,
            "fecha_salida_confirmada": datetime.utcnow().isoformat(),
        },
    )
    equipo_id = solicitud.get("equipo_id")
    if equipo_id:
        _supabase_request(
            "PATCH",
            "equipos",
            params={"id": f"eq.{equipo_id}"},
            json={
                "estado": "Fuera del parque con permiso",
                "dentro": False,
                "en_casa": True,
                "asignado": solicitud.get("solicitante_nombre") or solicitud.get("persona_salida") or "",
                "actualizado_en": datetime.utcnow().isoformat(),
            },
        )
    return {"ok": True, "id": solicitud_id}


@app.post("/api/usuarios/cambiar-password")
def cambiar_password(data: CambiarPassword, usuario=Depends(get_usuario_actual)):
    db = Session()
    try:
        u = db.query(Usuario).filter_by(username=usuario["username"]).first()
        if not verificar_password(data.password_actual, u.password):
            raise HTTPException(status_code=400, detail="Contrasena actual incorrecta")
        u.password = pwd_context.hash(data.password_nueva)
        db.commit()
        return {"ok": True, "mensaje": "Contrasena actualizada"}
    finally:
        db.close()


# ── Endpoint del sensor (sin autenticacion) ────────────────────────────────

@app.post("/api/ping")
async def recibir_ping(data: PingRequest):
    db = Session()
    try:
        ahora = datetime.utcnow()

        # Geolocalizar con redes WiFi
        lat, lng, accuracy = await geolocate(data.wifi_networks)

        # Calibración opcional: si la precisión es mala y estás dentro de red empresa,
        # usa coordenada fija de oficina para evitar ubicaciones lejanas.
        if data.dentro and OFFICE_LAT and OFFICE_LNG:
            if lat is None or accuracy is None or accuracy > MAX_GEO_ACCURACY_M:
                lat = OFFICE_LAT
                lng = OFFICE_LNG
                accuracy = min(MAX_GEO_ACCURACY_M, 50.0)

        # Fallback: si Google no pudo geolocalizar, usar ultima geo conocida del equipo
        if lat is None:
            serial_limpio_geo = (data.serial_number or "").strip()
            equipo_geo = None
            if serial_limpio_geo:
                equipo_geo = _buscar_equipo_por_serie_normalizada(db, serial_limpio_geo)
            if not equipo_geo:
                equipo_geo = db.query(Equipo).filter_by(device_id=data.device_id).first()
            if equipo_geo and equipo_geo.lat:
                lat      = equipo_geo.lat
                lng      = equipo_geo.lng
                accuracy = equipo_geo.accuracy

        log = PingLog(
            id        = str(uuid.uuid4()),
            device_id = data.device_id,
            serial_number = (data.serial_number or "").strip(),
            hostname  = data.hostname,
            ip        = data.ip,
            ssid      = data.ssid or "",
            dentro    = data.dentro,
            sistema   = data.sistema or "",
            timestamp = ahora,
            lat       = lat,
            lng       = lng,
            accuracy  = accuracy,
            usb_storage_blocked = data.usb_storage_blocked,
            usb_storage_devices = data.usb_storage_devices,
            usb_block_error = data.usb_block_error or ""
        )
        db.add(log)

        # ── Buscar equipo existente ────────────────────────────────────────
        # Prioridad 1: buscar por serial_number (evita duplicados al reinstalar)
        # Prioridad 2: buscar por device_id (compatibilidad con registros viejos)
        serial_limpio = (data.serial_number or "").strip()
        equipo = None
        if serial_limpio:
            equipo = _buscar_equipo_por_serie_normalizada(db, serial_limpio)
            if equipo:
                # Actualizar device_id al nuevo en caso de reinstalación
                equipo.device_id = data.device_id

        if not equipo:
            equipo = db.query(Equipo).filter_by(device_id=data.device_id).first()

        if equipo:
            if serial_limpio:
                equipo.serial_number = serial_limpio
            equipo.hostname    = data.hostname
            equipo.ip          = data.ip
            equipo.ssid        = data.ssid or ""
            equipo.dentro      = data.dentro
            equipo.sistema     = data.sistema or ""
            equipo.ultimo_ping = ahora
            equipo.usb_storage_blocked = data.usb_storage_blocked
            if equipo.usb_storage_policy is None and data.usb_storage_blocked is not None:
                equipo.usb_storage_policy = data.usb_storage_blocked
            equipo.usb_storage_devices = data.usb_storage_devices
            equipo.usb_block_error = data.usb_block_error or ""
            equipo.usb_updated_at = ahora
            if lat:
                equipo.lat      = lat
                equipo.lng      = lng
                equipo.accuracy = accuracy
        else:
            equipo = Equipo(
                device_id   = data.device_id,
                serial_number = serial_limpio,
                hostname    = data.hostname,
                ip          = data.ip,
                ssid        = data.ssid or "",
                dentro      = data.dentro,
                sistema     = data.sistema or "",
                ultimo_ping = ahora,
                primer_ping = ahora,
                lat         = lat,
                lng         = lng,
                accuracy    = accuracy,
                usb_storage_blocked = data.usb_storage_blocked,
                usb_storage_policy = data.usb_storage_blocked,
                usb_storage_devices = data.usb_storage_devices,
                usb_block_error = data.usb_block_error or "",
                usb_updated_at = ahora
            )
            db.add(equipo)

        db.commit()
        sync_equipo_to_firestore(equipo, data.serial_number)
        geo = f"lat:{lat:.4f},lng:{lng:.4f},acc:{accuracy:.0f}m" if lat else "sin geo"
        print(f"Ping [{data.hostname}] {'DENTRO' if data.dentro else 'FUERA'} | {geo}")
        return {"ok": True, "mensaje": "Ping registrado", "geo": {"lat": lat, "lng": lng, "accuracy": accuracy}}
    finally:
        db.close()


@app.get("/api/equipos/{device_id}/usb-policy")
def obtener_usb_policy(device_id: str):
    db = Session()
    try:
        equipo = db.query(Equipo).filter_by(device_id=device_id).first()
        if not equipo:
            return {"block_usb_storage": None}
        return {"block_usb_storage": equipo.usb_storage_policy}
    finally:
        db.close()


# ── Endpoints protegidos ───────────────────────────────────────────────────

@app.get("/api/equipos")
def listar_equipos(usuario=Depends(get_usuario_actual)):
    db = Session()
    try:
        equipos = db.query(Equipo).order_by(Equipo.ultimo_ping.desc()).all()
        limite_offline = datetime.utcnow() - timedelta(minutes=2)
        resultado_por_clave = {}
        for e in equipos:
            online = e.ultimo_ping >= limite_offline if e.ultimo_ping else False
            asignacion = _obtener_asignacion_firestore(e)
            item = {
                "device_id":   e.device_id,
                "serial_number": e.serial_number,
                "numInventario": asignacion.get("numInventario", ""),
                "asignado": asignacion.get("asignado", ""),
                "departamento": asignacion.get("departamento", ""),
                "puesto": asignacion.get("puesto", ""),
                "inventariado": asignacion.get("inventariado", False),
                "hostname":    e.hostname,
                "ip":          e.ip,
                "ssid":        e.ssid,
                "dentro":      e.dentro,
                "online":      online,
                "sistema":     e.sistema,
                "ultimo_ping": e.ultimo_ping.isoformat() if e.ultimo_ping else None,
                "lat":         e.lat,
                "lng":         e.lng,
                "accuracy":    e.accuracy,
                "usb_storage_blocked": e.usb_storage_blocked,
                "usb_storage_policy": e.usb_storage_policy,
                "usb_storage_devices": e.usb_storage_devices,
                "usb_block_error": e.usb_block_error,
                "usb_updated_at": e.usb_updated_at.isoformat() if e.usb_updated_at else None,
            }
            clave = _normalizar_serie(e.serial_number) or (e.device_id or "").strip()
            if clave not in resultado_por_clave:
                resultado_por_clave[clave] = item

        firebase_client = _get_firebase_db()
        if firebase_client:
            try:
                for doc in firebase_client.collection("equipos").stream():
                    data = doc.to_dict() or {}
                    tipo = str(data.get("tipo", "") or "").strip().lower()
                    if tipo and tipo not in ["laptop", "desktop", "escritorio", "pc", "computadora"]:
                        continue
                    if not (data.get("deviceId") or data.get("device_id") or data.get("serie") or data.get("serial_number")):
                        continue
                    item = _equipo_firestore_a_resultado(doc.id, data, limite_offline)
                    clave = _normalizar_serie(item.get("serial_number")) or (item.get("device_id") or doc.id).strip()
                    if clave not in resultado_por_clave:
                        resultado_por_clave[clave] = item
            except Exception as e:
                print(f"No se pudieron cargar equipos desde Firestore: {e}")

        resultado = sorted(
            resultado_por_clave.values(),
            key=lambda x: x.get("ultimo_ping") or "",
            reverse=True
        )
        return {"equipos": resultado, "total": len(resultado)}
    finally:
        db.close()


@app.patch("/api/equipos/{device_id}/usb-policy")
def actualizar_usb_policy(device_id: str, data: UsbPolicyUpdate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    db = Session()
    try:
        equipo = db.query(Equipo).filter_by(device_id=device_id).first()
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        equipo.usb_storage_policy = data.block_usb_storage
        db.commit()
        sync_equipo_to_firestore(equipo, equipo.serial_number)
        accion = "bloquear" if data.block_usb_storage else "habilitar"
        return {"ok": True, "mensaje": f"Politica USB actualizada: {accion}", "block_usb_storage": data.block_usb_storage}
    finally:
        db.close()


@app.delete("/api/equipos/{device_id}")
def eliminar_equipo(device_id: str, usuario=Depends(get_usuario_actual)):
    """
    Elimina un equipo de la BD (y su historial de pings).
    Útil para limpiar duplicados o equipos dados de baja.
    """
    if usuario["username"] != "admin":
        raise HTTPException(status_code=403, detail="Solo el super admin puede eliminar equipos")

    db = Session()
    try:
        equipo = db.query(Equipo).filter_by(device_id=device_id).first()
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        # Borrar historial de pings asociado
        db.query(PingLog).filter_by(device_id=device_id).delete()
        _eliminar_doc_firestore_equipo(equipo)
        db.delete(equipo)
        db.commit()
        print(f"Equipo eliminado: {device_id} ({equipo.hostname})")
        return {"ok": True, "mensaje": f"Equipo {equipo.hostname} eliminado correctamente"}
    finally:
        db.close()


@app.get("/api/equipos/{device_id}/historial")
def historial_equipo(device_id: str, limite: int = 50, usuario=Depends(get_usuario_actual)):
    db = Session()
    try:
        logs = (
            db.query(PingLog)
            .filter_by(device_id=device_id)
            .order_by(PingLog.timestamp.desc())
            .limit(limite)
            .all()
        )
        resultado = [{
            "serial_number": l.serial_number,
            "dentro":    l.dentro,
            "ip":        l.ip,
            "ssid":      l.ssid,
            "timestamp": l.timestamp.isoformat(),
            "lat":       l.lat,
            "lng":       l.lng,
            "accuracy":  l.accuracy,
        } for l in logs]
        return {"device_id": device_id, "historial": resultado}
    finally:
        db.close()


@app.get("/api/alertas")
def equipos_fuera(usuario=Depends(get_usuario_actual)):
    db = Session()
    try:
        limite_offline = datetime.utcnow() - timedelta(minutes=2)
        limite_sin_senal = datetime.utcnow() - timedelta(minutes=OFFLINE_ALERT_MINUTES)
        equipos = db.query(Equipo).all()
        fuera = []
        sin_senal = []
        for e in equipos:
            online = e.ultimo_ping >= limite_offline if e.ultimo_ping else False
            if online and not e.dentro:
                fuera.append({
                    "device_id":   e.device_id,
                    "serial_number": e.serial_number,
                    "hostname":    e.hostname,
                    "ip":          e.ip,
                    "ultimo_ping": e.ultimo_ping.isoformat() if e.ultimo_ping else None,
                    "lat":         e.lat,
                    "lng":         e.lng,
                    "accuracy":    e.accuracy,
                })
            if (not online) and (e.ultimo_ping is None or e.ultimo_ping <= limite_sin_senal):
                sin_senal.append({
                    "device_id": e.device_id,
                    "serial_number": e.serial_number,
                    "hostname": e.hostname,
                    "ip": e.ip,
                    "ultimo_ping": e.ultimo_ping.isoformat() if e.ultimo_ping else None,
                    "minutos_sin_senal": OFFLINE_ALERT_MINUTES,
                })
        return {
            "fuera": fuera,
            "sin_senal": sin_senal,
            "count_fuera": len(fuera),
            "count_sin_senal": len(sin_senal),
            "count": len(fuera) + len(sin_senal),
        }
    finally:
        db.close()


@app.post("/api/firebase/sync-equipos")
def sync_todos_a_firebase(usuario=Depends(get_usuario_actual)):
    if not firebase_db:
        firebase_client = _get_firebase_db()
    else:
        firebase_client = firebase_db
    if not firebase_client:
        raise HTTPException(
            status_code=400,
            detail="Firebase no configurado. Define FIREBASE_CREDENTIALS_PATH y firebase-admin."
        )
    db = Session()
    try:
        equipos = db.query(Equipo).all()
        for equipo in equipos:
            sync_equipo_to_firestore(equipo)
        return {"ok": True, "mensaje": f"Sincronizados {len(equipos)} equipos a Firestore"}
    finally:
        db.close()


@app.get("/api/debug/match-serie")
def debug_match_serie(device_id: Optional[str] = None, usuario=Depends(get_usuario_actual)):
    """
    Diagnóstico de relación Sensor -> Serie -> Firebase.
    Si no se manda device_id, usa el equipo con ping más reciente.
    """
    firebase_client = _get_firebase_db()
    if not firebase_client:
        raise HTTPException(
            status_code=400,
            detail="Firebase no configurado. Define FIREBASE_CREDENTIALS_PATH y firebase-admin."
        )

    db = Session()
    try:
        if device_id:
            equipo = db.query(Equipo).filter_by(device_id=device_id).first()
        else:
            equipo = db.query(Equipo).order_by(Equipo.ultimo_ping.desc()).first()

        if not equipo:
            raise HTTPException(status_code=404, detail="No hay equipos en SQLite para diagnosticar")

        serie = (equipo.serial_number or "").strip()
        serie_doc_id, serie_doc_data = _buscar_doc_firestore_por_serie(serie)

        fallback_doc = firebase_client.collection("equipos").document(equipo.device_id).get()
        fallback_exists = bool(fallback_doc.exists)
        fallback_data = fallback_doc.to_dict() if fallback_exists else None

        estrategia = "serie" if serie_doc_id else "device_id"
        doc_destino = serie_doc_id or equipo.device_id
        motivo = (
            "Match por serie encontrado en Firestore"
            if serie_doc_id
            else "No hubo match por serie; se usa fallback por device_id"
        )

        return {
            "ok": True,
            "equipo_sqlite": {
                "device_id": equipo.device_id,
                "serial_number": equipo.serial_number,
                "hostname": equipo.hostname,
                "ultimo_ping": equipo.ultimo_ping.isoformat() if equipo.ultimo_ping else None,
            },
            "match_firestore": {
                "estrategia": estrategia,
                "doc_destino": doc_destino,
                "motivo": motivo,
                "por_serie": {
                    "serie_consultada": serie or None,
                    "doc_id": serie_doc_id,
                    "existe": bool(serie_doc_id),
                    "hostname": (serie_doc_data or {}).get("hostname") if serie_doc_data else None,
                    "serie": (serie_doc_data or {}).get("serie") if serie_doc_data else None,
                },
                "fallback_device_id": {
                    "doc_id": equipo.device_id,
                    "existe": fallback_exists,
                    "hostname": (fallback_data or {}).get("hostname") if fallback_data else None,
                    "serie": (fallback_data or {}).get("serie") if fallback_data else None,
                },
            },
        }
    finally:
        db.close()


@app.get("/api/debug/ultimos-pings")
def debug_ultimos_pings(limite: int = 20, usuario=Depends(get_usuario_actual)):
    """
    Devuelve los últimos pings para verificar si el sensor envía serial_number.
    """
    limite = max(1, min(limite, 200))
    db = Session()
    try:
        logs = (
            db.query(PingLog)
            .order_by(PingLog.timestamp.desc())
            .limit(limite)
            .all()
        )
        return {
            "ok": True,
            "total": len(logs),
            "pings": [
                {
                    "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                    "device_id": l.device_id,
                    "serial_number": l.serial_number,
                    "hostname": l.hostname,
                    "ip": l.ip,
                    "ssid": l.ssid,
                    "dentro": l.dentro,
                }
                for l in logs
            ],
        }
    finally:
        db.close()


# ── Dashboard y root ───────────────────────────────────────────────────────

@app.get("/dashboard")
def dashboard():
    return FileResponse("dashboard.html")


@app.get("/equipos")
def equipos_page():
    return FileResponse("equipos.html")


@app.get("/usuarios")
def usuarios_page():
    return FileResponse("usuarios.html")


@app.get("/empleados")
def empleados_page():
    return FileResponse("empleados.html")


@app.get("/login")
def login_page():
    return FileResponse("login.html")


@app.get("/guardia")
def guardia_page():
    return FileResponse("guardia.html")


@app.get("/reset-password")
def reset_password_page():
    return FileResponse("reset-password.html")


@app.get("/")
def root():
    return {"estado": "Monitor de equipos activo", "version": "3.0"}