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
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, Float, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta
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


class Usuario(Base):
    __tablename__ = "usuarios"
    id       = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, index=True)
    password = Column(String)
    nombre   = Column(String)
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

        ping_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(ping_logs)"))}
        if "serial_number" not in ping_cols:
            conn.execute(text("ALTER TABLE ping_logs ADD COLUMN serial_number TEXT"))
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


class UsuarioCreate(BaseModel):
    username: str
    password: str
    nombre:   str


class CambiarPassword(BaseModel):
    password_actual: str
    password_nueva:  str


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
        token = crear_token({"sub": usuario.username, "nombre": usuario.nombre})
        return {"access_token": token, "token_type": "bearer", "nombre": usuario.nombre}
    finally:
        db.close()


@app.get("/api/me")
def me(usuario=Depends(get_usuario_actual)):
    return usuario


@app.post("/api/usuarios")
def crear_usuario(data: UsuarioCreate, usuario=Depends(get_usuario_actual)):
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
        return {"usuarios": [{"username": u.username, "nombre": u.nombre} for u in usuarios]}
    finally:
        db.close()


@app.delete("/api/usuarios/{username}")
def eliminar_usuario(username: str, usuario=Depends(get_usuario_actual)):
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
            accuracy  = accuracy
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
                accuracy    = accuracy
            )
            db.add(equipo)

        db.commit()
        sync_equipo_to_firestore(equipo, data.serial_number)
        geo = f"lat:{lat:.4f},lng:{lng:.4f},acc:{accuracy:.0f}m" if lat else "sin geo"
        print(f"Ping [{data.hostname}] {'DENTRO' if data.dentro else 'FUERA'} | {geo}")
        return {"ok": True, "mensaje": "Ping registrado", "geo": {"lat": lat, "lng": lng, "accuracy": accuracy}}
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
            resultado.append({
                "device_id":   e.device_id,
                "serial_number": e.serial_number,
                "numInventario": asignacion.get("numInventario", ""),
                "asignado": asignacion.get("asignado", ""),
                "departamento": asignacion.get("departamento", ""),
                "puesto": asignacion.get("puesto", ""),
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
            })
        return {"equipos": resultado, "total": len(resultado)}
    finally:
        db.close()


@app.delete("/api/equipos/{device_id}")
def eliminar_equipo(device_id: str, usuario=Depends(get_usuario_actual)):
    """
    Elimina un equipo de la BD (y su historial de pings).
    Útil para limpiar duplicados o equipos dados de baja.
    """
    db = Session()
    try:
        equipo = db.query(Equipo).filter_by(device_id=device_id).first()
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        # Borrar historial de pings asociado
        db.query(PingLog).filter_by(device_id=device_id).delete()
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

@app.get("/")
def root():
    return {"estado": "Monitor de equipos activo", "version": "3.0"}