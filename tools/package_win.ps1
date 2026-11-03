# Remit Windows 打包脚本
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File tools\package_win.ps1
# 产物: <BuildRoot>\output\RemitSetup.exe
#
# 原理：把基础 Python + 锁文件创建的生产 site-packages 合并为"便携运行时"，
# 前端以静态文件形式由后端直接托管，MATLAB 缺失时自动回退 Python。

[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$BuildRoot = (Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Remit\build"),
    [string]$BasePython = "",
    [switch]$SkipFrontendBuild,
    [switch]$SkipInstaller,
    [switch]$BytecodeOnly
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
$DeveloperVenv = Join-Path $RepoRoot "backend\.venv"
$VenvDir = Join-Path $BuildRoot "dependency-env"
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

function Initialize-BuildDependencies {
    param([string]$ProjectDirectory, [string]$EnvironmentDirectory, [string]$PythonExecutable)
    $project = [IO.Path]::GetFullPath($ProjectDirectory).TrimEnd('\')
    $environment = [IO.Path]::GetFullPath($EnvironmentDirectory).TrimEnd('\')
    if ($environment.Equals($project, [StringComparison]::OrdinalIgnoreCase) -or
        $environment.StartsWith($project + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $project.StartsWith($environment + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "构建依赖环境必须与开发项目目录隔离"
    }
    $previousEnvironment = [Environment]::GetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", "Process")
    try {
        $env:UV_PROJECT_ENVIRONMENT = $environment
        & uv sync --project $project --locked --no-dev --no-install-project --no-editable --no-python-downloads --python $PythonExecutable | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "锁定生产依赖安装失败；未修改开发环境" }
    }
    finally {
        [Environment]::SetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", $previousEnvironment, "Process")
    }
    $python = Join-Path $environment "Scripts\python.exe"
    Assert-Path -Path $python -Message "独立构建 Python 缺失"
    & uv pip check --python $python | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "生产依赖一致性检查失败" }
}

function Install-InnoChineseLanguage {
    param([string]$CompilerDirectory)
    # Inno Setup 6.7.3 的安装程序未捆绑简体中文；固定官方源文件版本与摘要。
    $languagePath = Join-Path $CompilerDirectory "Languages\ChineseSimplified.isl"
    $languageHash = "E0B0B350E2245F3C5E65586DFE43D574F6E7F06F2261149ABA284954B3FC9A8D"
    if ((Test-Path -LiteralPath $languagePath) -and
        (Get-FileHash -LiteralPath $languagePath -Algorithm SHA256).Hash -eq $languageHash) {
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $languagePath) | Out-Null
    $downloadPath = "$languagePath.download"
    try {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/jrsoftware/issrc/6ef32198ef1f7b7b375cd4b6b90896c2a58eb4c2/Files/Languages/ChineseSimplified.isl" -OutFile $downloadPath -UseBasicParsing
        if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash -ne $languageHash) {
            throw "Inno Setup 简体中文语言文件校验失败"
        }
        Move-Item -LiteralPath $downloadPath -Destination $languagePath -Force
    }
    finally {
        if (Test-Path -LiteralPath $downloadPath) {
            Remove-Item -LiteralPath $downloadPath -Force
        }
    }
}

Write-Host "========== Remit 打包开始 =========="

# ---------- 0. 校验输入 ----------
Assert-Path -Path $RepoRoot -Message "仓库根目录不存在"
Assert-Path -Path (Join-Path $RepoRoot "backend\app\main.py") -Message "后端代码缺失"
if ([string]::IsNullOrWhiteSpace($BasePython)) {
    $developerPython = Join-Path $DeveloperVenv "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $developerPython)) {
        throw "请通过 -BasePython 指定基础 Python 目录，或先准备 backend\.venv"
    }
    $BasePython = (& $developerPython -c "import sys; print(sys.base_prefix)").Trim()
}
$BasePython = [IO.Path]::GetFullPath($BasePython)
Assert-Path -Path $BasePython -Message "基础 Python 安装目录缺失"
Assert-Path -Path (Join-Path $BasePython "python.exe") -Message "基础 Python 可执行文件缺失"
$PythonVersion = (& (Join-Path $BasePython "python.exe") --version).Trim()
Write-Host "[0/7] 在独立目录安装锁定生产依赖（保留开发环境）..."
Initialize-BuildDependencies -ProjectDirectory (Join-Path $RepoRoot "backend") -EnvironmentDirectory $VenvDir -PythonExecutable (Join-Path $BasePython "python.exe")
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
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
$FontDirectory = Join-Path $RepoRoot "backend\fonts"
if (Test-Path -LiteralPath $FontDirectory -PathType Container) {
    Invoke-Robocopy $FontDirectory (Join-Path $Stage "backend\fonts")
}
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "backend\project\work_dir") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "backend\project\repair_backups") | Out-Null

