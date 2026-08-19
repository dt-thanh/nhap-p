$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if ($args.Count -gt 0) {
    $Target = $args[0]
} else {
    $Target = 'head'
}

if ($env:BACKUP_DIR) {
    $BackupDir = $env:BACKUP_DIR
} else {
    $BackupDir = 'backups'
}

if ($env:DB_SERVICE) {
    $DBService = $env:DB_SERVICE
} else {
    $DBService = 'db'
}

function Write-Info {
    param([string]$Message)
    Write-Host "[migrate] $Message"
}

function Fail {
    param([string]$Message)
    Write-Error "[migrate] LỖI: $Message"
    exit 1
}

$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile)) {
    Fail "không thấy .env ở $repoRoot"
}

Get-Content -Path $envFile | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrWhiteSpace($line)) {
        return
    }
    if ($line.StartsWith('#')) {
        return
    }
    if ($line.Contains('=')) {
        $parts = $line.Split('=', 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

if ($env:POSTGRES_USER) {
    $PG_USER = $env:POSTGRES_USER
} else {
    $PG_USER = 'app'
}

if ($env:POSTGRES_DB) {
    $PG_DB = $env:POSTGRES_DB
} else {
    $PG_DB = 'absorption'
}

if ($env:APP_ENV) {
    $appEnv = $env:APP_ENV
} else {
    $appEnv = 'development'
}

Write-Info "database : $PG_DB (user $PG_USER)"
Write-Info "app_env  : $appEnv"
Write-Info "target   : $Target"

$current = @()
& docker compose exec -T $DBService psql -U $PG_USER -d $PG_DB -tAc "SELECT version_num FROM alembic_version" 2>$null | ForEach-Object {
    $current += $_
}
if ($LASTEXITCODE -ne 0) {
    $current = @()
}
$currentText = ($current | Out-String).Trim()
if ($currentText) {
    $currentLabel = $currentText
} else {
    $currentLabel = '<chưa có>'
}
Write-Info "revision : $currentLabel"

if ($appEnv -eq 'production') {
    Write-Host ''
    Write-Host '*** ĐÂY LÀ MÔI TRƯỜNG SẢN XUẤT ***'
    $confirm = Read-Host "Gõ đúng tên database ($PG_DB) để tiếp tục"
    if ($confirm -ne $PG_DB) {
        Fail 'không khớp — dừng lại.'
    }
}

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Backup = Join-Path $BackupDir "pre_${Target}_${Stamp}.dump"

Write-Info "đang sao lưu -> $Backup"
& docker compose exec -T $DBService pg_dump -U $PG_USER -d $PG_DB --format=custom > $Backup 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail 'pg_dump thất bại — KHÔNG migrate.'
}
if ((Get-Item $Backup).Length -le 0) {
    Fail 'bản sao lưu rỗng — KHÔNG migrate.'
}

$verifyPath = '/tmp/verify_' + [System.IO.Path]::GetFileName($Backup)
& docker compose cp "$Backup" "${DBService}:${verifyPath}" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail 'không chép được bản sao lưu vào container để kiểm.'
}
$entries = (& docker compose exec -T $DBService pg_restore --list $verifyPath 2>$null | Select-String 'TABLE DATA' | Measure-Object).Count
& docker compose exec -T $DBService rm -f $verifyPath 2>$null
if ($entries -le 0) {
    Fail 'bản sao lưu không đọc được (0 bảng dữ liệu) — KHÔNG migrate.'
}
Write-Info "sao lưu hợp lệ ($((Get-Item $Backup).Length) byte, $entries bảng có dữ liệu)"

Write-Info "alembic upgrade $Target"
& docker compose exec -T api alembic upgrade $Target
if ($LASTEXITCODE -ne 0) {
    Fail "alembic upgrade thất bại. Bản sao lưu: $Backup"
}

$newRev = (& docker compose exec -T $DBService psql -U $PG_USER -d $PG_DB -tAc "SELECT version_num FROM alembic_version" 2>$null | Out-String).Trim()
Write-Info "revision sau khi migrate: $newRev"
if ([string]::IsNullOrWhiteSpace($newRev)) {
    Fail 'không đọc được alembic_version sau khi migrate.'
}

Write-Info 'kiểm dữ liệu so với baseline (nếu có)...'
$baseline = Join-Path $repoRoot ("docs/baselines/dev_" + $newRev.Split('_')[0] + '.json')
if (Test-Path $baseline) {
    & python -m scripts.baseline_dev_data --compare $baseline
} else {
    Write-Info "chưa có $baseline — tạo baseline mới sau khi kiểm bằng mắt."
}

Write-Host ''
Write-Info "XONG. Bản sao lưu trước khi migrate: $Backup"
Write-Info 'Lùi lại: docker compose exec api alembic downgrade <revision trước>'
