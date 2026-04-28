@echo off
title Actualizar Configuracion Sensor
color 0A
echo.
echo =============================================
echo   ACTUALIZAR URL DEL SENSOR
echo =============================================
echo.

set DESTINO=%APPDATA%\SensorEquipo
set CONFIG=%DESTINO%\sensor_config.json

if not exist "%CONFIG%" (
    echo [ERROR] No existe %CONFIG%
    echo Instala primero el sensor con instalar_en_pc.bat
    pause
    exit /b 1
)

set /p NUEVA_URL=Escribe la nueva URL (https://...ngrok-free.dev):
if "%NUEVA_URL%"=="" (
    echo [ERROR] URL vacia.
    pause
    exit /b 1
)

powershell -NoProfile -Command ^
  "$p='%CONFIG%';" ^
  "$j=Get-Content $p -Raw | ConvertFrom-Json;" ^
  "$j.SERVER_URL='%NUEVA_URL%';" ^
  "$j | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $p"

if errorlevel 1 (
    echo [ERROR] No se pudo actualizar el archivo de configuracion.
    pause
    exit /b 1
)

taskkill /F /IM SensorEquipo.exe >nul 2>&1
start "" "%DESTINO%\SensorEquipo.exe"

echo.
echo [OK] Configuracion actualizada y sensor reiniciado.
echo Archivo: %CONFIG%
echo URL: %NUEVA_URL%
echo.
pause
