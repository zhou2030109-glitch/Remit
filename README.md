<div align="center">
  <img src="./assets/remit-icon.png" alt="Remit 标志" width="140" />
  <h1>Remit</h1>
  <p><strong>本地优先、可检查、可恢复的数学建模工作台</strong></p>
  <p>让 Agent 像一支数模队伍一样协作，让人始终握着题意、选型和交付的决定权。</p>
  <p>
    <a href="https://github.com/zhou2030109-glitch/Remit/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/zhou2030109-glitch/Remit/actions/workflows/ci.yml/badge.svg" /></a>
    <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" />
    <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42B883?logo=vuedotjs&logoColor=white" />
    <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
    <a href="./README_EN.md"><img alt="English" src="https://img.shields.io/badge/English-README-64748B" /></a>
  </p>
  <p>
    <a href="#项目亮点">项目亮点</a> ·
    <a href="#工作流程">工作流程</a> ·
    <a href="#快速开始">快速开始</a> ·
    <a href="#模型配置">模型配置</a> ·
    <a href="./docs/workflow.md">完整介绍</a> ·
    <a href="#star-history-">Star History</a> ·
    <a href="#加入交流群">社区交流</a>
  </p>
  <p>
    <a href="https://linux.do">
      <img src="https://img.shields.io/badge/LINUX-DO-FFB003.svg?logo=data:image/svg%2bxml;base64,DQo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCIgaGVpZ2h0PSIxMDAiPjxwYXRoIGQ9Ik00Ni44Mi0uMDU1aDYuMjVxMjMuOTY5IDIuMDYyIDM4IDIxLjQyNmM1LjI1OCA3LjY3NiA4LjIxNSAxNi4xNTYgOC44NzUgMjUuNDV2Ni4yNXEtMi4wNjQgMjMuOTY4LTIxLjQzIDM4LTExLjUxMiA3Ljg4NS0yNS40NDUgOC44NzRoLTYuMjVxLTIzLjk3LTIuMDY0LTM4LjAwNC0yMS40M1EuOTcxIDY3LjA1Ni0uMDU0IDUzLjE4di02LjQ3M0MxLjM2MiAzMC43ODEgOC41MDMgMTguMTQ4IDIxLjM3IDguODE3IDI5LjA0NyAzLjU2MiAzNy41MjcuNjA0IDQ2LjgyMS0uMDU2IiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZWNlY2VjO2ZpbGwtb3BhY2l0eToxIi8+PHBhdGggZD0iTTQ3LjI2NiAyLjk1N3EyMi41My0uNjUgMzcuNzc3IDE1LjczOGE0OS43IDQ5LjcgMCAwIDEgNi44NjcgMTAuMTU3cS00MS45NjQuMjIyLTgzLjkzIDAgOS43NS0xOC42MTYgMzAuMDI0LTI0LjM4N2E2MSA2MSAwIDAgMSA5LjI2Mi0xLjUwOCIgc3R5bGU9InN0cm9rZTpub25lO2ZpbGwtcnVsZTpldmVub2RkO2ZpbGw6IzE5MTkxOTtmaWxsLW9wYWNpdHk6MSIvPjxwYXRoIGQ9Ik03Ljk4IDcwLjkyNmMyNy45NzctLjAzNSA1NS45NTQgMCA4My45My4xMTNRODMuNDI2IDg3LjQ3MyA2Ni4xMyA5NC4wODZxLTE4LjgxIDYuNTQ0LTM2LjgzMi0xLjg5OC0xNC4yMDMtNy4wOS0yMS4zMTctMjEuMjYyIiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZjlhZjAwO2ZpbGwtb3BhY2l0eToxIi8+PC9zdmc+" alt="LINUX DO" />
    </a>
  </p>
</div>

---

Remit 把题面理解、数据检查、文献研究、模型选择、代码执行、结果验证和论文写作组织成
一条带人工确认节点的多 Agent 工作流。它不只给出一段答案，而是把每一步产生的分析、
证据、代码、图表和交付物放进同一个项目工作区，方便检查、返修和恢复。

<p align="center">
  <img src="./assets/remit-workbench-overview.png" alt="Remit 数学建模工作台主页" width="1100" />
