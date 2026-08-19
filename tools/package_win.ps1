# Remit Windows 打包脚本
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File tools\package_win.ps1
# 产物: <BuildRoot>\output\RemitSetup.exe
#
# 原理：把现有 Python 3.13 基础安装 + 后端虚拟环境 site-packages 合并为"便携运行时"，
# 前端以静态文件形式由后端直接托管，Redis 使用包内二进制，MATLAB 缺失时自动回退 Python。

[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\Users\33845\Desktop\Remit",
    [string]$BuildRoot = "E:\codex\remit-build",
    [switch]$SkipFrontendBuild,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$BasePython = "C:\Users\33845\AppData\Local\Programs\Python\Python313"
$VenvDir = Join-Path $RepoRoot "backend\.venv"
$Stage = Join-Path $BuildRoot "staging\Remit"
$InnoDir = Join-Path $BuildRoot "tools\InnoSetup"
$IsccExe = Join-Path $InnoDir "ISCC.exe"
$OutDir = Join-Path $BuildRoot "output"
$InnoDownload = Join-Path $BuildRoot "tools\innosetup-dl.exe"

function Assert-Path {
    param([string]$Path, [string]$Message)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Message : $Path"
    }
}

function Invoke-Robocopy {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    $argsList = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
    foreach ($d in $ExcludeDirs) { $argsList += @("/XD", $d) }
    foreach ($f in $ExcludeFiles) { $argsList += @("/XF", $f) }
    & robocopy @argsList | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy 失败(代码 $LASTEXITCODE): $Source -> $Destination"
    }
}

function Remove-TrustedTree {
    param([string]$Path, [string]$RootGuard)
    $resolved = [IO.Path]::GetFullPath($Path)
    $guard = [IO.Path]::GetFullPath($RootGuard).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($guard, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除越界路径: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string[]]$Lines)
    $text = $Lines -join "`n"
    [IO.File]::WriteAllText($Path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host "========== Remit 打包开始 =========="

# ---------- 0. 校验输入 ----------
Assert-Path -Path $RepoRoot -Message "仓库根目录不存在"
Assert-Path -Path (Join-Path $RepoRoot "backend\app\main.py") -Message "后端代码缺失"
Assert-Path -Path (Join-Path $VenvDir "Scripts\python.exe") -Message "后端虚拟环境缺失"
Assert-Path -Path $BasePython -Message "基础 Python 3.13 安装目录缺失"
Assert-Path -Path (Join-Path $BasePython "python.exe") -Message "基础 Python 可执行文件缺失"
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# ---------- 1. 前端生产构建 ----------
$DistFile = Join-Path $RepoRoot "frontend\dist\index.html"
$FrontendBuilt = Test-Path -LiteralPath $DistFile
if (-not $SkipFrontendBuild) {
    Write-Host "[1/7] 构建前端 (pnpm run build)..."
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        pnpm run build
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
    }
    finally { Pop-Location }
    $FrontendBuilt = Test-Path -LiteralPath $DistFile
}
Assert-Path -Path $DistFile -Message "前端 dist/index.html 缺失（请先构建）"

# ---------- 2. 清空并建立暂存目录 ----------
Remove-TrustedTree -Path $Stage -RootGuard $BuildRoot
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

# ---------- 3. 复制后端代码与资源 ----------
Write-Host "[2/7] 复制后端代码与资源..."
Invoke-Robocopy (Join-Path $RepoRoot "backend\app") (Join-Path $Stage "backend\app") -ExcludeDirs @("__pycache__", ".pytest_cache", ".ruff_cache") -ExcludeFiles @("*.pyc")
Invoke-Robocopy (Join-Path $RepoRoot "backend\fonts") (Join-Path $Stage "backend\fonts")
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "backend\project\work_dir") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "backend\project\repair_backups") | Out-Null

