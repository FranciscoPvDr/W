# Guía de Despliegue - Monitor de Equipos

Esta guía te ayudará a desplegar tu aplicación en Render y reemplazar ngrok permanentemente.

## 📋 Prerrequisitos

1. **Git instalado** - Descárgalo de [git-scm.com](https://git-scm.com/)
2. **Cuenta en GitHub** - [github.com](https://github.com)
3. **Cuenta en Render** - [render.com](https://render.com)

## 🚀 Paso 1: Instalar Git

Si no tienes Git instalado:

1. Ve a [git-scm.com/download](https://git-scm.com/download)
2. Descarga la versión para Windows
3. Ejecuta el instalador (puedes dejar las opciones por defecto)

## 🚀 Paso 2: Inicializar Repositorio Git

Abre una terminal en la carpeta de tu proyecto y ejecuta:

```bash
git init
git add .
git commit -m "Initial commit - Monitor de equipos"
```

## 🚀 Paso 3: Crear Repositorio en GitHub

1. Ve a [github.com/new](https://github.com/new)
2. Ponle un nombre como `monitor-equipos`
3. Déjalo como **Público** o **Privado** (tu decisión)
4. NO marques "Initialize this repository with a README"
5. Haz clic en "Create repository"

## 🚀 Paso 4: Conectar con GitHub

En tu terminal, ejecuta estos comandos (reemplaza `TU-USUARIO` y `TU-REPOSITORIO`):

```bash
git remote add origin https://github.com/TU-USUARIO/monitor-equipos.git
git branch -M main
git push -u origin main
```

## 🚀 Paso 5: Desplegar en Render

### Opción A: Usando render.yaml (Recomendado)

1. Ve a [render.com](https://render.com) e inicia sesión
2. Haz clic en **"New +"** → **"Blueprint"**
3. Conecta tu cuenta de GitHub
4. Selecciona tu repositorio `monitor-equipos`
5. Render detectará automáticamente el archivo `render.yaml`
6. Haz clic en **"Apply"**

### Opción B: Manual

1. Ve a [render.com](https://render.com) → **"New +"** → **"Web Service"**
2. Conecta tu repositorio de GitHub
3. Configura:
   - **Name**: `monitor-equipos-backend`
   - **Region**: Oregon (o el más cercano a México)
   - **Branch**: `main`
   - **Root Directory**: (déjalo vacío)
   - **Runtime**: `Python 3`
   - **Build Command**: (déjalo vacío)
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

4. **Variables de Entorno** (haz clic en "Environment" → "Add Environment Variable"):
   - `PYTHON_VERSION`: `3.11.0`
   - `SECRET_KEY`: (genera una clave segura, ej: `mi-clave-secreta-2024-muy-segura`)
   - `OFFICE_LAT`: Coordenada de latitud de tu oficina (ej: `19.4326`)
   - `OFFICE_LNG`: Coordenada de longitud de tu oficina (ej: `-99.1332`)
   - `MAX_GEO_ACCURACY_M`: `250`
   - `OFFLINE_ALERT_MINUTES`: `10`

5. **Firebase (Opcional)**:
   - Si usas Firebase, necesitas subir tus credenciales:
     - Ve a "Environment" → "Files" → "Add File"
     - Sube tu archivo `inventariosti-mc-firebase-adminsdk-*.json`
     - Luego agrega la variable `FIREBASE_CREDENTIALS_PATH` con el nombre del archivo

6. Haz clic en **"Create Web Service"**

## 🚀 Paso 6: Actualizar Configuración del Sensor

Una vez que Render despliegue tu aplicación:

1. Copia la URL que te da Render (ej: `https://monitor-equipos-backend.onrender.com`)
2. Actualiza el archivo `sensor_config.json`:
   ```json
   {
     "SERVER_URL": "https://monitor-equipos-backend.onrender.com",
     ...
   }
   ```
3. Vuelve a empaquetar el sensor con la nueva configuración

## 🚀 Paso 7: Actualizar Dashboard

El dashboard está en `dashboard.html`. Necesitas actualizar la URL del backend:

1. Abre `dashboard.html`
2. Busca la línea que dice algo como:
   ```javascript
   const API_URL = "https://mountable-heroics-doorpost.ngrok-free.app";
   ```
3. Cámbiala a:
   ```javascript
   const API_URL = "https://monitor-equipos-backend.onrender.com";
   ```
4. Guarda los cambios

## 🔍 Verificar el Despliegue

1. **Dashboard**: Accede a `https://TU-URL.onrender.com/dashboard`
2. **API**: Accede a `https://TU-URL.onrender.com/` (debería mostrar el estado)
3. **Login**: Usuario: `admin`, Contraseña: `admin123`

## ⚠️ Consideraciones Importantes

### 1. Base de Datos Persistente
Render usa un sistema de archivos efímero. Para producción, considera:
- **PostgreSQL** (Render ofrece uno gratuito)
- Migrar los datos existentes

### 2. Cold Starts
El plan gratuito de Render tiene "cold starts" (se duerme después de 15 min de inactividad).
- La primera petición después de dormir tarda ~30 segundos
- Los sensores seguirán funcionando, solo la primera petición tardará más

### 3. Firebase
Si usas Firebase, asegúrate de:
- Subir el archivo JSON de credenciales como "Secret File" en Render
- Configurar la variable `FIREBASE_CREDENTIALS_PATH`

### 4. URLs de ngrok
Elimina las referencias a ngrok en:
- `sensor.py` (línea 25)
- `sensor_config.json`
- `dashboard.html`

## 📦 Comandos Útiles

### Actualizar el despliegue:
```bash
git add .
git commit -m "Actualización"
git push
```
Render automáticamente redeplegará con cada push a `main`.

### Ver logs en Render:
Ve a tu servicio en Render → Pestaña "Logs"

## 🆘 Solución de Problemas

### Error: "Module not found"
- Verifica que `requirements.txt` esté en la raíz
- Revisa los logs en Render para ver qué módulo falta

### Error: "Database locked"
- SQLite no es ideal para producción
- Considera migrar a PostgreSQL

### Error: "Firebase credentials not found"
- Verifica que el archivo JSON esté subido como "Secret File"
- Verifica que `FIREBASE_CREDENTIALS_PATH` apunte al nombre correcto del archivo

## 📞 Soporte

Si tienes problemas:
1. Revisa los logs en Render
2. Prueba localmente primero: `uvicorn main:app --reload`
3. Verifica que todas las variables de entorno estén configuradas

---

**¡Listo! Ahora tienes tu aplicación desplegada profesionalmente sin depender de ngrok.** 🎉