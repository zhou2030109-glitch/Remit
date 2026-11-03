[CmdletBinding()]
param([string]$OwnerToken = "")

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

function Test-ProjectListener([int]$ListenerId, [string]$Name) {
    # 旧版本缺少身份记录时只识别具体服务，不向上追溯到桌面壳或用户终端。
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $ListenerId" -ErrorAction SilentlyContinue
    if ($null -eq $info) { return $false }
    $rootPrefix = $Root.TrimEnd('\') + '\'
    switch ($Name) {
        "redis" { return $info.ExecutablePath -eq (Join-Path $Root "tools\redis\redis-server.exe") }
        "backend" {
            $pythonPaths = @(
                (Join-Path $Root "backend\.venv\Scripts\python.exe"),
                (Join-Path $Root "backend\venv\Scripts\python.exe")
            )
            if ($info.CommandLine -notmatch '\buvicorn\s+app\.main:app\b') { return $false }
            if ($info.ExecutablePath -in $pythonPaths) { return $true }
            # uv 托管的 venv 由 venv 内 python.exe 派生子解释器并让子进程监听端口，
            # 监听者路径不在 $pythonPaths 里；向上追溯父进程确认它仍属于本项目。
            $parentId = [int]$info.ParentProcessId
            for ($depth = 0; $depth -lt 4 -and $parentId -gt 0; $depth++) {
                $parentInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $parentId" -ErrorAction SilentlyContinue
                if ($null -eq $parentInfo) { return $false }
                if ($parentInfo.ExecutablePath -in $pythonPaths) { return $true }
                $parentId = [int]$parentInfo.ParentProcessId
            }
            return $false
        }
        "frontend" {
            $vitePath = [regex]::Escape($rootPrefix + 'frontend\node_modules\')
            return $info.CommandLine -match $vitePath -and $info.CommandLine -match '[\\/]vite[\\/]bin[\\/]vite\.js(?:"|\s|$)'
        }
    }
    return $false
}

function Test-RecordedProcess([int]$ProcessId, [string]$Name, [string]$IdentityPath) {
    if (-not (Test-Path -LiteralPath $IdentityPath -PathType Leaf)) { return $false }
    try {
        $identity = Get-Content -LiteralPath $IdentityPath -Raw | ConvertFrom-Json
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        return (
            $identity.ProcessId -eq $ProcessId -and
            $identity.ProjectRoot -eq $Root -and
            $identity.Service -eq $Name -and
            $identity.StartedUtcTicks -eq $process.StartTime.ToUniversalTime().Ticks.ToString() -and
            -not [string]::IsNullOrWhiteSpace($identity.ExecutablePath) -and
            $identity.ExecutablePath -eq $process.Path
        )
    }
    catch { return $false }
}

function Stop-ServiceTree([int]$ProcessId, [string]$Name) {
    if ($Name -eq "redis") {
        $python = Join-Path $Root "backend\.venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            $python = Join-Path $Root "backend\venv\Scripts\python.exe"
        }
        & $python (Join-Path $PSScriptRoot "redis_lifecycle.py") stop --port $RedisPort
        if ($LASTEXITCODE -ne 0) { throw "Redis could not save its state; leaving it running." }
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $process -and -not $process.WaitForExit(10000)) {
            throw "Redis is still exiting after saving; leaving it running."
        }
        Write-Host "[STOPPED] $Name (PID $ProcessId, saved)"
        return
    }
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        throw "Could not stop $Name (PID $ProcessId)."
    }
    Write-Host "[STOPPED] $Name (PID $ProcessId)"
}

foreach ($service in $services) {
    $serviceName = $service.Name
    $pidPath = Join-Path $LogDirectory "$serviceName.pid"
    $identityPath = "$pidPath.json"
    if ($OwnerToken) {
        try {
            $identity = Get-Content -LiteralPath $identityPath -Raw -ErrorAction Stop | ConvertFrom-Json
            if ($identity.OwnerToken -ne $OwnerToken -or $identity.ProjectRoot -ne $Root -or $identity.Service -ne $serviceName) {
                continue
            }
        }
        catch { continue }
    }
    $processId = 0
    if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
        $processIdText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
        [void][int]::TryParse($processIdText, [ref]$processId)
    }
    if ($processId -gt 0 -and (Test-RecordedProcess -ProcessId $processId -Name $serviceName -IdentityPath $identityPath)) {
        Stop-ServiceTree -ProcessId $processId -Name $serviceName
    }
    elseif ($OwnerToken) {
        Write-Host "[OK] $serviceName from this desktop session is no longer running."
    }
    else {
        $listenerId = Get-ListeningProcessId -Port $service.Port
        if ($listenerId -gt 0 -and (Test-ProjectListener -ListenerId $listenerId -Name $serviceName)) {
            Stop-ServiceTree -ProcessId $listenerId -Name $serviceName
        }
        elseif ($listenerId -gt 0) {
            Write-Warning "$serviceName uses port $($service.Port), but it was not started from this project; leaving it running."
        }
        else { Write-Host "[OK] $serviceName is already stopped." }
    }
    foreach ($record in @($pidPath, $identityPath)) {
        if (Test-Path -LiteralPath $record -PathType Leaf) {
            Remove-Item -LiteralPath $record -Force
        }
    }
}