</p>
<p align="center"><sub>Remit 工作台主页：项目进度、待人工确认、运行状态和 Agent 协作链集中在同一页。</sub></p>

主页用来回答四个最直接的问题：现在做到哪一步了、哪些结果正在等人确认、执行环境是否
正常、四个核心 Agent 正在怎样协作。进入项目后，题目、数据、文献、模型、代码、结果和
论文分别有独立视图，不需要在一条很长的聊天记录里寻找产物。

> Remit 目前处于 `0.1.x` 阶段，接口与工作流仍可能调整。完整工作流需要模型接口，
> 部分步骤会真实执行模型生成的代码，请只在可信环境中使用并保留人工检查。

## 项目亮点

- **四个核心角色分工**：Coordinator 忠实读题，Modeler 设计与复核模型，Coder 真实运行
  代码，Writer 只使用通过检查的结果写论文；
- **从数据和文献回头校正题意**：先扫描附件、检索开放文献并提取方法卡，再修正逐题理解，
  避免从错误前提出发一路跑到底；
- **候选模型必须下场比较**：每问先安排包含 baseline 的 Pilot，在相同数据划分和指标下
  真实试跑，再从跑通的候选中定案；
- **证据链贯穿交付**：方法推荐、文献采用、代码输出、图表、论文数字和质量报告能够相互
  对照，不把“模型说过”当成计算证据；
- **人工节点真正可退回**：题意、选型、Pilot、各小问和终稿都能暂停审批，带着累计意见
  返回具体节点，而不是整项任务重来；
- **本地优先且可恢复**：每个任务拥有独立目录、检查点和审批历史；MATLAB 优先、Python
  备用，也可以选择 E2B 沙箱；
- **模型接入可组合**：兼容 OpenAI Chat/Responses、Anthropic 和 Gemini，各角色可独立选择
  供应商、模型、上下文和推理强度。

## 工作流程

<p align="center">
  <a href="./assets/remit-workflow-overview.svg">
    <img src="./assets/remit-workflow-overview.svg" alt="Remit 从赛题上传到人工验收的横向工作流程" width="1100" />
  </a>
</p>

每个阶段都会把产物写入当前任务目录。审核通过后继续，发现问题时可以退回相应节点；
中断后则从检查点恢复。想了解题面校正、三级方法检索、模型评审组、Pilot 和终稿门禁的
完整细节，请阅读 [Remit 项目介绍](docs/workflow.md)；模块边界见
[架构文档](docs/architecture.md)。

## 运行要求

- Windows 10/11（桌面启动器）、macOS 12+/Linux（脚本启动）或支持 Docker Compose 的系统；
- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)；
- Node.js 20+ 与 pnpm 10；
- 完整工作流需要 Redis。Windows 源码模式可使用仓库内置的 Redis 运行文件；
  macOS 通过 `brew install redis` 安装，Linux 使用发行版的软件包管理器；
  启动脚本会在 16379 端口拉起一个 Remit 专属实例，且不会接管外部 Redis。

模型调用会产生第三方 API 费用。部分工作流会执行模型生成的代码，请仅在可信本机环境
运行并检查输入数据。

## 快速开始

### Windows 源码模式

```powershell
git clone https://github.com/zhou2030109-glitch/Remit.git
cd Remit

Copy-Item backend/.env.example backend/.env.dev

cd backend
uv sync --frozen

cd ../frontend
pnpm install --frozen-lockfile

cd ..
./win_start.bat
```

访问 <http://127.0.0.1:15173>。后端 API 文档位于
<http://127.0.0.1:18000/docs>。运行 `win_stop.bat` 停止服务。

### macOS / Linux 源码模式

```bash
git clone https://github.com/zhou2030109-glitch/Remit.git
cd Remit

cp backend/.env.example backend/.env.dev

cd backend
uv sync --frozen

cd ../frontend
pnpm install --frozen-lockfile

cd ..
bash tools/start_services.sh
```

访问 <http://127.0.0.1:15173>。后端 API 文档位于
<http://127.0.0.1:18000/docs>。运行 `bash tools/stop_services.sh` 停止服务。
macOS 也可双击 `mac_start.command` / `mac_stop.command`。首次运行前请安装 Redis
（macOS：`brew install redis`；Linux：使用发行版的软件包管理器）和 `lsof`。
启动脚本会自动生成缺失的 `backend/.env.dev`；
`tools/start_services.sh --check` 可以随时校验启动依赖。

