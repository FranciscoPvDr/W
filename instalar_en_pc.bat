@echo off
title Instalador de Sensor de Equipos
color 0A
cd /d "%~dp0"
echo.
echo =============================================
echo   INSTALADOR - SENSOR DE EQUIPOS
echo =============================================
echo.

:: Verificar archivos necesarios
if not exist "SensorEquipo.exe" (
    echo [ERROR] No se encuentra SensorEquipo.exe en esta carpeta.
    pause
    exit /b 1
)

if not exist "sensor_config.json" (
    echo [ERROR] No se encuentra sensor_config.json en esta carpeta.
    pause
    exit /b 1
)

:: Detener instancia previa antes de reemplazar el ejecutable
taskkill /F /IM SensorEquipo.exe >nul 2>&1

:: Eliminar tarea anterior si existe
schtasks /delete /tn "SensorEquipo" /f >nul 2>&1

:: Copiar archivos al directorio de instalacion
set DESTINO=%APPDATA%\SensorEquipo
mkdir "%DESTINO%" >nul 2>&1
copy /Y "SensorEquipo.exe" "%DESTINO%\SensorEquipo.exe"
if errorlevel 1 (
    echo [ERROR] No se pudo copiar SensorEquipo.exe. Cierra el sensor o reinicia Windows e intenta de nuevo.
    pause
    exit /b 1
)
copy /Y "sensor_config.json" "%DESTINO%\sensor_config.json"
if errorlevel 1 (
    echo [ERROR] No se pudo copiar sensor_config.json.
    pause
    exit /b 1
)
echo [1/4] Archivos copiados a: %DESTINO%

:: Crear tarea programada
:: - Se ejecuta al iniciar sesion cualquier usuario
:: - Delay de 30 segundos para que Windows termine de cargar
:: - Corre con privilegios altos en segundo plano
schtasks /create ^
    /tn "SensorEquipo" ^
    /tr "\"%DESTINO%\SensorEquipo.exe\"" ^
    /sc ONLOGON ^
    /delay 0000:30 ^
    /rl HIGHEST ^
    /f >nul

if errorlevel 1 (
    echo [ERROR] No se pudo crear la tarea programada.
    echo Intenta ejecutar este archivo como Administrador.
    pause
    exit /b 1
)
echo [2/4] Tarea programada creada correctamente.

:: Eliminar entrada vieja del registro si existe
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "SensorEquipo" /f >nul 2>&1
echo [3/4] Entrada antigua del registro eliminada.

:: Arrancar el sensor ahora
start "" "%DESTINO%\SensorEquipo.exe"
echo [4/4] Sensor iniciado.

echo.
echo =============================================
echo   Instalacion completada!
echo.
echo   El sensor arrancara automaticamente al
echo   iniciar sesion en Windows.
echo.
echo   Config: %DESTINO%\sensor_config.json
echo   Log:    %DESTINO%\sensor.log
echo =============================================
echo.
pause