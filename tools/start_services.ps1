[CmdletBinding()]
param(
    [switch]$Visible,
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RedisDirectory = Join-Path $Root "tools\redis"
$RedisExecutable = Join-Path $RedisDirectory "redis-server.exe"
$BackendDirectory = Join-Path $Root "backend"
$BackendPython = Join-Path $BackendDirectory ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
    $BackendPython = Join-Path $BackendDirectory "venv\Scripts\python.exe"
}
$FrontendDirectory = Join-Path $Root "frontend"
$LogDirectory = Join-Path $Root "logs"
$RedisPort = 16379
$BackendPort = 18000
$FrontendPort = 15173

function Assert-LauncherDependencies {
    if (-not (Test-Path -LiteralPath $RedisExecutable -PathType Leaf)) {
        throw "Redis executable not found: $RedisExecutable"
    }
    if (-not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
        throw "Backend virtual environment not found. Run: cd backend; uv sync"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory "package.json") -PathType Leaf)) {
        throw "Frontend package.json not found: $FrontendDirectory"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory "node_modules") -PathType Container)) {
        throw "Frontend dependencies are missing. Run: cd frontend; pnpm install"
    }
    $viteEntryPoint = Join-Path $FrontendDirectory "node_modules\vite\bin\vite.js"
    if (-not (Test-Path -LiteralPath $viteEntryPoint -PathType Leaf)) {
        throw "Frontend Vite entry point not found. The project may have moved. Run: cd frontend; pnpm install --force --frozen-lockfile"
    }
    $script:PnpmCommand = (Get-Command "pnpm.cmd" -ErrorAction Stop).Source
}

function Test-ListeningPort([int]$Port) {
    return $script:ListeningPorts -contains $Port
}

function Test-ProjectOwnedListener([int]$Port) {
    $rootPattern = [regex]::Escape($Root)
    $listeners = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($listeners.Count -eq 0) {
        return $false
    }

    foreach ($listenerId in $listeners) {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerId" -ErrorAction SilentlyContinue
        if ($null -eq $processInfo) {
            return $false
        }
        $identity = "$($processInfo.ExecutablePath) $($processInfo.CommandLine)"
        if ($identity -notmatch $rootPattern) {
            return $false
        }
    }
    return $true
}

function Save-ServicePid([string]$Name, [System.Diagnostics.Process]$Process) {
    $pidPath = Join-Path $LogDirectory "$Name.pid"
    Set-Content -LiteralPath $pidPath -Value $Process.Id -Encoding ascii -NoNewline
}

function Start-ProjectService {
    param(
        [string]$Name,
        [int]$Port,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory
    )

    if (Test-ListeningPort $Port) {
        if (Test-ProjectOwnedListener $Port) {
            Write-Host "[OK] $Name is already listening on port $Port."
            return
        }
        throw "Port $Port is occupied by another application. Stop that application before starting Remit."
    }

    $startParameters = @{
        FilePath = $FilePath
        ArgumentList = $ArgumentList
        WorkingDirectory = $WorkingDirectory
        PassThru = $true
    }
    if ($Visible) {
        $startParameters.WindowStyle = "Normal"
    }
    else {
        $startParameters.WindowStyle = "Hidden"
        $startParameters.RedirectStandardOutput = Join-Path $LogDirectory "$Name.out.log"
        $startParameters.RedirectStandardError = Join-Path $LogDirectory "$Name.err.log"
    }

    $process = Start-Process @startParameters
    Save-ServicePid -Name $Name -Process $process
    Write-Host "[STARTED] $Name (PID $($process.Id), port $Port)"
}

Assert-LauncherDependencies
if ($Check) {
    Write-Host "LAUNCHER_CHECK_OK"
    exit 0
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$ListeningPorts = @(
    [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        ForEach-Object { $_.Port }
)

Write-Host "=============================================="
Write-Host " Remit: Redis + FastAPI + Vue"
Write-Host " Mode: $(if ($Visible) { 'visible terminals' } else { 'hidden background services' })"
Write-Host "=============================================="

Start-ProjectService `
    -Name "redis" `
    -Port $RedisPort `
    -FilePath $RedisExecutable `
    -ArgumentList @("--port", "$RedisPort", "--bind", "127.0.0.1", "::1") `
    -WorkingDirectory $RedisDirectory

Start-ProjectService `
    -Name "backend" `
    -Port $BackendPort `
    -FilePath $BackendPython `
    -ArgumentList @(
        "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "$BackendPort",
        "--ws-ping-interval", "60", "--ws-ping-timeout", "120"
    ) `
    -WorkingDirectory $BackendDirectory

$pnpmInvocation = '"{0}" run dev --host 127.0.0.1 --port {1} --strictPort' -f $PnpmCommand, $FrontendPort
$frontendCmdSwitch = if ($Visible) { "/k" } else { "/c" }
Start-ProjectService `
    -Name "frontend" `
    -Port $FrontendPort `
    -FilePath $env:ComSpec `
    -ArgumentList @("/d", "/s", $frontendCmdSwitch, "`"$pnpmInvocation`"") `
    -WorkingDirectory $FrontendDirectory

Write-Host ""
Write-Host "Frontend: http://localhost:$FrontendPort"
Write-Host "Backend:  http://localhost:$BackendPort"
if (-not $Visible) {
    Write-Host "Logs:     $LogDirectory"
    Write-Host "Stop:     double-click win_stop.bat"
}
