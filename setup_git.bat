@echo off
echo ========================================
echo   Setup Git y Primer Commit
echo ========================================
echo.

REM Check if git is installed
where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Git no esta instalado!
    echo.
    echo Por favor, instala Git desde:
    echo https://git-scm.com/download
    echo.
    pause
    exit /b 1
)

echo Git encontrado!
echo.

REM Initialize git repo
echo [1/4] Inicializando repositorio Git...
git init

echo [2/4] Agregando todos los archivos...
git add .

echo [3/4] Creando primer commit...
git commit -m "Initial commit - Monitor de equipos listo para Render"

echo.
echo [4/4] Configuracion completada!
echo.
echo AHORA SIGUE ESTOS PASOS:
echo.
echo 1. Crea un repositorio en GitHub:
echo    https://github.com/new
echo.
echo 2. Ejecuta estos comandos (reemplaza TU-USUARIO):
echo    git remote add origin https://github.com/TU-USUARIO/monitor-equipos.git
echo    git branch -M main
echo    git push -u origin main
echo.
echo 3. Lee DEPLOYMENT_GUIDE.md para desplegar en Render
echo.
pause