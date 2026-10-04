@echo off
REM DigiClinic - start the system locally (no Docker needed)
cd /d "%~dp0"
set TAILWINDCSS_VERSION=v4.3.3
set PYTHONUTF8=1
set "UV=python -m uv"
where uv >nul 2>nul && set "UV=uv"
echo [1/6] Installing packages...
%UV% sync --quiet || goto :error
echo [2/6] Starting database...
.venv\Scripts\python scripts\devdb.py start || goto :error
echo [3/6] Building styles...
.venv\Scripts\tailwindcss -i static\src\app.css -o static\css\app.css --minify || goto :error
echo [4/6] Updating database...
.venv\Scripts\python manage.py migrate --noinput || goto :error
.venv\Scripts\python manage.py seed_org || goto :error
echo [5/6] Starting WhatsApp gateway...
call :whatsapp
echo.
echo   [6/6] DigiClinic is running:  http://127.0.0.1:8010
echo   Keep this window open. Press Ctrl+C to stop.
echo.
start "" http://127.0.0.1:8010
.venv\Scripts\python manage.py runserver 127.0.0.1:8010
goto :eof

:whatsapp
set "NODE_EXE="
if exist "%ProgramFiles%\nodejs\node.exe" set "NODE_EXE=%ProgramFiles%\nodejs\node.exe"
if not defined NODE_EXE for /f "delims=" %%N in ('where node 2^>nul') do if not defined NODE_EXE set "NODE_EXE=%%N"
if not defined NODE_EXE (
  echo   Node.js not found - WhatsApp disabled, email still works.
  goto :eof
)
for %%D in ("%NODE_EXE%") do set "PATH=%%~dpD;%PATH%"
curl -s -o nul http://127.0.0.1:3320/health && (
  echo   WhatsApp gateway already running.
  goto :eof
)
if not exist whatsapp-gateway\node_modules (
  set PUPPETEER_SKIP_DOWNLOAD=true
  pushd whatsapp-gateway
  call npm install --no-audit --no-fund
  popd
)
start "DigiClinic WhatsApp" /min "%NODE_EXE%" whatsapp-gateway\server.js
echo   WhatsApp gateway started.
goto :eof

:error
echo.
echo   Something failed. Scroll up to see the error, and send a screenshot.
pause
