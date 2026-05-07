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
from typing import Optional, List
from jose import JWTError, jwt
from passlib.context import CryptContext
import httpx
import uuid
import os
import secrets

try:
    import firebase_admin
    from firebase_admin import auth, credentials, firestore
except ImportError:
    firebase_admin = None
    auth = None
    credentials = None
    firestore = None

# ── Configuracion ─────────────────────────────────────────────────────────
SECRET_KEY         = "cambia-esta-clave-secreta-en-produccion-2024"
ALGORITHM          = "HS256"
TOKEN_EXPIRE_HOURS = 8
GOOGLE_API_KEY     = os.getenv("GOOGLE_API_KEY", "")
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", GOOGLE_API_KEY or "AIzaSyAcBzbTSYAfrumE3BSpkZpFrq9Ih1TEV1k").strip()
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
SUPER_ADMIN_EMAIL = "francisco.pavana@mundocharro.mx"
OFFICE_LAT = float(os.getenv("OFFICE_LAT", "0") or 0)
OFFICE_LNG = float(os.getenv("OFFICE_LNG", "0") or 0)
MAX_GEO_ACCURACY_M = float(os.getenv("MAX_GEO_ACCURACY_M", "250") or 250)
OFFLINE_ALERT_MINUTES = int(os.getenv("OFFLINE_ALERT_MINUTES", "10") or 10)

app = FastAPI(title="Monitor de Equipos")

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


def _normalizar_email_usuario(username: str, email: Optional[str] = ""):
    value = (email or "").strip()
    if value:
        return value
    user = (username or "").strip()
    if "@" in user:
        return user
    return f"{user}@mundocharro.local"


def sync_usuario_to_firebase(usuario, password: Optional[str] = None):
    firebase_client = _get_firebase_db()
    if not firebase_client or auth is None:
        return None
    email = _normalizar_email_usuario(usuario.username, usuario.email)
    uid = usuario.firebase_uid
    try:
        if uid:
            auth.update_user(uid, email=email, display_name=usuario.nombre or usuario.username, disabled=not usuario.activo)
        else:
            try:
                user_record = auth.get_user_by_email(email)
            except Exception:
                user_record = auth.create_user(
                    email=email,
                    password=password,
                    display_name=usuario.nombre or usuario.username,
                    disabled=not usuario.activo
                )
            uid = user_record.uid
            usuario.firebase_uid = uid
            usuario.email = email
        firebase_client.collection("usuarios").document(uid).set({
            "uid": uid,
            "username": usuario.username,
            "email": email,
            "nombre": usuario.nombre or usuario.username,
            "role": usuario.role or "ingeniero",
            "activo": bool(usuario.activo),
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        return uid
    except Exception as e:
        print(f"No se pudo sincronizar usuario {usuario.username} con Firebase: {e}")
        return None


def actualizar_password_usuario(usuario, password_nueva: str):
    usuario.password = pwd_context.hash(password_nueva)
    if usuario.firebase_uid and auth is not None:
        try:
            auth.update_user(usuario.firebase_uid, password=password_nueva)
        except Exception as e:
            print(f"No se pudo actualizar password Firebase para {usuario.username}: {e}")


def autenticar_firebase_email(email: str, password: str):
    if not FIREBASE_WEB_API_KEY:
        return None
    try:
        resp = httpx.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}",
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=10
        )
        if resp.status_code != 200:
            print(f"Firebase Auth login fallo para {email}: {resp.status_code} {resp.text[:300]}")
            return None
        data = resp.json()
        return {"uid": data.get("localId"), "email": data.get("email", email).lower()}
    except Exception as e:
        print(f"No se pudo autenticar contra Firebase Auth: {e}")
        return None


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
    email    = Column(String, nullable=True)
    firebase_uid = Column(String, nullable=True)
    reset_token = Column(String, nullable=True)
    reset_expires = Column(DateTime, nullable=True)
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
        if "email" not in usuarios_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN email TEXT"))
        if "firebase_uid" not in usuarios_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN firebase_uid TEXT"))
        if "reset_token" not in usuarios_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN reset_token TEXT"))
        if "reset_expires" not in usuarios_cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN reset_expires DATETIME"))
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

        super_admin = db.query(Usuario).filter_by(username=SUPER_ADMIN_EMAIL).first()
        if not super_admin:
            super_admin = Usuario(
                id       = str(uuid.uuid4()),
                username = SUPER_ADMIN_EMAIL,
                password = pwd_context.hash(os.getenv("SUPER_ADMIN_DEFAULT_PASSWORD", "admin123")),
                nombre   = "Francisco Pavana",
                email    = SUPER_ADMIN_EMAIL,
                role     = "super_admin",
                activo   = True
            )
            db.add(super_admin)
            db.commit()
            print(f"Super admin web creado - user: {SUPER_ADMIN_EMAIL}")
        else:
            super_admin.email = super_admin.email or SUPER_ADMIN_EMAIL
            super_admin.role = "super_admin"
            super_admin.activo = True
            db.commit()

        sync_usuario_to_firebase(super_admin)
        db.commit()
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


