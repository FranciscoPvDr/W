@echo off

title Instalador de Sensor de Equipos

color 0A

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



:: Copiar archivos al directorio de instalacion

set DESTINO=%APPDATA%\SensorEquipo

mkdir "%DESTINO%" >nul 2>&1

copy /Y "SensorEquipo.exe" "%DESTINO%\SensorEquipo.exe" >nul

copy /Y "sensor_config.json" "%DESTINO%\sensor_config.json" >nul

echo [1/4] Archivos copiados a: %DESTINO%



:: Eliminar tarea anterior si existe

schtasks /delete /tn "SensorEquipo" /f >nul 2>&1



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



:: Detener instancia previa y arrancar el sensor ahora

taskkill /F /IM SensorEquipo.exe >nul 2>&1

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