# ---------- 4. 生成后端运行环境配置（不含任何真实密钥） ----------
Write-Host "[3/7] 生成运行配置 .env.dev..."
$envLines = Get-Content -LiteralPath (Join-Path $RepoRoot "backend\.env.example")
$envOverrides = @{
    "^ENV=.*" = "ENV=dev"
    "^REDIS_URL=.*" = "REDIS_URL=redis://127.0.0.1:16379/0"
    "^CORS_ALLOW_ORIGINS=.*" = "CORS_ALLOW_ORIGINS=*"
    "^SERVER_HOST=.*" = "SERVER_HOST=http://localhost:18000"
    "^LOG_LEVEL=.*" = "LOG_LEVEL=INFO"
    "^DEBUG=.*" = "DEBUG=false"
    "^CODE_EXECUTION_BACKEND=.*" = "CODE_EXECUTION_BACKEND=matlab"
    "^MATLAB_FALLBACK_TO_PYTHON=.*" = "MATLAB_FALLBACK_TO_PYTHON=true"
}
$envOutput = foreach ($line in $envLines) {
    $matched = $false
    foreach ($pattern in $envOverrides.Keys) {
        if ($line -match $pattern) {
            $envOverrides[$pattern]
            $matched = $true
            break
        }
    }
    if (-not $matched) { $line }
}
Write-Utf8NoBom -Path (Join-Path $Stage "backend\.env.dev") -Lines $envOutput

# ---------- 5. 组装便携 Python 运行时 ----------
Write-Host "[4/7] 组装便携 Python 运行时（约 1.8 GB）..."
$RuntimePython = Join-Path $Stage "runtime\python"
New-Item -ItemType Directory -Force -Path $RuntimePython | Out-Null
Invoke-Robocopy $BasePython $RuntimePython -ExcludeDirs @("Doc", "Scripts", "share", "site-packages") -ExcludeFiles @("*.pyc")
Invoke-Robocopy (Join-Path $VenvDir "Lib\site-packages") (Join-Path $RuntimePython "Lib\site-packages") -ExcludeDirs @("__pycache__") -ExcludeFiles @("*.pyc")
if (Test-Path -LiteralPath (Join-Path $VenvDir "share")) {
    Invoke-Robocopy (Join-Path $VenvDir "share") (Join-Path $Stage "runtime\share") -ExcludeDirs @("__pycache__")
}

# ---------- 6. 复制前端、Redis、资产与启动脚本 ----------
Write-Host "[5/7] 复制前端、Redis 与启动脚本..."
Invoke-Robocopy (Join-Path $RepoRoot "frontend\dist") (Join-Path $Stage "frontend\dist")
$assetsDir = Join-Path $Stage "assets"
New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null
foreach ($icon in @("remit-icon.png", "remit-m-icon.png", "remit-m-icon.ico")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot "assets\$icon") -Destination $assetsDir -Force
}
$redisDst = Join-Path $Stage "tools\redis"
New-Item -ItemType Directory -Force -Path $redisDst | Out-Null
foreach ($redisFile in @("redis-server.exe", "redis-cli.exe", "msys-2.0.dll", "msys-crypto-3.dll", "msys-gcc_s-seh-1.dll", "msys-ssl-3.dll", "msys-stdc++-6.dll")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\redis\$redisFile") -Destination $redisDst -Force
}
Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\remit_prod_app.py") -Destination (Join-Path $Stage "tools\remit_prod_app.py") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\启动Remit.bat") -Destination (Join-Path $Stage "启动Remit.bat") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\停止Remit.bat") -Destination (Join-Path $Stage "停止Remit.bat") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "logs") | Out-Null

# 版本信息
$gitHash = ""
try { $gitHash = (git -C $RepoRoot rev-parse --short HEAD).Trim() } catch {}
Write-Utf8NoBom -Path (Join-Path $Stage "VERSION.txt") -Lines @(
    "Remit 打包版本 1.0.0",
    "构建时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "来源提交: $gitHash",
    "内置 Python 3.13.3 + Redis + 前端静态文件"
)