### Docker Compose

```bash
cp backend/.env.example backend/.env.dev
docker compose up --build
```

前端默认端口为 `15173`，后端为 `18000`，Redis 为 `16379`。

需要可直接运行的生产镜像或离线安装包时，见[发布包构建与使用](docs/distribution.md)。

## 模型配置

编辑本地的 `backend/.env.dev`。四个核心角色采用相同字段结构：

```dotenv
COORDINATOR_API_TYPE=openai-responses
COORDINATOR_API_KEY=your-key
COORDINATOR_MODEL=your-model
COORDINATOR_BASE_URL=https://your-provider.example/
COORDINATOR_MAX_TOKENS=8192
```

把 `COORDINATOR` 替换为 `MODELER`、`CODER`、`WRITER` 即可分别配置。完整字段见
[配置文档](docs/configuration.md)。不要提交任何 `.env` 文件或真实密钥。

## 合成示例

仓库只附带项目自写的社区降温合成数据，不包含第三方比赛题面或附件。可通过
`POST /example` 并传入 `{"example_id": "urban-cooling"}` 创建演示任务，也可以直接在
界面上传自己的题目和数据。

## 开发与验证

```bash
cd backend
uv run ruff check app tests
uv run pytest tests -q

cd ../frontend
pnpm run lint
pnpm run test
pnpm run build

cd ..
# 仓库级启动器与配置契约测试（Windows 使用 .venv\Scripts\python.exe）
backend/.venv/bin/python -m pytest tests -q
```

Windows 安装包可通过以下命令生成，默认产物位于当前用户本地应用数据目录下的
`Remit/build/output/RemitSetup.exe`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/package_win.ps1
```

## 项目结构

```text
backend/      FastAPI、工作流、Agent、模型接入与执行器
frontend/     Vue 3 + TypeScript 工作台
tools/        Windows 启动、打包工具与 Redis 运行文件
assets/       Remit 品牌资源
docs/         架构、配置与来源审计文档
tests/        仓库级启动器和配置契约测试
```

任务数据写入 `backend/project/work_dir/<task-id>/`，日志写入 `logs/`，两者均不应提交。

## 安全边界

Remit 面向可信的单用户本机环境，不具备公网多租户服务所需的认证、授权和执行隔离。
公开部署前必须补充安全边界。漏洞请按 [安全策略](SECURITY.md) 私下报告。

## 参与和许可证

欢迎提交 Issue 与 Pull Request。开始前请阅读 [贡献指南](CONTRIBUTING.md) 和
[社区行为准则](CODE_OF_CONDUCT.md)。Remit 自有源码与合成示例采用
[MIT License](LICENSE)；依赖和捆绑运行文件保留各自许可证，详见
[第三方声明](THIRD_PARTY_NOTICES.md)。

当前源码经过针对 MathModelAgent 的来源审计和独立实现整改；早期公开版本的来源事实不
因分支历史重建而改变。技术范围、残余分类和限制见 [NOTICE.md](NOTICE.md) 与
[来源审计](docs/originality-audit.md)。这些材料用于透明披露，不构成法律结论。

## Star History ⭐

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/zhou2030109-glitch/Remit/refs/heads/star-history/assets/star-history/star-history-dark.svg?v=2" />
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/zhou2030109-glitch/Remit/refs/heads/star-history/assets/star-history/star-history-light.svg?v=2" />
    <img alt="Remit Star History" src="https://raw.githubusercontent.com/zhou2030109-glitch/Remit/refs/heads/star-history/assets/star-history/star-history-light.svg?v=2" width="800" />
  </picture>
</p>

## 加入交流群

想交流 Remit 的使用、数学建模工作流或一起参与开发，可以扫码加入微信群
**Remit（数模 agent）**。欢迎分享建议、问题和实际使用体验。

<p align="center">
  <img src="./assets/remit-wechat-group.png" alt="Remit 数模 Agent 微信交流群二维码" width="360" />
</p>

> 微信群二维码有效期较短，当前图片标注为 9 月 4 日前有效；过期后会在仓库更新。
