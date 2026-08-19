---
description: 
globs: 
alwaysApply: false
---
# WebSocket 前后端交互规则

[backend/app/routers/ws.py](mdc:backend/app/routers/ws.py) 负责处理 WebSocket 连接，路由为 `/task/{task_id}`，用于前端与后端的实时消息通信。

- 连接建立后，后端会校验 `task_id` 是否存在于 Redis。
- 若存在，后端会订阅 Redis 频道 `task:{task_id}:messages`，并通过 WebSocket 向前端推送消息。
- 消息结构体依赖 [backend/app/schemas/response.py](mdc:backend/app/schemas/response.py) 中的 `AgentMessage`、`AgentType`、`SystemMessage`。
- 后端通过 `ws_manager.send_personal_message_json` 方法将消息以 JSON 格式推送给前端。
- 前端应监听 WebSocket 消息事件，解析 JSON 数据并进行相应处理。
- 断开连接或异常时，后端会关闭 WebSocket 并取消 Redis 订阅。

该机制实现了任务级别的实时消息推送，前端需根据消息结构体进行解析和展示。

---

## WebSocket API 接口文档

### 1. 连接接口
- **URL**: `/task/{task_id}`
- **协议**: WebSocket
- **方法**: `GET`（升级为 WebSocket）
- **路径参数**:
  - `task_id`：任务唯一标识符
- **连接流程**：
  1. 前端发起 WebSocket 连接：`ws://<host>/task/{task_id}`
  2. 后端校验 `task_id` 是否存在于 Redis。
  3. 校验通过后建立连接，否则关闭连接并返回 code=1008。

### 2. 消息推送
- **推送时机**：后端监听 Redis 频道 `task:{task_id}:messages`，有新消息时推送。
- **消息格式**：JSON，结构体定义见 [backend/app/schemas/response.py](mdc:backend/app/schemas/response.py)
- **示例消息**：
```json
{
  "type": "system", // 或 "agent"
  "content": "任务开始处理",
  "agent_type": "bot" // 可选，见 AgentType 定义
}
```
- **字段说明**：
  - `type`：消息类型（如 system/agent）
  - `content`：消息内容
  - `agent_type`：代理类型（可选）

### 3. 断开与异常
- **断开时机**：
  - 前端主动断开
  - 后端检测到异常或任务结束
- **异常处理**：
  - 若 `task_id` 不存在，后端关闭连接，code=1008，reason="Task not found"
  - 其他异常，后端推送 error 字段的 JSON 消息

### 4. 前端处理建议
- 监听 WebSocket 消息事件，按 type/agent_type 解析内容
- 断开时可自动重连或提示用户
- 错误消息应友好展示
