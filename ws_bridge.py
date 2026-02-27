# -*- coding: utf-8 -*-
"""
云湖 WebSocket 桥接服务
将 Webhook 收到的事件实时推送给已连接的客户端
"""
import asyncio
import base64
import json
import logging
import tempfile
import os
from typing import Set, Optional

from aiohttp import web, WSMsgType

logger = logging.getLogger("yunhu.ws_bridge")


class YunhuWSBridge:
    """
    WebSocket 桥接服务

    将 YunhuWebhook 收到的事件推送给所有已连接的 WS 客户端。
    同时接收客户端发来的"发送消息"指令，调用 YunhuClient 执行。

    用法:
        bridge = YunhuWSBridge(webhook, client, path="/ws")
        bridge.attach(app)   # 挂载到已有 aiohttp.Application
    """

    def __init__(self, webhook, client, path: str = "/ws", token: str = ""):
        """
        :param webhook: YunhuWebhook 实例（事件来源 & 日志）
        :param client:  YunhuClient  实例（用于执行发送指令）
        :param path:    WebSocket 挂载路径
        :param token:   可选鉴权 token，非空时客户端握手须带 ?token=xxx
        """
        self.webhook = webhook
        self.client = client
        self.path = path
        self.auth_token = token

        self._connections: Set[web.WebSocketResponse] = set()
        self._broadcast_task: Optional[asyncio.Task] = None

    # 挂载

    def attach(self, app: web.Application):
        """将 WS 路由挂载到已有的 aiohttp Application"""
        app.router.add_get(self.path, self._ws_handler)
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)

    async def _on_startup(self, app):
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        logger.info(f"云湖 WS 桥接已启动: {self.path}")

    async def _on_cleanup(self, app):
        if self._broadcast_task:
            self._broadcast_task.cancel()
        for ws in list(self._connections):
            await ws.close()

    # WS 处理

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        # 鉴权
        if self.auth_token:
            token = request.query.get("token", "")
            if token != self.auth_token:
                raise web.HTTPUnauthorized(text="Invalid WS token")

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._connections.add(ws)
        logger.info(f"新 WS 连接: {request.remote}，当前连接数: {len(self._connections)}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_client_msg(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._connections.discard(ws)
            logger.info(f"WS 连接断开: {request.remote}，剩余连接数: {len(self._connections)}")

        return ws

    async def _handle_client_msg(self, raw: str):
        """
        处理来自客户端的指令，格式：
        {
            "action": "send_text" | "send_markdown" | "send_image" |
                      "send_file" | "send_video" | "recall",
            "recv_id": "xxx",
            "recv_type": "user" | "group",
            "content": "...",          # send_text / send_markdown
            "image_key": "xxx",        # send_image
            "file_key":  "xxx",        # send_file
            "video_key": "xxx",        # send_video
            "msg_id": "xxx",           # recall
            "chat_id": "xxx",          # recall
            "chat_type": "xxx",        # recall
            "parent_id": "",           # 可选，回复消息
            "upload_path": "xxx",      # 可选，本地文件路径，自动上传
        }
        """
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"WS 消息解析失败: {e}")
            self.webhook.append_log("error", "ws_parse_error", f"WS 指令解析失败: {e}", {"raw": raw[:200]})
            return

        action = data.get("action", "")
        recv_id = data.get("recv_id", "")
        recv_type = data.get("recv_type", "user")
        parent_id = data.get("parent_id", "")

        logger.info(f"[WS] 收到指令 action={action!r} recv_id={recv_id!r} recv_type={recv_type!r}")

        try:
            if action == "send_text":
                resp = await self.client.send_text(recv_id, recv_type, data["content"], parent_id)
                summary = f"→ {recv_type}:{recv_id} [{resp.code}] {data['content'][:50]}"
                if resp.code == 1:
                    self.webhook.append_log("send", "send_text", summary, {
                        "recv_id": recv_id, "recv_type": recv_type,
                        "content": data["content"], "resp_code": resp.code, "resp_msg": resp.msg
                    })
                else:
                    self.webhook.append_log("error", "send_text_fail",
                        f"发送文本失败 code={resp.code} {resp.msg}", {
                            "recv_id": recv_id, "content": data["content"],
                            "resp_code": resp.code, "resp_msg": resp.msg
                        })
                logger.info(f"[WS] send_text 返回 code={resp.code} msg={resp.msg!r}")

            elif action == "send_markdown":
                resp = await self.client.send_markdown(recv_id, recv_type, data["content"], parent_id)
                summary = f"→ {recv_type}:{recv_id} [{resp.code}] Markdown({len(data['content'])}字)"
                if resp.code == 1:
                    self.webhook.append_log("send", "send_markdown", summary, {
                        "recv_id": recv_id, "recv_type": recv_type,
                        "content": data["content"][:200], "resp_code": resp.code
                    })
                else:
                    self.webhook.append_log("error", "send_markdown_fail",
                        f"发送 Markdown 失败 code={resp.code} {resp.msg}", {
                            "recv_id": recv_id, "resp_code": resp.code, "resp_msg": resp.msg
                        })

            elif action == "send_image":
                image_key = data.get("image_key", "")
                if not image_key and data.get("image_data"):
                    # base64 编码的文件内容（来自远程 AstrBot Docker 节点）
                    raw_bytes = base64.b64decode(data["image_data"])
                    filename = data.get("filename", "image.png")
                    resp = await self.client.upload_image_bytes(raw_bytes, filename)
                    if resp.ok and resp.data:
                        image_key = resp.data.get("imageKey", "")
                    else:
                        self.webhook.append_log("error", "send_image_fail",
                            f"Base64 图片上传失败: code={resp.code} {resp.msg}",
                            {"resp_code": resp.code, "resp_msg": resp.msg})
                elif not image_key and data.get("download_url"):
                    # 远程 HTTP URL：在 SDK 侧下载再上传
                    tmp_path = await _download_tmp(data["download_url"])
                    if tmp_path:
                        resp = await self.client.upload_image(tmp_path)
                        os.unlink(tmp_path)
                        if resp.ok and resp.data:
                            image_key = resp.data.get("imageKey", "")
                elif not image_key and data.get("upload_path"):
                    resp = await self.client.upload_image(data["upload_path"])
                    if resp.ok and resp.data:
                        image_key = resp.data.get("imageKey", "")
                if image_key:
                    resp = await self.client.send_image(recv_id, recv_type, image_key, parent_id)
                    summary = f"→ {recv_type}:{recv_id} 图片 [{resp.code}]"
                    if resp.code == 1:
                        self.webhook.append_log("send", "send_image", summary, {"recv_id": recv_id, "image_key": image_key})
                    else:
                        self.webhook.append_log("error", "send_image_fail", f"发送图片失败: {resp.msg}", {"resp_code": resp.code, "resp_msg": resp.msg})

            elif action == "send_file":
                file_key = data.get("file_key", "")
                if not file_key and data.get("file_data"):
                    raw_bytes = base64.b64decode(data["file_data"])
                    filename = data.get("filename", "file.bin")
                    fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(filename)[-1] or ".bin")
                    with os.fdopen(fd, "wb") as f:
                        f.write(raw_bytes)
                    resp = await self.client.upload_file(tmp_path)
                    os.unlink(tmp_path)
                    if resp.ok and resp.data:
                        file_key = resp.data.get("fileKey", "")
                    else:
                        self.webhook.append_log("error", "send_file_fail",
                            f"Base64 文件上传失败: code={resp.code} {resp.msg}",
                            {"resp_code": resp.code, "resp_msg": resp.msg})
                elif not file_key and data.get("download_url"):
                    tmp_path = await _download_tmp(data["download_url"])
                    if tmp_path:
                        resp = await self.client.upload_file(tmp_path)
                        os.unlink(tmp_path)
                        if resp.ok and resp.data:
                            file_key = resp.data.get("fileKey", "")
                elif not file_key and data.get("upload_path"):
                    resp = await self.client.upload_file(data["upload_path"])
                    if resp.ok and resp.data:
                        file_key = resp.data.get("fileKey", "")
                if file_key:
                    resp = await self.client.send_file(recv_id, recv_type, file_key, parent_id)
                    summary = f"→ {recv_type}:{recv_id} 文件 [{resp.code}]"
                    if resp.code == 1:
                        self.webhook.append_log("send", "send_file", summary, {"recv_id": recv_id, "file_key": file_key})
                    else:
                        self.webhook.append_log("error", "send_file_fail", f"发送文件失败: {resp.msg}", {"resp_code": resp.code, "resp_msg": resp.msg})

            elif action == "send_video":
                video_key = data.get("video_key", "")
                if not video_key and data.get("video_data"):
                    raw_bytes = base64.b64decode(data["video_data"])
                    filename = data.get("filename", "video.mp4")
                    fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(filename)[-1] or ".mp4")
                    with os.fdopen(fd, "wb") as f:
                        f.write(raw_bytes)
                    resp = await self.client.upload_video(tmp_path)
                    os.unlink(tmp_path)
                    if resp.ok and resp.data:
                        video_key = resp.data.get("videoKey", "")
                    else:
                        self.webhook.append_log("error", "send_video_fail",
                            f"Base64 视频上传失败: code={resp.code} {resp.msg}",
                            {"resp_code": resp.code, "resp_msg": resp.msg})
                elif not video_key and data.get("download_url"):
                    tmp_path = await _download_tmp(data["download_url"])
                    if tmp_path:
                        resp = await self.client.upload_video(tmp_path)
                        os.unlink(tmp_path)
                        if resp.ok and resp.data:
                            video_key = resp.data.get("videoKey", "")
                elif not video_key and data.get("upload_path"):
                    resp = await self.client.upload_video(data["upload_path"])
                    if resp.ok and resp.data:
                        video_key = resp.data.get("videoKey", "")
                if video_key:
                    resp = await self.client.send_video(recv_id, recv_type, video_key, parent_id)
                    summary = f"→ {recv_type}:{recv_id} 视频 [{resp.code}]"
                    if resp.code == 1:
                        self.webhook.append_log("send", "send_video", summary, {"recv_id": recv_id, "video_key": video_key})
                    else:
                        self.webhook.append_log("error", "send_video_fail", f"发送视频失败: {resp.msg}", {"resp_code": resp.code, "resp_msg": resp.msg})

            elif action == "recall":
                resp = await self.client.recall_message(data["msg_id"], data["chat_id"], data["chat_type"])
                if resp.code == 1:
                    self.webhook.append_log("send", "recall", f"撤回消息 {data['msg_id']}", {"msg_id": data["msg_id"]})
                else:
                    self.webhook.append_log("error", "recall_fail", f"撤回失败: {resp.msg}", {"resp_code": resp.code, "resp_msg": resp.msg})

            else:
                logger.warning(f"未知 WS 指令: {action}")
                self.webhook.append_log("error", "unknown_action", f"未知指令: {action}", data)

        except Exception as e:
            logger.error(f"执行 WS 指令 {action} 失败: {e}", exc_info=True)
            self.webhook.append_log("error", f"{action}_exception", f"执行指令异常: {e}", {"action": action, "recv_id": recv_id})

    # 广播事件

    async def _broadcast_loop(self):
        """从 webhook 事件队列取事件，广播给所有 WS 连接"""
        while True:
            try:
                event = await self.webhook.event_queue.get()
                if not self._connections:
                    continue
                payload = json.dumps(event.raw, ensure_ascii=False)
                dead = set()
                for ws in list(self._connections):
                    try:
                        await ws.send_str(payload)
                    except Exception:
                        dead.add(ws)
                self._connections -= dead
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"广播事件异常: {e}", exc_info=True)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# 模块级工具函数

async def _download_tmp(url: str) -> Optional[str]:
    """下载远程 URL 到临时文件，返回临时文件路径；失败返回 None"""
    import aiohttp as _aiohttp
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(url, timeout=_aiohttp.ClientTimeout(total=30)) as resp:
                suffix = os.path.splitext(url.split("?")[0])[-1] or ".bin"
                fd, path = tempfile.mkstemp(suffix=suffix)
                with os.fdopen(fd, "wb") as f:
                    f.write(await resp.read())
        return path
    except Exception as e:
        logger.error(f"[云湖] 下载失败 {url}: {e}")
        return None