<div align="center">
  <img src="./assets/remit-icon.png" alt="Remit 标志" width="140" />
  <h1>Remit 2.0 — 数模 Agent</h1>
  <p><strong>本地优先、可检查、可恢复的数学建模工作台</strong></p>
  <p>让 Agent 像一支数模队伍一样协作，让人始终握着题意、选型和交付的决定权。</p>
  <p>
    <a href="https://github.com/zhou2030109-glitch/Remit/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/zhou2030109-glitch/Remit/actions/workflows/ci.yml/badge.svg" /></a>
    <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" />
    <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42B883?logo=vuedotjs&logoColor=white" />
    <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
    <a href="./README_EN.md"><img alt="English" src="https://img.shields.io/badge/English-README-64748B" /></a>
  </p>
  <p>
    <a href="#20-升级">2.0 升级</a> ·
    <a href="#快速启动docker">快速开始</a> ·
    <a href="./docs/upgrading-to-v2.md">旧版与升级</a> ·
    <a href="./docs/paper-quality.md">论文质量规则</a> ·
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

本地数学建模工作台：通过团队对话，让协调手、建模手、代码手和论文手协作完成题目分析、计算与论文写作。

[English](README_EN.md) · [安装与分发](docs/distribution.md) · [配置说明](docs/configuration.md) · [赛事适配](docs/competition-adapters.md)

## 2.0 升级

当前主分支为 **Remit 2.0.0**，整合新版团队对话、计算恢复、论文工作区与图文排版规则。
团团、灵灵、点点、墨墨分别负责协调、建模、代码和论文；原有 Remit 标志、社区与 Star History 保留。

- 对话集中呈现当前阶段、关键结果与异常，执行明细按需展开。
- 按小问开展探索实验，保存可复用检查点；区分运行、重试、待审核与失败。
- 建模结果交接到独立论文工作区，支持章节续写、LaTeX 编辑与 PDF 预览。
- 论文按证据选图，统一变量与向量字体、居中三线表、图幅与图号，并检查摘要和分页。
- Windows / macOS 原生安装包构建流程随源码提供，包含运行与论文工具。

[查看 2.0 变更](docs/releases/2.0.0.md) · [升级与回退](docs/upgrading-to-v2.md) ·
[浏览升级前源码](https://github.com/zhou2030109-glitch/Remit/tree/legacy/pre-2.0)

## 当前状态

这是持续开发中的源码版本。支持计划确认、附件与文件夹导入、分步执行、断点恢复、成果审核、LaTeX 编辑与 PDF 导出。探索实验按小问执行并保留已校验的中间结果。

模型可能生成错误代码，接口也可能超时；需要检查实际计算结果并完成用户验收。软件测试通过不代表某道赛题已求解正确，也不保证比赛格式全部合规。

## 桌面安装

2.0 源码已发布，Windows x64、Mac Apple 芯片与 Intel 安装包由原生构建流程生成。
[Releases](https://github.com/zhou2030109-glitch/Remit/releases/tag/v2.0.0) 中只有出现对应的安装附件后才可下载；
构建和安装验证未通过时不会上传该批安装包。旧预览包不等于 2.0。
安装包内置计算与论文工具，首次打开仍需填写自己的模型服务信息。详细范围见 [桌面分发说明](docs/desktop-distribution.md)。

## 快速启动：Docker

安装 Git 和支持 Linux 容器的 Docker Compose，在终端运行：

```sh
git clone https://github.com/zhou2030109-glitch/Remit.git
cd Remit
docker compose -f docker-compose.release.yml up -d --build
```

打开 <http://localhost:18000>，在界面里的模型连接设置中填写自己的 API 地址、密钥、模型名和协议。首次构建包含科学计算库、中文字体和 LaTeX，下载量较大。模型服务费用由自己的供应商收取。

容器默认使用 Python 计算。配置与任务保存在命名数据卷中，`docker compose -f docker-compose.release.yml down` 停止服务并保留数据；`down -v` 会删除这些数据。

## Windows 源码启动

需要 Python 3.12、uv、Node.js 24、pnpm 10。仓库包含 Windows 启动器所需的 Redis 运行文件及许可证。

```powershell
git clone https://github.com/zhou2030109-glitch/Remit.git
cd Remit
cd backend
uv sync --locked
Copy-Item .env.example .env.dev
cd ../frontend
pnpm install --frozen-lockfile
Copy-Item .env.example .env.development
cd ..
.\win_start.bat
```

打开 <http://localhost:15173>。用 `win_stop.bat` 停止本项目服务。`win_start.bat --check` 检查启动依赖。

- 模型密钥在界面保存到本机配置，仓库不提供密钥。
- MATLAB 可选，需要自行安装并具备可用许可证；默认优先 MATLAB，不可用时按配置回退 Python。可在 `backend/.env.dev` 设置 `CODE_EXECUTION_BACKEND=python`。
- 本机导出论文 PDF 需安装 XeLaTeX（TeX Live 或 MiKTeX）与中文字体；Docker 方案已包含这些组件。
- macOS/Linux 源码部署另需安装 Redis，参考 [开发说明](docs/development.md)。

## 第一次使用

1. 配置四个角色的模型连接，检查连接状态。
2. 新建项目，选择赛事并导入题面和数据。可先使用 `backend/app/example/urban_cooling/` 中的合成小例子。
3. 与协调手确认题意和计划，再启动计算。方案、关键结果与论文均需检查。
4. 在“文件与结果”查看可追溯产物，在“论文”编辑、编译并导出。

赛事适配包含国赛、华为杯、美赛、华数杯等配置和开源基础技能；当届规则以主办方文件为准。详见 [赛事适配范围](docs/competition-adapters.md)。

## 仓库内容

```text
backend/app/     后端、角色、执行器和赛事技能
frontend/src/    对话、成果阅读与论文编辑界面
backend/tests/  后端回归测试
frontend/tests/ 前端行为测试
tests/          启动器与安装契约测试
tools/          启动、打包与可选资料库工具
docs/           使用与开发文档
```

不包含本机密钥、真实赛题附件、对话与运行记录、个人论文库、截图、虚拟环境或构建产物。可选论文库默认为空；通用写作技能与有许可的开源技能仍可使用。

## 测试

```sh
cd backend
uv run pytest tests -q
cd ../frontend
pnpm test
pnpm build
```

更多验证范围见 [发布验证](docs/release-validation.md)。本地执行模型生成的代码会使用本机权限；请在可信本机使用，处理不可信代码时选择隔离环境。该版本不提供多用户身份认证，不应直接暴露为公共网站。

## 许可与来源

Remit 自有代码使用 [MIT](LICENSE)。第三方依赖、Windows Redis 运行库和导入技能保留各自许可。项目历史来源及适用范围请同时阅读 [NOTICE](NOTICE.md)、[第三方声明](THIRD_PARTY_NOTICES.md) 和 [来源审计](docs/originality-audit.md)。

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
**Remit（数模 Agent）**。欢迎分享建议、问题和实际使用体验。

<p align="center">
  <a href="./assets/remit-wechat-group-20260929.png">
    <img src="./assets/remit-wechat-group-20260929.png" alt="Remit 数模 Agent 微信交流群二维码，2026 年 10 月 6 日前有效" width="360" />
  </a>
</p>

> 二维码更新于 2026 年 9 月 29 日，当前图片标注为 10 月 6 日前有效。点击图片可查看原图；
> 如二维码失效，可提交 Issue 提醒更新。
