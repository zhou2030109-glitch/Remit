[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LogDirectory = Join-Path $Root "logs"
$RedisPort = 16379
$BackendPort = 18000
$FrontendPort = 15173
$services = @(
    @{ Name = "frontend"; Port = $FrontendPort },
    @{ Name = "backend"; Port = $BackendPort },
    @{ Name = "redis"; Port = $RedisPort }
)

function Get-ListeningProcessId([int]$Port) {
    # Do not use ``-p TCP``: it omits IPv6 listeners such as Vite on ::1.
    $lines = & netstat.exe -ano
    foreach ($line in $lines) {
        $parts = @($line.Trim() -split "\s+")
        if (
            $parts.Count -ge 5 -and
            $parts[0] -eq "TCP" -and
            $parts[1].EndsWith(":$Port") -and
            $parts[3] -eq "LISTENING"
        ) {
            $listenerId = 0
            if ([int]::TryParse($parts[4], [ref]$listenerId)) {
                return $listenerId
            }
        }
    }
    return 0
}

function Get-ProjectProcessRoot([int]$ListenerId) {
    $rootPattern = [regex]::Escape($Root)
    $currentId = $ListenerId
    $projectRootId = 0
    for ($depth = 0; $depth -lt 8 -and $currentId -gt 0; $depth++) {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $currentId" -ErrorAction SilentlyContinue
        if ($null -eq $processInfo) {
            break
        }
        $identity = "$($processInfo.ExecutablePath) $($processInfo.CommandLine)"
        if ($identity -notmatch $rootPattern) {
            break
        }
        $projectRootId = $currentId
        $currentId = [int]$processInfo.ParentProcessId
    }
    return $projectRootId
}

foreach ($service in $services) {
    $serviceName = $service.Name
    $pidPath = Join-Path $LogDirectory "$serviceName.pid"
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        $listenerId = Get-ListeningProcessId -Port $service.Port
        if ($listenerId -le 0) {
            Write-Host "[OK] $serviceName is already stopped."
            continue
        }
        $projectRootId = Get-ProjectProcessRoot -ListenerId $listenerId
        if ($projectRootId -le 0) {
            Write-Warning "$serviceName uses port $($service.Port), but it was not started from this project; leaving it running."
            continue
        }
        & taskkill.exe /PID $projectRootId /T /F | Out-Null
        Write-Host "[STOPPED] $serviceName (discovered PID $projectRootId)"
        continue
    }

    $processIdText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    $processId = 0
    if (-not [int]::TryParse($processIdText, [ref]$processId)) {
        Write-Warning "Invalid PID file: $pidPath"
        Remove-Item -LiteralPath $pidPath -Force
        continue
    }

    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Host "[OK] $serviceName is already stopped."
    }
    else {
        & taskkill.exe /PID $processId /T /F | Out-Null
        Write-Host "[STOPPED] $serviceName (PID $processId)"
    }
    Remove-Item -LiteralPath $pidPath -Force
}