# ---------- 3.5 可选的仅字节码分发（不改变源码许可证） ----------
if ($BytecodeOnly) {
    Write-Host "[2.5/7] 编译后端为字节码..."
    $StageAppDir = Join-Path $Stage "backend\app"
    & (Join-Path $BasePython "python.exe") -m compileall -q -b $StageAppDir
    if ($LASTEXITCODE -ne 0) { throw "后端字节码编译失败" }
    Get-ChildItem -LiteralPath $StageAppDir -Recurse -File -Filter *.py | Remove-Item -Force
    Get-ChildItem -LiteralPath $StageAppDir -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    $pycCount = (Get-ChildItem -LiteralPath $StageAppDir -Recurse -File -Filter *.pyc).Count
    if ($pycCount -eq 0) { throw "后端字节码编译结果为空" }
    Write-Host "  已生成 $pycCount 个字节码文件，明文 .py 源码已移除"
}

# ---------- 4. 生成后端运行环境配置（不含任何真实密钥） ----------
Write-Host "[3/7] 生成运行配置 .env.dev..."
$envLines = Get-Content -LiteralPath (Join-Path $RepoRoot "backend\.env.example")
$envOverrides = @{
    "^ENV=.*" = "ENV=dev"
    "^CORS_ALLOW_ORIGINS=.*" = "CORS_ALLOW_ORIGINS=http://localhost:18000,http://127.0.0.1:18000"
    "^SERVER_HOST=.*" = "SERVER_HOST=http://localhost:18000"
    "^LOG_LEVEL=.*" = "LOG_LEVEL=INFO"
    "^DEBUG=.*" = "DEBUG=false"
    "^CODE_EXECUTION_BACKEND=.*" = "CODE_EXECUTION_BACKEND=python"
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
$buildCommit = (& git -C $RepoRoot rev-parse HEAD).Trim()
$sourceDigest = & $VenvPython -c "import hashlib,pathlib,sys; root=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); [(h.update(p.relative_to(root).as_posix().encode()),h.update(p.read_bytes())) for p in sorted(root.rglob('*.py'))]; print(h.hexdigest())" (Join-Path $RepoRoot "backend\app")
if ($LASTEXITCODE -ne 0) { throw "源码摘要计算失败" }
$sourceDigest = $sourceDigest.Trim()
$buildInfo = @{
    commit = $buildCommit
    built_at = [DateTime]::UtcNow.ToString("o")
    source_sha256 = $sourceDigest
    source_hash_scope = "backend/app/**/*.py"
    dirty = [bool](& git -C $RepoRoot status --porcelain)
    platform = "windows-x64"
    latex_bundled = $false
    dependency_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $RepoRoot "backend\uv.lock") -Algorithm SHA256).Hash.ToLower()
    dependencies = "uv sync --locked --no-dev --no-install-project; isolated build environment"
}
Write-Utf8NoBom -Path (Join-Path $Stage "backend\build-info.json") -Lines @($buildInfo | ConvertTo-Json)

# ---------- 5. 组装便携 Python 运行时 ----------
Write-Host "[4/7] 组装便携 Python 运行时（锁定生产依赖）..."
$RuntimePython = Join-Path $Stage "runtime\python"
New-Item -ItemType Directory -Force -Path $RuntimePython | Out-Null
Invoke-Robocopy $BasePython $RuntimePython -ExcludeDirs @("Doc", "Scripts", "share", "site-packages") -ExcludeFiles @("*.pyc")
Invoke-Robocopy (Join-Path $VenvDir "Lib\site-packages") (Join-Path $RuntimePython "Lib\site-packages") -ExcludeDirs @("__pycache__") -ExcludeFiles @("*.pyc")
if (Test-Path -LiteralPath (Join-Path $VenvDir "share")) {
    Invoke-Robocopy (Join-Path $VenvDir "share") (Join-Path $Stage "runtime\share") -ExcludeDirs @("__pycache__")
}

