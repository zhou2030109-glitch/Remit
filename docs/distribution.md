# 构建和运行发布包

**Windows 最新候选包（2026-10-03）：[前往下载](https://github.com/zhou2030109-glitch/Remit/releases/tag/candidate-20261003-7407a60)。** 包含本轮优化和 CI 修复，源码检查及包内运行验证通过；实际安装、升级、卸载和独立用户电脑验收仍待完成。程序内版本号仍为 2.0.1，请按候选文件名与校验值区分。

## Docker 发布镜像

根目录 `Dockerfile` 将生产前端和后端装入同一 Linux 镜像，包含 Python
计算环境、Pandoc、XeLaTeX 与开源中文字体。浏览器、REST 和 WebSocket 共用
一个端口。原有 `docker-compose.yml` 继续用于源码开发。
发布环境使用独立的 `remit-release` 项目名，避免复用开发环境的数据卷。

```bash
docker build --build-arg SOURCE_REVISION=$(git rev-parse HEAD) -t remit:2.0.1 .
docker compose -f docker-compose.release.yml up -d --no-build
```

打开 <http://localhost:18000>，在界面内填写模型配置。无需复制开发环境的密钥。
端口冲突时，Bash 中先执行 `export REMIT_PORT=18080`，PowerShell 中先执行
`$env:REMIT_PORT="18080"`；发布服务只绑定本机回环地址。

配置、任务文件和消息档案分别保存在 Compose 命名卷中。
`docker compose -f docker-compose.release.yml down` 停止容器并保留数据；不要在
希望保留任务时使用 `down -v`。备份时先停止服务，再备份这些卷。

离线分发可导出应用镜像：

```bash
docker save remit:2.0.1 | gzip > remit-2.0.1-linux-amd64.tar.gz
```

接收方需要支持 Linux 容器的 Docker，先导入归档，再使用随包 Compose 文件：

```bash
docker load -i remit-2.0.1-linux-amd64.tar.gz
docker compose -f docker-compose.release.yml up -d --no-build --pull never
```

Windows 用户可使用 Docker Desktop 的 Linux 容器后端，或自行配置 WSL 中的 Docker。
镜像导入后不需要网络安装计算依赖；实际建模仍需要可访问的模型服务。

## Windows 安装包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/package_win.ps1
```

默认输出 `%LOCALAPPDATA%\Remit\build\output\RemitSetup.exe`。
构建需要 uv。脚本使用 `backend/uv.lock` 在 `<BuildRoot>\dependency-env` 创建独立生产环境，
不复制开发环境中的测试工具或额外安装包，也不修改 `backend/.venv`。
锁文件与项目声明不一致或依赖检查失败会中止构建；不会在打包时自动更新锁文件。
默认从开发环境定位基础 Python；未创建开发环境时，可用 `-BasePython` 指定基础 Python 安装目录。
`backend/build-info.json` 记录锁文件 SHA-256 和依赖构建方式。

包中包含 Python、生产前端和后端源码；不包含本机模型密钥和历史任务。
安装后在界面填写模型配置。Python 计算无需另装 MATLAB。

安装包不捆绑 MiKTeX/TeX Live；需要导出最终 LaTeX/PDF 论文时，请在目标电脑安装
XeLaTeX，并确保 `xelatex` 可从 PATH 调用。没有编译器时应用会给出明确提示。
中文论文模板使用 `ctex`、`fvextra` 等宏包；`fvextra` 负责代码长行换行，避免附录文字被裁切。
本机已验证安装的该宏包。应用不会在编译时自动安装缺失的 TeX 宏包。
本机构建与隔离目录验证不能替代所有 Windows 版本上的安装兼容性测试。

安装目录内可运行随包自检（无模型调用）：

```powershell
.\runtime\python\python.exe .\tools\verify_portable_runtime.pyc --install-root . --report .\logs\runtime-check.json
```

自检会使用包内 Python 启动独立 Jupyter 内核，检查计算库、写作技能读取、
简单运算及 CSV/PNG 导出。临时计算目录在结束后清理，不读取历史任务。
目标电脑已安装 XeLaTeX 时，可追加 `--with-latex`，实际编译并渲染国赛、
华为杯和美赛三套带公式及图片的测试模板。自检成功不代表外部模型连接、
MATLAB 或具体赛题结果已通过验证；默认不检查 LaTeX。

分发时保留 LICENSE、NOTICE.md 与 THIRD_PARTY_NOTICES.md，并用提供的 SHA256
校验文件确认下载完整性。
