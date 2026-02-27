# -*- coding: utf-8 -*-
"""
云湖 Webhook 服务端
接收云湖平台推送的事件，并分发给注册的处理器
"""
import asyncio
import logging
import json
import time
from typing import Callable, List, Optional
from aiohttp import web
from models import YunhuEvent, parse_event

logger = logging.getLogger("yunhu.webhook")


class YunhuWebhook:
    """
    云湖 Webhook 接收服务

    用法:
        webhook = YunhuWebhook(host="0.0.0.0", port=8080, path="/webhook")

        @webhook.on_message
        async def handle(event: YunhuEvent):
            print(event.message.text)

        await webhook.start()
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        path: str = "/webhook",
    ):
        self.host = host
        self.port = port
        self.path = path

        self._handlers: List[Callable] = []
        self._event_type_handlers: dict = {}
        self._runner: Optional[web.AppRunner] = None
        self._app: Optional[web.Application] = None

        # 用于 SDK 内部消费事件的队列（供 WS 桥接等使用）
        self.event_queue: asyncio.Queue = asyncio.Queue()

        # 统一事件日志（最近 200 条，供 WebUI 显示）
        # 每条格式:
        # {
        #   "time": int(ms),
        #   "direction": "recv" | "send" | "error",
        #   "type": str,      # 事件类型 or 动作 or 错误类型
        #   "summary": str,   # 一行摘要
        #   "data": dict,     # 原始数据（recv）或指令详情（send）
        # }
        self._event_log: list = []
        self._max_log = 200

    # 日志追加

    def append_log(self, direction: str, type_: str, summary: str, data: dict = None):
        """追加一条统一日志记录"""
        entry = {
            "time": int(time.time() * 1000),
            "direction": direction,   # recv / send / error
            "type": type_,
            "summary": summary,
            "data": data or {},
        }
        self._event_log.append(entry)
        if len(self._event_log) > self._max_log:
            self._event_log.pop(0)

    # 注册处理器

    def on_event(self, event_type: str = None):
        """
        注册事件处理器装饰器

        @webhook.on_event()                            # 所有事件
        @webhook.on_event("message.receive.normal")    # 指定类型
        async def handler(event: YunhuEvent):
            ...
        """
        def decorator(func: Callable):
            if event_type:
                self._event_type_handlers.setdefault(event_type, []).append(func)
            else:
                self._handlers.append(func)
            return func
        return decorator

    def on_message(self, func: Callable):
        """快捷装饰器：普通消息事件"""
        self._event_type_handlers.setdefault("message.receive.normal", []).append(func)
        return func

    def on_command(self, func: Callable):
        """快捷装饰器：指令消息事件"""
        self._event_type_handlers.setdefault("message.receive.instruction", []).append(func)
        return func

    def on_button(self, func: Callable):
        """快捷装饰器：按钮汇报事件"""
        self._event_type_handlers.setdefault("button.report.inline", []).append(func)
        return func

    # HTTP 处理

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        try:
            body = await request.text()
            data = json.loads(body)
        except Exception as e:
            logger.warning(f"解析请求体失败: {e}")
            self.append_log("error", "parse_error", f"请求体解析失败: {e}", {"raw": body[:500] if 'body' in dir() else ""})
            return web.json_response({"code": 400, "msg": "invalid json"}, status=400)

        logger.debug(f"收到事件: {data}")

        event = parse_event(data)
        if event:
            # 构造摘要
            summary = _make_recv_summary(event, data)
            self.append_log("recv", event.event_type, summary, data)

            # 放入队列供外部消费
            await self.event_queue.put(event)
            # 触发注册的处理器
            asyncio.create_task(self._dispatch(event))
        else:
            self.append_log("error", "parse_error", "事件解析失败（格式不符）", data)

        return web.json_response({"code": 1, "msg": "ok"})

    async def _dispatch(self, event: YunhuEvent):
        """分发事件到所有匹配的处理器"""
        handlers = self._handlers.copy()
        handlers += self._event_type_handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"事件处理器异常: {e}", exc_info=True)
                self.append_log("error", "handler_error", f"处理器异常: {e}", {"event_type": event.event_type})

    # 启动 / 停止 

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post(self.path, self._handle_webhook)
        return app

    async def start(self):
        """启动 Webhook 服务（非阻塞，返回后服务在后台运行）"""
        self._app = self.build_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"云湖 Webhook 已启动: http://{self.host}:{self.port}{self.path}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
            logger.info("云湖 Webhook 已停止")

    async def run_forever(self):
        """启动并阻塞运行"""
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()

    def get_event_log(self, direction: str = None) -> list:
        """
        获取事件日志（供 WebUI 使用）
        direction: None=全部, "recv", "send", "error"
        """
        logs = list(reversed(self._event_log))
        if direction:
            logs = [l for l in logs if l["direction"] == direction]
        return logs

    def clear_event_log(self):
        self._event_log.clear()


# 工具函数

def _make_recv_summary(event, data: dict) -> str:
    """为接收事件生成一行摘要"""
    et = event.event_type
    if event.sender:
        name = event.sender.senderNickname or event.sender.senderId
    else:
        name = data.get("userId", "—")

    if "message" in et and event.message:
        text = event.message.text
        ct = event.message.contentType
        if ct == "text":
            return f"{name}: {text[:60]}"
        else:
            return f"{name} 发送了 {ct} 消息"
    elif et == "button.report.inline":
        return f"用户 {event.button_user_id} 点击按钮: {event.button_value[:40]}"
    else:
        return et