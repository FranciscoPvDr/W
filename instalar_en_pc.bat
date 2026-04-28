@echo off
title Instalador de Sensor de Equipos
color 0A
echo.
echo =============================================
echo   INSTALADOR - SENSOR DE EQUIPOS
echo =============================================
echo.

if not exist "SensorEquipo.exe" (
    echo [ERROR] No se encuentra SensorEquipo.exe en esta carpeta.
    pause
    exit /b 1
)

if not exist "sensor_config.json" (
    echo [ERROR] No se encuentra sensor_config.json en esta carpeta.
    echo Copia el archivo de config junto al .exe.
    pause
    exit /b 1
)

set DESTINO=%APPDATA%\SensorEquipo
mkdir "%DESTINO%" >nul 2>&1
copy /Y "SensorEquipo.exe" "%DESTINO%\SensorEquipo.exe" >nul
copy /Y "sensor_config.json" "%DESTINO%\sensor_config.json" >nul

echo [1/4] Archivos copiados a: %DESTINO%

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" ^
    /v "SensorEquipo" ^
    /t REG_SZ ^
    /d "\"%DESTINO%\SensorEquipo.exe\"" ^
    /f >nul

echo [2/4] Configurado para iniciar con Windows.

taskkill /F /IM SensorEquipo.exe >nul 2>&1
echo [3/4] Deteniendo instancia previa (si existe).

start "" "%DESTINO%\SensorEquipo.exe"
echo [4/4] Sensor iniciado.

echo.
echo =============================================
echo   Sensor activo y con auto-inicio.
echo   Config editable: %DESTINO%\sensor_config.json
echo =============================================
echo.
pause
