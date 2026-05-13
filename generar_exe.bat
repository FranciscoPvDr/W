@echo off
pushd "%~dp0"
title Generador de Sensor de Equipos
color 0A
echo.
echo =============================================
echo   GENERADOR DE SENSOR - MONITOR DE EQUIPOS
echo =============================================
echo.

:: Verificar que Python esta instalado
py --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta instalado.
    echo Descargalo en: https://www.python.org/downloads/
    echo Asegurate de marcar "Add Python to PATH" al instalar.
    pause
    exit /b 1
)

echo [1/4] Python encontrado. Instalando dependencias...
py -m pip install requests pyinstaller --quiet
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

echo [2/4] Dependencias instaladas correctamente.
echo.
echo [3/4] Generando ejecutable... (puede tardar 1-2 minutos)
echo.

py -m PyInstaller --onefile ^
                  --noconsole ^
                  --name "SensorEquipo" ^
                  sensor.py

if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la generacion del ejecutable.
    pause
    exit /b 1
)

echo.
echo [4/4] Limpiando archivos temporales...
rmdir /s /q build >nul 2>&1
del /q SensorEquipo.spec >nul 2>&1
copy /Y "sensor_config.json" "dist\sensor_config.json" >nul
copy /Y "instalar_en_pc.bat" "dist\instalar_en_pc.bat" >nul
copy /Y "actualizar_config.bat" "dist\actualizar_config.bat" >nul

echo.
echo =============================================
echo   LISTO! Archivos en:
echo   dist\SensorEquipo.exe
echo   dist\sensor_config.json
echo   dist\instalar_en_pc.bat
echo   dist\actualizar_config.bat
echo.
echo   Edita sensor_config.json con la URL del servidor
echo   y luego ejecuta instalar_en_pc.bat en cada PC.
echo =============================================
echo.
pause