# ---------- 7. 使用说明 ----------
Write-Utf8NoBom -Path (Join-Path $Stage "使用说明.txt") -Lines @(
    "Remit 数学建模工作台 —— 使用说明",
    "============================================",
    "",
    "一、启动与停止",
    "  1. 双击桌面/开始菜单的【Remit 数学建模工作台】，或在安装目录双击【启动Remit.bat】。",
    "  2. 首次启动需要 10~60 秒（内置 Python 环境初始化），之后自动打开浏览器界面。",
    "  3. 关闭浏览器后程序仍会驻留托盘；右键托盘图标选择【退出应用】即可完全退出。",
    "  4. 也可双击【停止Remit.bat】停止后台服务。",
    "",
    "二、模型配置（首次使用必填）",
    "  1. 打开工作台后，进入界面右上角的 API 配置对话框。",
    "  2. 为 Coordinator / Modeler / Coder / Writer 四个 Agent 分别填写：",
    "     - API 类型（如 openai-chat / openai-responses / anthropic）",
    "     - API 密钥",
    "     - 模型名称（如 deepseek-chat）",
    "     - API 服务地址（如 https://api.deepseek.com/v1）",
    "  3. 也可以直接编辑安装目录 backend\.env.dev 文件后重启。",
    "",
    "三、MATLAB 说明（重点）",
    "  本软件不需要安装 MATLAB。默认优先使用 MATLAB（自动探测 PATH 与常见安装目录），",
    "  检测不到 MATLAB 时自动回退到内置的 Python 计算环境，全部功能正常可用。",
    "  无需任何额外配置；界面中会提示当前使用的计算后端。",
    "",
    "四、常见问题",
    "  1. 端口 16379/18000 被占用：关闭占用这些端口的程序后重启应用。",
    "  2. 图表中文乱码：任务工作目录会自动放入中文字体，无需手动处理。",
    "  3. 想彻底卸载：使用系统的【添加或删除程序】卸载 Remit。",
    "  4. 日志位置：安装目录 logs\ 目录。",
    ""
)

# ---------- 8. 生成安装包 ----------
if ($SkipInstaller) {
    Write-Host "[6/7] 跳过安装包生成（-SkipInstaller），暂存目录: $Stage"
    Write-Host "========== 打包完成 =========="
    return
}

Write-Host "[6/7] 准备 Inno Setup..."
if (-not (Test-Path -LiteralPath $IsccExe)) {
    if (-not (Test-Path -LiteralPath $InnoDownload)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InnoDownload) | Out-Null
        Write-Host "  下载 Inno Setup..."
        Invoke-WebRequest -Uri "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe" -OutFile $InnoDownload -UseBasicParsing
    }
    Write-Host "  便携模式解压 Inno Setup..."
    New-Item -ItemType Directory -Force -Path $InnoDir | Out-Null
    $installProc = Start-Process -FilePath $InnoDownload -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/PORTABLE=1", "/DIR=`"$InnoDir`"") -PassThru -Wait
    if ($installProc.ExitCode -ne 0) {
        throw "Inno Setup 便携安装失败，退出码 $($installProc.ExitCode)"
    }
}
Assert-Path -Path $IsccExe -Message "ISCC.exe 不可用"

Write-Host "[7/7] 编译安装包 RemitSetup.exe..."
Push-Location (Join-Path $RepoRoot "tools\installer")
try {
    & $IsccExe remit.iss "/DStageDir=$BuildRoot\staging" "/DOutDir=$OutDir"
    if ($LASTEXITCODE -ne 0) { throw "ISCC 编译失败，退出码 $LASTEXITCODE" }
}
finally { Pop-Location }

$finalExe = Join-Path $OutDir "RemitSetup.exe"
Assert-Path -Path $finalExe -Message "安装包未生成"
$sizeMb = [math]::Round((Get-Item -LiteralPath $finalExe).Length / 1MB, 1)
$hash = (Get-FileHash -LiteralPath $finalExe -Algorithm SHA256).Hash
Write-Host ""
Write-Host "========== 打包完成 =========="
Write-Host "安装包: $finalExe ($sizeMb MB)"
Write-Host "SHA256: $hash"
Write-Host "暂存目录（可自行压缩为绿色版）: $Stage"
