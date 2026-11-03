# 桌面安装与构建

## 使用

**Windows 最新候选包（2026-10-03）：[前往下载](https://github.com/zhou2030109-glitch/Remit/releases/tag/candidate-20261003-7407a60)。** 包含本轮优化和 CI 修复，源码检查及包内运行验证通过；实际安装、升级、卸载和独立用户电脑验收仍待完成。程序内版本号仍为 2.0.1，请按候选文件名与校验值区分。

从 [GitHub Releases](https://github.com/zhou2030109-glitch/Remit/releases) 选择与电脑相符的安装包；没有附件的版本还不能下载。

- Windows x64：运行 Setup.exe。安装器会准备 Microsoft C++ 运行库，需要管理员确认。保留默认英文安装目录，避免 TeX Live 的非 ASCII 安装路径限制；项目目录和用户名可以包含中文。
- macOS 15+：Apple 芯片选 arm64，Intel 选 x64。打开 DMG，将 Remit 拖入 Applications，再打开 Remit。
- 工作台在默认浏览器打开，原有 Remit 图标显示在托盘或菜单栏，可以从菜单打开项目文件夹或退出。退出前应保存进度。
- 当前预览包没有发布者签名或 Apple 公证，操作系统可能要求额外确认。不要关闭系统安全防护。

包内包含 Python 3.12、NumPy/Pandas/SciPy/scikit-learn/XGBoost/SHAP 等计算库、中文绘图字体、Pandoc、XeLaTeX。用户无需另装 Python、Node、pnpm 或 TeX。包中保留第三方许可证和依赖版本清单。

需要自己的模型服务地址、模型名称和密钥，模型调用需联网。MATLAB 及工具箱不随包提供，需用户合法安装；默认计算后端为 Python。PyTorch、外部嵌入模型及 GPU 驱动不属于基础包，特定算法仍可能有额外依赖。不同硬件、模型和随机算法不能保证相同耗时或逐字相同结果。

## 数据与升级

Windows 使用 `%LOCALAPPDATA%\Remit\Desktop`；Mac 使用 `~/Library/Application Support/Remit`。
其中 `.env.user` 保存模型设置，`project/work_dir` 保存项目，`logs` 保存诊断记录。程序目录不保存用户项目。
安装包不含开发者的密钥、私有论文、附件或历史任务。升级前退出 Remit，替换程序后复用数据目录；卸载保留数据。备份请先退出，再复制整个用户数据目录。

启动器自动选择可用本地端口，避免抢占开发环境或另一服务。重复启动复用当前工作台。启动失败会提示日志位置。

## 开发者构建

构建机需要 Git、uv、Node 24、pnpm 10；Mac 还需要 Xcode Command Line Tools。

```sh
cd frontend
pnpm install --frozen-lockfile
cd ..
python tools/build_desktop.py --build-root /absolute/ascii/build-directory
```

Windows 可用 `E:/RemitDesktopBuild`。目录必须独立于用户数据。首次使用空目录，中断后加 `--reuse`；脚本不会自动删除原目录。依赖包下载校验 SHA-256，Python 包按 uv.lock 的哈希安装。

`.github/workflows/desktop.yml` 通过手动触发或数字版本标签（如 `v2.0.0`）在三种原生系统分别构建，普通源码推送只运行代码测试。只有三个平台构建及安装验证均通过后，才向对应 Release 上传安装包；源码 Release 可以先出现。没有签名凭据时仅做 macOS 运行所需的 ad-hoc 签名，不宣称获得发布者验证。

验证使用包内 Python，移除开发机工具路径，真实执行计算、中文绘图、Pandoc 转换、三种模板 PDF 编译与渲染，另启动独立 API / 静态前端。报告记录在构建目录 `reports/` 并作为 CI 附件保留。此检查不调用付费模型，也不验证用户的赛题结论。
