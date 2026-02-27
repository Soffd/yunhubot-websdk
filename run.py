# -*- coding: utf-8 -*-
"""
云湖 SDK 启动入口
同时启动 Webhook 服务（接收消息）、WebSocket 桥接（推送事件）和 WebUI（管理界面）

配置优先级：配置文件 > 命令行参数 > 内置默认值
配置文件（yunhu_config.json）支持以下字段：
  token            - 机器人 Token
  webhook_host     - Webhook 监听地址（默认 0.0.0.0）
  webhook_port     - Webhook 监听端口（默认 8080）
  webhook_path     - Webhook 路径（默认 /webhook）
  ws_path          - WebSocket 桥接路径（默认 /ws）
  ws_token         - WebSocket 鉴权 Token（留空不鉴权）
  webui_host       - WebUI 监听地址（默认 0.0.0.0）
  webui_port       - WebUI 端口（默认 8081）
  webui_password   - WebUI 访问密码（留空不验证）
"""
import asyncio
import logging
import argparse
import json
import os
import sys
# 虚拟环境
# source /www/wwwroot/yunhuchat/venv/bin/activate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# 安装内存日志处理器（必须在 basicConfig 之后，以捕获启动阶段的日志）
from webui import install_log_handler
install_log_handler()

logger = logging.getLogger("yunhu")

DEFAULT_CONFIG = {
    "token": "",
    "webhook_host": "0.0.0.0",
    "webhook_port": 8080,
    "webhook_path": "/webhook",
    "ws_path": "/ws",
    "ws_token": "",
    "webui_host": "0.0.0.0",
    "webui_port": 8081,
    "webui_password": "",
}


def load_config(path: str) -> dict:
    """从 JSON 文件加载配置，缺失字段用默认值补全"""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            cfg.update(loaded)
            logger.info(f"已加载配置文件: {path}")
        except Exception as e:
            logger.warning(f"配置文件读取失败，使用默认值: {e}")
    else:
        logger.info(f"配置文件 {path} 不存在，将在首次保存时创建")
    return cfg


def save_default_config(path: str, cfg: dict):
    """如果配置文件不存在，保存一份默认配置"""
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            logger.info(f"已创建默认配置文件: {path}")
        except Exception as e:
            logger.warning(f"创建配置文件失败: {e}")


async def main(args):
    from webhook import YunhuWebhook
    from webui import YunhuWebUI
    from client import YunhuClient
    from ws_bridge import YunhuWSBridge

    # 读取配置（文件优先，命令行参数作为文件不存在时的备用默认值）
    config = load_config(args.config)

    token          = config.get("token", "")
    webhook_host   = config.get("webhook_host", args.webhook_host)
    webhook_port   = config.get("webhook_port", args.webhook_port)
    webhook_path   = config.get("webhook_path", args.webhook_path)
    ws_path        = config.get("ws_path", args.ws_path)
    ws_token       = config.get("ws_token", args.ws_token)
    webui_host     = config.get("webui_host", args.webui_host)
    webui_port     = config.get("webui_port", args.webui_port)
    webui_password = config.get("webui_password", args.webui_password)

    # 确保配置文件存在（首次启动时写入）
    save_default_config(args.config, config)

    # 创建核心组件
    webhook = YunhuWebhook(
        host=webhook_host,
        port=webhook_port,
        path=webhook_path,
    )
    client = YunhuClient(token=token)

    # 创建 WS 桥接
    bridge = YunhuWSBridge(
        webhook=webhook,
        client=client,
        path=ws_path,
        token=ws_token,
    )

    # 创建 WebUI
    webui = YunhuWebUI(
        webhook=webhook,
        bridge=bridge,
        config_path=args.config,
        webui_port=webui_port,
        webui_host=webui_host,
        password=webui_password,
    )

    # 5. 启动 webhook（先 build app，再 attach bridge，再 start）
    aiohttp_web = __import__("aiohttp").web
    webhook._app = webhook.build_app()
    bridge.attach(webhook._app)
    webhook._runner = aiohttp_web.AppRunner(webhook._app)
    await webhook._runner.setup()
    site = aiohttp_web.TCPSite(webhook._runner, webhook.host, webhook.port)
    await site.start()
    logger.info(f"云湖 Webhook 已启动: http://{webhook.host}:{webhook.port}{webhook.path}")

    await webui.start()

    ws_url = f"ws://<your-ip>:{webhook_port}{ws_path}"
    if ws_token:
        ws_url += f"?token={ws_token}"

    print("\n" + "=" * 60)
    print("  ☁  云湖机器人 SDK 已启动")
    print("=" * 60)
    print(f"  📡 Webhook    : http://{webhook_host}:{webhook_port}{webhook_path}")
    print(f"  🔌 WS 桥接    : {ws_url}")
    print(f"  🖥  WebUI     : http://127.0.0.1:{webui_port}")
    if webui_password:
        print(f"  🔒 WebUI 密码 : 已设置")
    if ws_token:
        print(f"  🔒 WS Token  : 已设置")
    print(f"  📄 配置文件   : {args.config}")
    print("=" * 60)
    print("  将 Webhook 地址填入云湖官网控制台完成配置")
    print("  Ctrl+C 停止服务\n")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("正在停止服务...")
    finally:
        await webhook.stop()
        await webui.stop()
        await client.close()
        logger.info("已停止")


def parse_args():
    parser = argparse.ArgumentParser(
        description="云湖机器人 SDK",
        epilog="配置文件中的值优先于命令行参数，可在 WebUI 中修改配置并重启生效。"
    )
    parser.add_argument("--webhook-host", default="0.0.0.0")
    parser.add_argument("--webhook-port", type=int, default=8080)
    parser.add_argument("--webhook-path", default="/webhook")
    parser.add_argument("--ws-path", default="/ws", help="WebSocket 桥接路径")
    parser.add_argument("--ws-token", default="", help="WebSocket 鉴权 token（可选）")
    parser.add_argument("--webui-host", default="0.0.0.0")
    parser.add_argument("--webui-port", type=int, default=8081)
    parser.add_argument("--webui-password", default="", help="WebUI 访问密码（留空则不验证）")
    parser.add_argument("--config", default="yunhu_config.json", help="配置文件路径")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass