# ☁ 云湖机器人 SDK

基于 Python 的云湖开放平台机器人 SDK，提供 Webhook 事件接收、WebSocket 实时推送桥接、REST API 封装以及可视化 WebUI 管理界面，开箱即用。

---

## 功能特性

- **Webhook 服务**：接收云湖平台推送的消息与按钮事件，支持多处理器注册与事件类型过滤
- **WebSocket 桥接**：将 Webhook 事件实时转发给所有已连接的 WS 客户端，同时接收客户端发送消息的指令（支持 Token 鉴权）
- **完整 API 封装**（`YunhuClient`）：
  - 发送文本、Markdown、图片、文件、视频消息
  - 批量发送、编辑消息、撤回消息
  - 上传图片/文件/视频（支持本地路径、字节流、远程 URL、Base64）
  - 用户看板（设置、全局设置、取消）
  - 获取消息列表
- **WebUI 管理界面**：浏览器可视化操作，功能包括：
  - Token 配置与连接测试
  - 手动发送消息（文本 / Markdown）
  - 实时事件日志与系统运行日志（SSE 流）
  - 服务配置修改（端口、路径、密码等）与一键重启
  - 支持访问密码保护
- **统一事件日志**：最近 200 条收发事件记录，区分 `recv / send / error` 三种方向，WebUI 可筛选查看

---

## 文件结构

```
├── run.py          # 启动入口，同时拉起 Webhook、WS 桥接、WebUI
├── webhook.py      # Webhook 服务端，事件接收与分发
├── ws_bridge.py    # WebSocket 桥接，事件广播 + 指令下发
├── client.py       # 云湖 REST API 客户端（YunhuClient）
├── webui.py        # WebUI 管理界面（aiohttp + 内嵌 HTML）
├── models.py       # 数据模型（事件、消息、API 响应等）
└── yunhu_config.json  # 配置文件（首次启动自动生成）
```

---

## 环境要求

- Python **3.10+**（使用了 `tuple[bool, str]` 等新式类型标注）
- 依赖库：

```
aiohttp
asyncio
```

---

## 部署步骤

### 1. 获取代码

```bash
git clone https://github.com/Soffd/yunhubot-websdk.git
cd yunhubot-websdk
```

### 2. 安装依赖

推荐使用虚拟环境：

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows

pip install aiohttp
pip install asyncio
```

### 3. 配置 Token

首次运行时会自动生成 `yunhu_config.json`，也可提前手动创建：

```json
{
  "token": "你的云湖机器人Token",
  "webhook_host": "0.0.0.0",
  "webhook_port": 8080,
  "webhook_path": "/webhook",
  "ws_path": "/ws",
  "ws_token": "",
  "webui_host": "0.0.0.0",
  "webui_port": 8081,
  "webui_password": ""
}
```

### 4. 启动服务

```bash
python run.py
```

启动成功后终端显示：

```
============================================================
  ☁  云湖机器人 SDK 已启动
============================================================
  📡 Webhook    : http://0.0.0.0:8080/webhook
  🔌 WS 桥接    : ws://<your-ip>:8080/ws
  🖥  WebUI     : http://127.0.0.1:8081
  📄 配置文件   : yunhu_config.json
============================================================
  将 Webhook 地址填入云湖官网控制台完成配置
  Ctrl+C 停止服务
```

### 5. 填写 Webhook 地址

将 `http://<你的服务器IP>:8080/webhook` 填入 [云湖官网控制台](https://www.yhchat.com/control) 机器人配置消息订阅接口，保存即可开始接收消息。

---

## 配置说明

所有配置均可在 `yunhu_config.json` 中修改，也可通过 WebUI 界面实时修改后重启生效。**配置文件优先级高于命令行参数。**

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `token` | `""` | 云湖机器人 Token（必填） |
| `webhook_host` | `0.0.0.0` | Webhook 监听地址 |
| `webhook_port` | `8080` | Webhook 监听端口 |
| `webhook_path` | `/webhook` | Webhook 接收路径 |
| `ws_path` | `/ws` | WebSocket 桥接路径 |
| `ws_token` | `""` | WS 鉴权 Token，留空不鉴权 |
| `webui_host` | `0.0.0.0` | WebUI 监听地址 |
| `webui_port` | `8081` | WebUI 监听端口 |
| `webui_password` | `""` | WebUI 访问密码，留空不验证 |

也可通过命令行参数指定（配置文件不存在时生效）：

```bash
python run.py \
  --webhook-port 9000 \
  --webui-port 9001 \
  --webui-password mypassword \
  --ws-token mytoken \
  --config /path/to/config.json
```

