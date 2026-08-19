@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

if "%1"=="" (
    set "TARGET=head"
) else (
    set "TARGET=%1"
)

set "BACKUP_DIR=backups"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

for /f "skip=1 tokens=1-3 delims=/ " %%a in ('echo %date%') do set "STAMP=%%a-%%b-%%c"
set "STAMP=%date:~-2,2%-%date:~-7,2%-%date:~-10,4%_%time:~0,2%-%time:~3,2%-%time:~6,2%"
set "STAMP=%STAMP: =0%"
set "BACKUP=%BACKUP_DIR%\pre_%TARGET%_%STAMP%.dump"

echo [migrate] database : absorption (user app)
echo [migrate] target   : %TARGET%

docker compose up -d --build
if errorlevel 1 (
    echo [migrate] LỖI: docker compose up thất bại.
    exit /b 1
)

echo [migrate] đang sao lưu -> %BACKUP%
docker compose exec -T db pg_dump -U app -d absorption --format=custom > "%BACKUP%"
if errorlevel 1 (
    echo [migrate] LỖI: pg_dump thất bại — KHÔNG migrate.
    exit /b 1
)

if not exist "%BACKUP%" (
    echo [migrate] LỖI: bản sao lưu không tạo ra — KHÔNG migrate.
    exit /b 1
)

for %%I in ("%BACKUP%") do if %%~zI==0 (
    echo [migrate] LỖI: bản sao lưu rỗng — KHÔNG migrate.
    exit /b 1
)

echo [migrate] alembic upgrade %TARGET%
docker compose exec -T api alembic upgrade %TARGET%
if errorlevel 1 (
    echo [migrate] LỖI: alembic upgrade thất bại. Bản sao lưu: %BACKUP%
    exit /b 1
)

docker compose exec -T db psql -U app -d absorption -tAc "SELECT version_num FROM alembic_version"

echo [migrate] XONG. Bản sao lưu trước khi migrate: %BACKUP%

echo [migrate] Lùi lại: docker compose exec api alembic downgrade ^<revision trước^>
exit /b 0
