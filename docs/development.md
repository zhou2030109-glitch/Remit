# 开发与发布

工具链：Python 3.12、uv 0.12.10、Node.js 24、pnpm 10.6.3。安装步骤见根目录 README，依赖使用锁文件。

## 验证

```sh
cd backend
uv sync --locked
uv run pytest tests -q
uv pip check
cd ../frontend
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

根目录启动器测试：Windows 使用 `backend/.venv/Scripts/python.exe -m pytest tests -q`；macOS/Linux 使用 `backend/.venv/bin/python -m pytest tests -q`。部分测试有平台限制。

## macOS/Linux 源码运行

按 README 安装后端和前端依赖并复制示例环境文件。在根目录执行：

```sh
bash tools/start_services.sh --check
bash tools/start_services.sh
```

打开 http://localhost:15173 。停止服务执行 `bash tools/stop_services.sh`。本机 PDF 编译另需 XeLaTeX 和中文字体。

## CI 与发布

当前 CI 在 Linux 执行后端测试、前端测试与生产构建。Windows 启动检查和本机验证范围见 release-validation.md。未配置自动镜像发布或安装包上传；需要分发时按 distribution.md 构建。

不要提交 `.env.dev`、`.env.user`、运行目录、私人论文库、日志或真实赛题数据。保留第三方许可与来源锁文件。

### Linux 集成测试依赖

测试包含真实 Pandoc 转换、中文绘图和桌面启动。Ubuntu 需先安装 `fonts-noto-cjk` 和 `xvfb`；`pypandoc-binary` 由锁定的 Python 依赖提供。缺少中文字体时，绘图会明确失败，避免生成缺字图片。