# ---------- 6. 复制前端、资产与启动脚本 ----------
Write-Host "[5/7] 复制前端与启动脚本..."
Invoke-Robocopy (Join-Path $RepoRoot "frontend\dist") (Join-Path $Stage "frontend\dist")
$assetsDir = Join-Path $Stage "assets"
New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null
foreach ($icon in @("remit-icon.png", "remit-m-icon.png", "remit-m-icon.ico")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot "assets\$icon") -Destination $assetsDir -Force
}
foreach ($entryPoint in @("remit_prod_app", "verify_portable_runtime")) {
    $entrySource = Join-Path $Stage "tools\$entryPoint.py"
    Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\$entryPoint.py") -Destination $entrySource -Force
    & (Join-Path $BasePython "python.exe") -m compileall -q -b $entrySource
    if ($LASTEXITCODE -ne 0) { throw "$entryPoint 字节码编译失败" }
    if ($BytecodeOnly) {
        Remove-Item -LiteralPath $entrySource -Force
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Stage "tools\$entryPoint.pyc"))) {
        throw "$entryPoint 字节码未生成"
    }
}
Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\启动Remit.bat") -Destination (Join-Path $Stage "启动Remit.bat") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "tools\停止Remit.bat") -Destination (Join-Path $Stage "停止Remit.bat") -Force
foreach ($noticeFile in @("LICENSE", "NOTICE.md", "THIRD_PARTY_NOTICES.md")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot $noticeFile) -Destination (Join-Path $Stage $noticeFile) -Force
}
$packageDocs = Join-Path $Stage "docs"
New-Item -ItemType Directory -Force -Path $packageDocs | Out-Null
foreach ($doc in @("distribution.md", "paper-library.md")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\$doc") -Destination $packageDocs -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "logs") | Out-Null

# 版本信息
$gitHash = "unknown"
try { $gitHash = (git -C $RepoRoot rev-parse --short HEAD).Trim() } catch {}
$SourceLayout = if ($BytecodeOnly) {
    "Python 文件以字节码形式分发；对应源码见公开仓库，MIT 许可不变"
}
else {
    "Python 源码随安装包分发"
}
Write-Utf8NoBom -Path (Join-Path $Stage "VERSION.txt") -Lines @(
    "Remit 打包版本 2.0.1",
    "构建时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "来源提交: $gitHash",
    "内置 $PythonVersion + 前端静态文件",
    "源码形式: $SourceLayout",
    "来源与许可: 请阅读随附 LICENSE、NOTICE.md 与 THIRD_PARTY_NOTICES.md"
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
    "  1. 打开工作台后，从左侧【设置】进入模型配置。",
    "  2. 可选择四位角色共用一个模型连接；高级设置可分别配置：",
    "     - API 类型（如 openai-chat / openai-responses / anthropic）",
    "     - API 密钥",
    "     - 模型名称（如 deepseek-chat）",
    "     - API 服务地址（如 https://api.deepseek.com/v1）",
    "  3. 保存配置不会调用模型；文本和工具验证须在界面显式操作，可能计费。",
    "",
    "三、MATLAB 说明（重点）",
    "  默认使用包内 Python。MATLAB 是可选能力，需用户安装并持有有效许可证。",
    "  首页本地环境自检会实际验证 Python 内核、计算库、中文图与论文编译器。",
    "  自检不调用模型，不代表真实模型解题或论文科学质量已通过。",
    "",
    "四、常见问题",
    "  1. 端口 16379/18000 被占用：关闭占用这些端口的程序后重启应用。",
    "  2. 图表中文乱码：任务工作目录会自动放入中文字体，无需手动处理。",
    "  3. 想彻底卸载：使用系统的【添加或删除程序】卸载 Remit。",
    "  4. 数据与日志：%LOCALAPPDATA%\Remit\data；界面也显示实际数据目录。",
    "  5. 源码与许可证：请阅读安装目录中的 LICENSE、NOTICE.md 与 THIRD_PARTY_NOTICES.md。",
    "",
    "五、论文 PDF 导出",
    "  安装包不包含 LaTeX 发行版。导出最终论文 PDF 需要另行安装 MiKTeX 或 TeX Live，",
    "  并确保 xelatex 已加入 PATH。缺少编译器时应用会提示，不影响 Python 建模计算。",
    "",
    "六、来源与许可",
    "  当前源码已完成针对 MathModelAgent 的独立实现整改。",
    "  早期版本来源、扫描边界和许可提示请阅读安装目录中的 NOTICE.md。",
    "  当前 Remit 自有源码使用 MIT License；第三方文件保留各自许可。",
    "",
    "七、计算环境自检（不调用模型）",
    "  在安装目录运行：runtime\python\python.exe tools\verify_portable_runtime.pyc --install-root . --report logs\runtime-check.json",
    "  检查包内 Python、Jupyter、计算库及 CSV/PNG 导出；不代表外部模型、MATLAB 或 LaTeX 已验证。",
    ""
)

# 来源声明文件（随安装包分发）
Write-Utf8NoBom -Path (Join-Path $Stage "版权声明.txt") -Lines @(
    "Remit 来源与许可声明",
    "============================================",
    "",
    "当前源码已完成针对 MathModelAgent 的独立实现整改。",
    "本项目曾发布早期版本；重建分支和字节码编译不改变其来源事实。",
    "完整说明请阅读随附 NOTICE.md。",
    "",
    "本声明随安装包一同分发。"
)

# ---------- 7.5 安全校验：确认暂存区不含真实密钥（防止 API Key 误打包） ----------
Write-Host "[6.5/7] 密钥泄漏检查..."
$secretPatterns = @(
    'sk-[A-Za-z0-9]{20,}',
    'AIza[A-Za-z0-9_\-]{30,}',
    'AKIA[0-9A-Z]{16}',
    'xox[baprs]-[A-Za-z0-9\-]{20,}',
    'gh[pousr]_[A-Za-z0-9]{20,}'
)
$secretTargets = @(
    (Join-Path $Stage "backend\app"),
    (Join-Path $Stage "backend\.env.dev"),
    (Join-Path $Stage "tools")
)
$leaks = @()
foreach ($target in $secretTargets) {
    if (-not (Test-Path -LiteralPath $target)) { continue }
    $item = Get-Item -LiteralPath $target
    $files = if ($item.PSIsContainer) {
        Get-ChildItem -LiteralPath $target -Recurse -File | Where-Object { $_.Extension -in '.pyc', '.py', '.txt', '.toml', '.json', '.bat', '.env.dev' }
    } else { @($item) }
    foreach ($file in $files) {
        try { $content = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) } catch { continue }
        foreach ($pattern in $secretPatterns) {
            if ($content -match $pattern) {
                $leaks += "$($file.FullName.Replace($Stage, '')) 命中疑似密钥 $pattern"
            }
        }
    }
}
if ($leaks.Count -gt 0) {
    throw "检测到疑似密钥泄漏，已中止打包：" + [Environment]::NewLine + ($leaks -join [Environment]::NewLine)
}
Write-Host "  未发现真实密钥，打包安全。"
# 包内真实执行链路必须通过后才生成安装器，避免开发机环境掩盖故障。
Write-Host "  验证包内 Python、真实代码执行器、中文图与 CSV（不调用模型）..."
& (Join-Path $RuntimePython "python.exe") (Join-Path $Stage "tools\verify_portable_runtime.pyc") --install-root $Stage --report (Join-Path $BuildRoot "runtime-check.json")
if ($LASTEXITCODE -ne 0) { throw "包内计算验证失败，停止生成安装包" }
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
    $installProc = Start-Process -FilePath $InnoDownload -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/PORTABLE=1", "/DIR=`"$InnoDir`"") -WindowStyle Hidden -PassThru -Wait
    if ($installProc.ExitCode -ne 0) {
        throw "Inno Setup 便携安装失败，退出码 $($installProc.ExitCode)"
    }
}
Assert-Path -Path $IsccExe -Message "ISCC.exe 不可用"
Install-InnoChineseLanguage -CompilerDirectory $InnoDir

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
