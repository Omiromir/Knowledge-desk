#Requires -Version 7.4

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
$origin = 'https://127.0.0.1:8443'
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$openssl = (Get-Command openssl.exe -ErrorAction SilentlyContinue).Source
if (-not $openssl) {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $openssl = Join-Path (Split-Path (Split-Path $git.Source)) 'mingw64/bin/openssl.exe'
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Создайте .venv и установите requirements.txt по README.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'backend/.env') -PathType Leaf)) {
    throw 'Создайте backend/.env по .env.example и заполните настройки Supabase.'
}
if (-not $npm) { throw 'Установите Node.js с npm по README.md.' }
if (-not $openssl -or -not (Test-Path -LiteralPath $openssl -PathType Leaf)) {
    throw 'Установите Git for Windows или добавьте openssl.exe в PATH.'
}

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 8443)
try {
    $listener.Start()
} catch {
    throw 'Порт 8443 недоступен. Проверьте, не запущен ли стенд в другом окне.'
} finally {
    $listener.Stop()
}

$server = $null
Push-Location -LiteralPath $root
try {
    Write-Host 'Сборка интерфейса'
    $previousApi = $env:VITE_API_BASE_URL
    try {
        $env:VITE_API_BASE_URL = '/'
        if (-not (Test-Path -LiteralPath 'frontend/node_modules/.bin/vite.cmd' -PathType Leaf)) {
            & $npm.Source --prefix frontend ci
            if ($LASTEXITCODE -ne 0) { throw 'npm ci завершился с ошибкой.' }
        }
        & $npm.Source --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Сборка интерфейса завершилась с ошибкой.' }
    } finally {
        $env:VITE_API_BASE_URL = $previousApi
    }

    $run = 'output/supabase-check/stand/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
    New-Item -ItemType Directory -Path $run -Force | Out-Null
    $log = Join-Path $root "$run/server.log"
    & $openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj '/CN=localhost' `
        -addext 'subjectAltName=IP:127.0.0.1' -keyout "$run/key.pem" -out "$run/cert.pem" *> "$run/tls.log"
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать локальный сертификат. Лог $(Join-Path $root "$run/tls.log")" }

    Write-Host 'Запуск HTTPS и проверка /health'
    $server = Start-Process -FilePath $python -WorkingDirectory $root -ArgumentList @(
        '-X utf8 -m uvicorn backend.app.main:app',
        '--loop backend.app.postgres:selector_loop --env-file backend/.env',
        '--host 127.0.0.1 --port 8443 --no-access-log',
        "--ssl-certfile $run/cert.pem --ssl-keyfile $run/key.pem"
    ) -Environment @{
        KNOWLEDGE_DESK_REQUIRE_AUTH = '1'
        KNOWLEDGE_DESK_FRONTEND_ORIGIN = $origin
        KNOWLEDGE_DESK_STATIC_DIR = 'frontend/dist'
        KNOWLEDGE_DESK_CHUNK_SIZE = '600'
    } -RedirectStandardOutput (Join-Path $root "$run/server.stdout.log") `
      -RedirectStandardError $log -WindowStyle Hidden -PassThru

    $watch = [Diagnostics.Stopwatch]::StartNew()
    $ready = $false
    $lastHealth = 'нет ответа'
    while ($watch.Elapsed.TotalSeconds -lt 45 -and -not $server.HasExited) {
        try {
            $response = Invoke-WebRequest -Uri "$origin/health" -NoProxy -SkipCertificateCheck `
                -SkipHttpErrorCheck -TimeoutSec 4 -MaximumRedirection 0
            $lastHealth = "HTTP $($response.StatusCode) $($response.Content)"
            $ready = $response.StatusCode -eq 200 -and ($response.Content | ConvertFrom-Json).status -eq 'ready'
        } catch {
            # The listener may take a few seconds to start.
        }
        if ($ready) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "Стенд не готов ($lastHealth). Лог $log" }

    Write-Host "Стенд готов  $origin/"
    Write-Host "Лог для trace_id  $log"
    Write-Host 'Остановка стенда  Ctrl+C'
    try {
        Start-Process -FilePath "$origin/"
    } catch {
        Write-Warning "Откройте $origin/ вручную. Стенд уже работает."
    }
    Wait-Process -InputObject $server
    if ($server.ExitCode -ne 0) { throw "Сервер завершился с кодом $($server.ExitCode). Лог $log" }
} finally {
    if ($server -and -not $server.HasExited) {
        taskkill.exe /PID $server.Id /T /F | Out-Null
    }
    Pop-Location
}