---

## 用法示例

### 在自己的代码中使用 Webhook 处理器

```python
import asyncio
from webhook import YunhuWebhook
from client import YunhuClient
from models import YunhuEvent

webhook = YunhuWebhook(port=8080)
client = YunhuClient(token="你的Token")

@webhook.on_message
async def handle_message(event: YunhuEvent):
    text = event.message.text
    recv_id = event.sender.senderId
    await client.send_text(recv_id, "user", f"你说了：{text}")

@webhook.on_command
async def handle_command(event: YunhuEvent):
    print(f"收到指令：{event.message.commandName}")

@webhook.on_button
async def handle_button(event: YunhuEvent):
    print(f"用户 {event.button_user_id} 点击了按钮，值：{event.button_value}")

asyncio.run(webhook.run_forever())
```

### 主动发送消息

```python
client = YunhuClient(token="你的Token")

# 发送文本
await client.send_text("user_id", "user", "Hello!")

# 发送 Markdown
await client.send_markdown("group_id", "group", "## 标题\n内容")

# 上传并发送图片
resp = await client.upload_image("/path/to/image.png")
image_key = resp.data["imageKey"]
await client.send_image("user_id", "user", image_key)

# 撤回消息
await client.recall_message(msg_id, chat_id, "user")
```

### 通过 WebSocket 桥接发送消息（适合外部系统对接）

连接 `ws://<host>:8080/ws`（若设置了 ws_token 则加 `?token=xxx`），发送 JSON 指令：

```json
{
  "action": "send_text",
  "recv_id": "用户ID",
  "recv_type": "user",
  "content": "Hello from WS!"
}
```

```json
{
  "action": "send_image",
  "recv_id": "用户ID",
  "recv_type": "user",
  "download_url": "https://example.com/image.png"
}
```

支持的 `action` 类型：

| action | 说明 | 必填字段 |
|--------|------|---------|
| `send_text` | 发送文本 | `recv_id`, `recv_type`, `content` |
| `send_markdown` | 发送 Markdown | `recv_id`, `recv_type`, `content` |
| `send_image` | 发送图片 | `recv_id`, `recv_type` + (`image_key` / `image_data` / `download_url` / `upload_path` 之一) |
| `send_file` | 发送文件 | `recv_id`, `recv_type` + (`file_key` / `file_data` / `download_url` / `upload_path` 之一) |
| `send_video` | 发送视频 | `recv_id`, `recv_type` + (`video_key` / `video_data` / `download_url` / `upload_path` 之一) |
| `recall` | 撤回消息 | `msg_id`, `chat_id`, `chat_type` |

图片/文件/视频支持四种来源，优先级依次为：`*_key`（已有资源）→ `*_data`（Base64）→ `download_url`（远程下载）→ `upload_path`（本地路径）。

---

## WebUI 界面

浏览器访问 `http://127.0.0.1:8081`，若设置了 `webui_password` 则需先登录。

WebUI 提供以下功能：

- **状态面板**：当前 Token、WS 连接数、服务在线状态
- **连接测试**：一键验证 Token 是否有效
- **发送消息**：手动向指定用户/群组发送文本或 Markdown 消息
- **事件日志**：实时查看收发事件（支持按方向过滤、一键清空）
- **系统日志**：实时 SSE 流式查看服务运行日志
- **配置管理**：在线修改所有配置项，保存后可一键重启服务

---

## 后台运行（Linux 生产部署）

推荐使用 `systemd` 或 `screen`/`tmux` 管理进程。

**使用 systemd：**

```ini
# /etc/systemd/system/yunhu.service
[Unit]
Description=云湖机器人 SDK
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/project
ExecStart=/path/to/venv/bin/python run.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable yunhu
systemctl start yunhu
systemctl status yunhu
```

---

## 常见问题

**Q：Token 无效，code 返回 1003？**  
A：请检查 `yunhu_config.json` 中 token 字段是否正确填写，可在 WebUI 点击「连接测试」验证。

**Q：云湖平台推送失败，收不到消息？**  
A：确认服务器防火墙已开放 `webhook_port`（默认 8080），Webhook 地址填写的是公网 IP/域名而非 `0.0.0.0`。

**Q：WebSocket 客户端无法连接？**  
A：确认使用正确的路径（默认 `/ws`）和端口（与 webhook 同端口），若设置了 `ws_token` 需在 URL 中携带 `?token=xxx`。

**Q：WebUI 忘记密码怎么办？**  
A：直接编辑 `yunhu_config.json`，将 `webui_password` 清空后重启服务即可。