class UsuarioCreate(BaseModel):
    username: str
    password: str
    nombre:   str
    email:    Optional[str] = ""
    role:     Optional[str] = "ingeniero"


class UsuarioRoleUpdate(BaseModel):
    role: str


class UsbPolicyUpdate(BaseModel):
    block_usb_storage: bool


class EquipoRegistro(BaseModel):
    numInventario: Optional[str] = ""
    asignado: Optional[str] = ""
    departamento: Optional[str] = ""
    puesto: Optional[str] = ""


class CambiarPassword(BaseModel):
    password_actual: str
    password_nueva:  str


class ResetPasswordConfirm(BaseModel):
    token: str
    password_nueva: str


class DebugFirebaseLogin(BaseModel):
    email: str
    password: str


def inferir_ubicacion(dentro: bool, ssid: Optional[str]) -> str:
    """Construye una ubicación legible para app móvil."""
    if dentro:
        return f"Oficina ({ssid})" if ssid else "Oficina"
    return f"Fuera de oficina ({ssid})" if ssid else "Fuera de oficina"


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
    limite_offline = datetime.utcnow() - timedelta(minutes=2)
    online = equipo.ultimo_ping >= limite_offline if equipo.ultimo_ping else False
    sin_senal = not online
    ubicacion = inferir_ubicacion(equipo.dentro, equipo.ssid)
    doc_id = _resolver_doc_firestore(equipo, serial_number)
    serie = (serial_number or equipo.serial_number or "").strip()

    payload = {
        "deviceId": equipo.device_id,
        "hostname": equipo.hostname or "",
        "ip": equipo.ip or "",
        "ssid": equipo.ssid or "",
        "dentro": bool(equipo.dentro),
        "online": online,
        "sinSenal": sin_senal,
        "estadoConexion": "online" if online else "sin_senal",
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
        "usbStorageBlocked": equipo.usb_storage_blocked,
        "usbStoragePolicy": equipo.usb_storage_policy,
        "usbStorageDevices": equipo.usb_storage_devices,
        "usbBlockError": equipo.usb_block_error or "",
        "usbUpdatedAt": equipo.usb_updated_at.isoformat() if equipo.usb_updated_at else None,
        "ultimoPing": ultimo_ping_iso,
        "primerPing": primer_ping_iso,
        "actualizadoEn": datetime.utcnow().isoformat(),
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
    firebase_client = _get_firebase_db()
    if not firebase_client:
        return {}

    serie = (equipo.serial_number or "").strip()
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
    }


def _eliminar_doc_firestore_equipo(equipo: Equipo):
    firebase_client = _get_firebase_db()
    if not firebase_client:
        return
    candidatos = {equipo.device_id}
    serie = (equipo.serial_number or "").strip()
    if serie:
        doc_id, _ = _buscar_doc_firestore_por_serie(serie)
        if doc_id:
            candidatos.add(doc_id)
    for doc_id in candidatos:
        try:
            firebase_client.collection("equipos").document(doc_id).delete()
        except Exception as e:
            print(f"No se pudo borrar doc Firestore {doc_id}: {e}")


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
        return {"username": usuario.username, "nombre": usuario.nombre, "role": usuario.role or "ingeniero"}
    finally:
        db.close()


def exigir_super_admin(usuario):
    if usuario.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Solo super admin puede realizar esta accion")


