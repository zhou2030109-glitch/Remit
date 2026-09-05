# 构建和运行发布包

## Docker 发布镜像

根目录 `Dockerfile` 将生产前端和后端装入同一 Linux 镜像，包含 Python
计算环境、Pandoc、XeLaTeX 与开源中文字体。浏览器、REST 和 WebSocket 共用
一个端口。原有 `docker-compose.yml` 继续用于源码开发。
发布环境使用独立的 `remit-release` 项目名，避免复用开发环境的数据卷。

```bash
docker build --build-arg SOURCE_REVISION=$(git rev-parse HEAD) -t remit:0.1.0 .
docker compose -f docker-compose.release.yml up -d --no-build
```

打开 <http://localhost:18000>，在界面内填写模型配置。无需复制开发环境的密钥。
端口冲突时，Bash 中先执行 `export REMIT_PORT=18080`，PowerShell 中先执行
`$env:REMIT_PORT="18080"`；发布服务只绑定本机回环地址。

配置、任务文件、消息档案和 Redis 数据分别保存在 Compose 命名卷中。
`docker compose -f docker-compose.release.yml down` 停止容器并保留数据；不要在
希望保留任务时使用 `down -v`。备份时先停止服务，再备份这些卷。

离线分发可将应用和 Redis 一起导出：

```bash
docker pull redis:7.4-alpine
docker save remit:0.1.0 redis:7.4-alpine | gzip > remit-0.1.0-linux-amd64.tar.gz
```

接收方需要支持 Linux 容器的 Docker，先导入归档，再使用随包 Compose 文件：

```bash
docker load -i remit-0.1.0-linux-amd64.tar.gz
docker compose -f docker-compose.release.yml up -d --no-build --pull never
```

Windows 本机如果使用本次构建准备的 WSL Ubuntu，可在终端通过
`wsl -d Ubuntu-24.04 -u root -- docker ...` 调用 Docker，或进入该发行版后执行上述命令。
镜像导入后不需要网络安装计算依赖；实际建模仍需要可访问的模型服务。

## Windows 安装包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/package_win.ps1
```

默认输出 `%LOCALAPPDATA%\Remit\build\output\RemitSetup.exe`。
包中包含 Python、Redis、生产前端和后端源码；不包含本机模型密钥和历史任务。
安装后在界面填写模型配置。Python 计算无需另装 MATLAB。

安装包不捆绑 MiKTeX/TeX Live；需要导出最终 LaTeX/PDF 论文时，请在目标电脑安装
XeLaTeX，并确保 `xelatex` 可从 PATH 调用。没有编译器时应用会给出明确提示。
本机构建与隔离目录验证不能替代所有 Windows 版本上的安装兼容性测试。

分发时保留 LICENSE、NOTICE.md 与 THIRD_PARTY_NOTICES.md，并用提供的 SHA256
校验文件确认下载完整性。