def puede_ver_inventario(usuario):
    return usuario.get("role") != "guardia"


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
        username = form.username.strip()
        usuario = db.query(Usuario).filter_by(username=username).first()
        if not usuario:
            usuario = db.query(Usuario).filter_by(email=username).first()
        firebase_login = None
        if "@" in username:
            firebase_login = autenticar_firebase_email(username, form.password)
        if firebase_login and not usuario:
            usuario = Usuario(
                id       = str(uuid.uuid4()),
                username = firebase_login["email"],
                password = pwd_context.hash(form.password),
                nombre   = firebase_login["email"].split("@")[0],
                email    = firebase_login["email"],
                firebase_uid = firebase_login["uid"],
                role     = "super_admin" if firebase_login["email"] == SUPER_ADMIN_EMAIL else "ingeniero",
                activo   = True
            )
            db.add(usuario)
            db.commit()
        elif firebase_login and usuario:
            usuario.email = firebase_login["email"]
            usuario.firebase_uid = usuario.firebase_uid or firebase_login["uid"]
            usuario.activo = True
            if firebase_login["email"] == SUPER_ADMIN_EMAIL:
                usuario.role = "super_admin"
            db.commit()
        if not usuario or not usuario.activo or (not firebase_login and not verificar_password(form.password, usuario.password)):
            raise HTTPException(status_code=401, detail="Usuario o contrasena incorrectos")
        role = usuario.role or "ingeniero"
        token = crear_token({"sub": usuario.username, "nombre": usuario.nombre, "role": role})
        return {"access_token": token, "token_type": "bearer", "nombre": usuario.nombre, "role": role}
    finally:
        db.close()


@app.get("/api/me")
def me(usuario=Depends(get_usuario_actual)):
    return usuario


@app.post("/api/usuarios")
def crear_usuario(data: UsuarioCreate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    if data.role not in {"guardia", "ingeniero", "super_admin"}:
        raise HTTPException(status_code=400, detail="Rol invalido")
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
            email    = _normalizar_email_usuario(data.username, data.email),
            role     = data.role or "ingeniero",
            activo   = True
        )
        db.add(nuevo)
        db.commit()
        sync_usuario_to_firebase(nuevo, data.password)
        db.commit()
        return {"ok": True, "mensaje": f"Usuario {data.username} creado", "email": nuevo.email, "firebase_uid": nuevo.firebase_uid}
    finally:
        db.close()


@app.get("/api/usuarios")
def listar_usuarios(usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    db = Session()
    try:
        usuarios = db.query(Usuario).filter_by(activo=True).all()
        return {"usuarios": [{"username": u.username, "nombre": u.nombre, "email": u.email, "firebase_uid": u.firebase_uid, "role": u.role or "ingeniero"} for u in usuarios]}
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
        sync_usuario_to_firebase(u)
        db.commit()
        return {"ok": True, "mensaje": f"Usuario {username} eliminado"}
    finally:
        db.close()


@app.patch("/api/usuarios/{username}/role")
def actualizar_role_usuario(username: str, data: UsuarioRoleUpdate, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    if data.role not in {"guardia", "ingeniero", "super_admin"}:
        raise HTTPException(status_code=400, detail="Rol invalido")
    db = Session()
    try:
        u = db.query(Usuario).filter_by(username=username, activo=True).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        u.role = data.role
        db.commit()
        sync_usuario_to_firebase(u)
        db.commit()
        return {"ok": True, "mensaje": f"Rol actualizado para {username}", "role": data.role}
    finally:
        db.close()


@app.post("/api/usuarios/cambiar-password")
def cambiar_password(data: CambiarPassword, usuario=Depends(get_usuario_actual)):
    db = Session()
    try:
        u = db.query(Usuario).filter_by(username=usuario["username"]).first()
        if not verificar_password(data.password_actual, u.password):
            raise HTTPException(status_code=400, detail="Contrasena actual incorrecta")
        actualizar_password_usuario(u, data.password_nueva)
        db.commit()
        return {"ok": True, "mensaje": "Contrasena actualizada"}
    finally:
        db.close()


@app.post("/api/usuarios/{username}/reset-token")
def generar_reset_token(username: str, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    db = Session()
    try:
        u = db.query(Usuario).filter_by(username=username, activo=True).first()
        if not u:
            u = db.query(Usuario).filter_by(email=username, activo=True).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        token = secrets.token_urlsafe(32)
        u.reset_token = token
        u.reset_expires = datetime.utcnow() + timedelta(minutes=30)
        db.commit()
        return {
            "ok": True,
            "reset_url": f"/reset-password?token={token}",
            "expires_at": u.reset_expires.isoformat()
        }
    finally:
        db.close()


@app.post("/api/password-reset/confirm")
def confirmar_reset_password(data: ResetPasswordConfirm):
    if len(data.password_nueva or "") < 6:
        raise HTTPException(status_code=400, detail="La contrasena debe tener al menos 6 caracteres")
    db = Session()
    try:
        u = db.query(Usuario).filter_by(reset_token=data.token, activo=True).first()
        if not u or not u.reset_expires or u.reset_expires < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Token invalido o expirado")
        if not u.firebase_uid:
            sync_usuario_to_firebase(u)
        actualizar_password_usuario(u, data.password_nueva)
        u.reset_token = None
        u.reset_expires = None
        db.commit()
        return {"ok": True, "mensaje": "Contrasena actualizada"}
    finally:
        db.close()


@app.post("/api/debug/firebase-login")
def debug_firebase_login(data: DebugFirebaseLogin, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    if not FIREBASE_WEB_API_KEY:
        return {"ok": False, "error": "FIREBASE_WEB_API_KEY no configurada"}
    try:
        resp = httpx.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}",
            json={"email": data.email.strip(), "password": data.password, "returnSecureToken": True},
            timeout=10
        )
        payload = resp.json()
        if resp.status_code == 200:
            return {"ok": True, "email": payload.get("email"), "uid": payload.get("localId")}
        return {
            "ok": False,
            "status": resp.status_code,
            "firebase_error": payload.get("error", {}).get("message", "unknown")
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
                equipo_geo = db.query(Equipo).filter(
                    Equipo.serial_number == serial_limpio_geo
                ).first()
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
            equipo = db.query(Equipo).filter(
                Equipo.serial_number == serial_limpio
            ).first()
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
        equipos = db.query(Equipo).all()
        limite_offline = datetime.utcnow() - timedelta(minutes=2)
        resultado = []
        for e in equipos:
            online = e.ultimo_ping >= limite_offline if e.ultimo_ping else False
            asignacion = _obtener_asignacion_firestore(e)
            item = {
                "device_id":   e.device_id,
                "serial_number": e.serial_number,
                "hostname":    e.hostname,
                "inventariado": bool(asignacion.get("numInventario") or asignacion.get("asignado")),
                "dentro":      e.dentro,
                "online":      online,
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
            if puede_ver_inventario(usuario):
                item.update({
                    "numInventario": asignacion.get("numInventario", ""),
                    "asignado": asignacion.get("asignado", ""),
                    "departamento": asignacion.get("departamento", ""),
                    "puesto": asignacion.get("puesto", ""),
                    "ip": e.ip,
                    "ssid": e.ssid,
                    "sistema": e.sistema,
                })
            resultado.append(item)
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


@app.post("/api/equipos/{device_id}/registrar")
def registrar_equipo_inventario(device_id: str, data: EquipoRegistro, usuario=Depends(get_usuario_actual)):
    exigir_super_admin(usuario)
    firebase_client = _get_firebase_db()
    if not firebase_client:
        raise HTTPException(status_code=400, detail="Firebase no configurado")
    db = Session()
    try:
        equipo = db.query(Equipo).filter_by(device_id=device_id).first()
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        doc_id = _resolver_doc_firestore(equipo, equipo.serial_number)
        payload = {
            "deviceId": equipo.device_id,
            "serie": equipo.serial_number or "",
            "hostname": equipo.hostname or "",
            "tipo": "Laptop",
            "subtipo": "Laptop",
            "numInventario": (data.numInventario or "").strip(),
            "asignado": (data.asignado or "").strip(),
            "departamento": (data.departamento or "").strip(),
            "puesto": (data.puesto or "").strip(),
            "origen": "sensor_validado",
            "inventariado": True,
            "actualizadoEn": datetime.utcnow().isoformat(),
        }
        firebase_client.collection("equipos").document(doc_id).set(payload, merge=True)
        sync_equipo_to_firestore(equipo, equipo.serial_number)
        return {"ok": True, "mensaje": "Laptop registrada como inventario oficial"}
    finally:
        db.close()


@app.delete("/api/equipos/{device_id}")
def eliminar_equipo(device_id: str, usuario=Depends(get_usuario_actual)):
    """
    Elimina un equipo de la BD (y su historial de pings).
    Útil para limpiar duplicados o equipos dados de baja.
    """
    exigir_super_admin(usuario)
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


@app.get("/guardia")
def guardia_page():
    return FileResponse("guardia.html")


@app.get("/login")
def login_page():
    return FileResponse("login.html")


@app.get("/reset-password")
def reset_password_page():
    return FileResponse("reset-password.html")


@app.get("/")
def root():
    return FileResponse("login.html")