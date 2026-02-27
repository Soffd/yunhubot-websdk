# -*- coding: utf-8 -*-
"""
云湖 SDK WebUI 管理界面：
  - 配置文件支持 webui_password / ws_token / 端口
  - 运行日志实时查看（SSE 流）
  - 重启服务按钮
  - 事件日志与系统日志合并展示
"""
import asyncio
import hashlib
import json
import logging
import os
import secrets
import sys
import time
from collections import deque
from typing import Optional
from aiohttp import web
from client import YunhuClient
from webhook import YunhuWebhook

logger = logging.getLogger("yunhu.webui")


# 内存日志处理器

class InMemoryLogHandler(logging.Handler):
    """将所有日志记录保存到内存环形缓冲区，供 WebUI 实时查看"""

    def __init__(self, maxlen: int = 500):
        super().__init__()
        self._records: deque = deque(maxlen=maxlen)
        self._waiters: list = []   # SSE 订阅者
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S"
        ))

    def emit(self, record: logging.LogRecord):
        line = self.format(record)
        entry = {
            "t": int(record.created * 1000),
            "level": record.levelname,
            "name": record.name,
            "msg": line,
        }
        self._records.append(entry)
        # 通知所有 SSE 等待者
        for q in list(self._waiters):
            try:
                q.put_nowait(entry)
            except Exception:
                pass

    def get_all(self) -> list:
        return list(self._records)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._waiters.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._waiters.remove(q)
        except ValueError:
            pass

    def clear(self):
        self._records.clear()


# 全局单例，在 run.py 里 attach 到 root logger
_log_handler = InMemoryLogHandler(maxlen=600)


def get_log_handler() -> InMemoryLogHandler:
    return _log_handler


def install_log_handler():
    """将内存日志处理器挂载到 root logger（在 run.py 中调用一次）"""
    root = logging.getLogger()
    if _log_handler not in root.handlers:
        root.addHandler(_log_handler)


# 登录页

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>云湖 SDK · 登录</title>
<style>
  :root{--bg:#0f1117;--surface:#1a1d27;--border:#2e3450;--accent:#6c8eff;--text:#e2e8f0;--text2:#94a3b8;--red:#f87171;--radius:12px}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Inter','PingFang SC',system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
  .box{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:40px;width:340px}
  .title{font-size:22px;font-weight:700;background:linear-gradient(135deg,#6c8eff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;text-align:center;margin-bottom:8px}
  .sub{text-align:center;color:var(--text2);font-size:13px;margin-bottom:28px}
  label{display:block;font-size:13px;color:var(--text2);margin-bottom:6px}
  input{width:100%;background:#22263a;border:1px solid var(--border);border-radius:8px;padding:11px 14px;color:var(--text);font-size:14px;outline:none;transition:border-color .2s}
  input:focus{border-color:var(--accent)}
  .btn{width:100%;margin-top:20px;padding:12px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-size:15px;font-weight:600;cursor:pointer;transition:background .2s}
  .btn:hover{background:#5a7bef}
  .err{color:var(--red);font-size:13px;margin-top:12px;text-align:center;display:none}
</style>
</head>
<body>
<div class="box">
  <div class="title">☁ 云湖 SDK</div>
  <div class="sub">请输入访问密码</div>
  <label>密码</label>
  <input type="password" id="pwd" placeholder="输入密码..." onkeydown="if(event.key==='Enter')login()">
  <button class="btn" onclick="login()">进入控制台</button>
  <div class="err" id="err">密码错误，请重试</div>
</div>
<script>
async function login(){
  const pwd=document.getElementById('pwd').value;
  const res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pwd})});
  const data=await res.json();
  if(data.ok)location.reload();
  else{document.getElementById('err').style.display='block';}
}
</script>
</body></html>"""

# 主界面

WEBUI_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>云湖 Bot SDK · 管理面板</title>
<style>
  :root{
    --bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2e3450;
    --accent:#6c8eff;--accent2:#a78bfa;--green:#34d399;--red:#f87171;
    --yellow:#fbbf24;--orange:#fb923c;--text:#e2e8f0;--text2:#94a3b8;--radius:12px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Inter','PingFang SC',system-ui,sans-serif;min-height:100vh}
  .layout{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
  .sidebar{background:var(--surface);border-right:1px solid var(--border);padding:24px 0;display:flex;flex-direction:column}
  .logo{padding:0 20px 24px;border-bottom:1px solid var(--border);margin-bottom:12px}
  .logo-title{font-size:19px;font-weight:700;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .logo-sub{font-size:11px;color:var(--text2);margin-top:2px}
  .nav-item{display:flex;align-items:center;gap:10px;padding:10px 20px;cursor:pointer;color:var(--text2);font-size:14px;transition:all .2s;border-left:3px solid transparent}
  .nav-item:hover{color:var(--text);background:var(--surface2)}
  .nav-item.active{color:var(--accent);background:rgba(108,142,255,.1);border-left-color:var(--accent)}
  .nav-icon{font-size:17px;width:20px}
  .status-dot{width:8px;height:8px;border-radius:50%;background:var(--red);margin-left:auto}
  .status-dot.online{background:var(--green);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
  .main{padding:28px 32px;overflow-y:auto;max-height:100vh}
  .page{display:none}
  .page.active{display:block}
  .page-title{font-size:22px;font-weight:700;margin-bottom:6px}
  .page-desc{color:var(--text2);font-size:13px;margin-bottom:24px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:22px;margin-bottom:18px}
  .card-title{font-size:14px;font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px}
  .form-group{margin-bottom:14px}
  label{display:block;font-size:12px;color:var(--text2);margin-bottom:5px}
  input,select,textarea{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:9px 13px;color:var(--text);font-size:14px;outline:none;transition:border-color .2s}
  input:focus,select:focus,textarea:focus{border-color:var(--accent)}
  textarea{resize:vertical;min-height:80px;font-family:monospace}
  .input-row{display:flex;gap:10px}
  .input-row input,.input-row select{flex:1}
  .btn{padding:9px 18px;border-radius:8px;border:none;cursor:pointer;font-size:13px;font-weight:500;transition:all .2s;display:inline-flex;align-items:center;gap:6px}
  .btn-primary{background:var(--accent);color:#fff}
  .btn-primary:hover{background:#5a7bef}
  .btn-danger{background:var(--red);color:#fff}
  .btn-danger:hover{background:#e05555}
  .btn-warn{background:var(--yellow);color:#111}
  .btn-warn:hover{background:#e5aa10}
  .btn-outline{background:transparent;border:1px solid var(--border);color:var(--text)}
  .btn-outline:hover{border-color:var(--accent);color:var(--accent)}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .badge{padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:3px}
  .badge-green{background:rgba(52,211,153,.15);color:var(--green)}
  .badge-red{background:rgba(248,113,113,.15);color:var(--red)}
  .badge-yellow{background:rgba(251,191,36,.15);color:var(--yellow)}
  .badge-blue{background:rgba(108,142,255,.15);color:var(--accent)}
  .alert{padding:11px 15px;border-radius:8px;font-size:13px;margin-bottom:14px;display:none}
  .alert.show{display:block}
  .alert-success{background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.3);color:var(--green)}
  .alert-error{background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.3);color:var(--red)}
  .alert-info{background:rgba(108,142,255,.1);border:1px solid rgba(108,142,255,.3);color:var(--accent)}
  .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:18px}
  .stat-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px}
  .stat-value{font-size:26px;font-weight:700}
  .stat-label{font-size:12px;color:var(--text2);margin-top:3px}
  .url-display{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:11px 15px;font-family:monospace;font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:12px;word-break:break-all;margin-bottom:10px}
  .spinner{width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;display:inline-block}
  @keyframes spin{to{transform:rotate(360deg)}}
  /* Event Log */
  .log-toolbar{display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap}
  .log-tabs{display:flex;gap:4px;flex:1;flex-wrap:wrap}
  .log-tab{padding:5px 14px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid var(--border);color:var(--text2);transition:all .2s;white-space:nowrap}
  .log-tab.active{color:#fff}
  .log-tab[data-tab="all"].active{background:var(--accent);border-color:var(--accent)}
  .log-tab[data-tab="recv"].active{background:#1a3a5c;border-color:var(--accent);color:var(--accent)}
  .log-tab[data-tab="send"].active{background:#1a3a2a;border-color:var(--green);color:var(--green)}
  .log-tab[data-tab="error"].active{background:#3a1a1a;border-color:var(--red);color:var(--red)}
  .event-log{background:var(--surface2);border-radius:8px;padding:10px;max-height:480px;overflow-y:auto;font-family:monospace;font-size:12px}
  .event-item{padding:8px 10px;border-radius:6px;margin-bottom:5px;border-left:3px solid var(--border);cursor:pointer;transition:opacity .15s}
  .event-item:hover{opacity:.85}
  .event-item.recv{border-left-color:var(--accent);background:rgba(108,142,255,.06)}
  .event-item.send{border-left-color:var(--green);background:rgba(52,211,153,.06)}
  .event-item.error{border-left-color:var(--red);background:rgba(248,113,113,.07)}
  .event-header{display:flex;align-items:center;gap:8px}
  .event-time{color:var(--text2);font-size:10px;white-space:nowrap}
  .event-type-tag{font-size:10px;padding:1px 7px;border-radius:10px;white-space:nowrap}
  .recv .event-type-tag{background:rgba(108,142,255,.2);color:var(--accent)}
  .send .event-type-tag{background:rgba(52,211,153,.2);color:var(--green)}
  .error .event-type-tag{background:rgba(248,113,113,.2);color:var(--red)}
  .event-summary{color:var(--text);margin-top:3px;font-size:12px;line-height:1.5}
  .event-detail{display:none;margin-top:8px;background:var(--bg);border-radius:6px;padding:10px;font-size:11px;color:var(--text2);white-space:pre-wrap;word-break:break-all;max-height:200px;overflow-y:auto}
  .event-item.expanded .event-detail{display:block}
  /* Run log terminal */
  .run-log-wrap{position:relative}
  .run-log{background:#0a0c12;border:1px solid var(--border);border-radius:8px;padding:12px 14px;height:520px;overflow-y:auto;font-family:'Fira Code','Cascadia Code',monospace;font-size:12px;line-height:1.65;color:#c9d1d9}
  .run-log-line{white-space:pre-wrap;word-break:break-all}
  .run-log-line.DEBUG{color:#64748b}
  .run-log-line.INFO{color:#c9d1d9}
  .run-log-line.WARNING{color:#fbbf24}
  .run-log-line.ERROR{color:#f87171}
  .run-log-line.CRITICAL{color:#f43f5e;font-weight:bold}
  .log-sse-dot{width:8px;height:8px;border-radius:50%;background:var(--red);display:inline-block;margin-right:6px}
  .log-sse-dot.live{background:var(--green);animation:pulse 1.5s infinite}
  .sep{border-top:1px solid var(--border);margin:18px 0}
  /* grid helpers */
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
  @media(max-width:900px){.grid-2,.grid-3{grid-template-columns:1fr}}
  @media(max-width:768px){.layout{grid-template-columns:1fr}.sidebar{display:none}}
</style>
</head>
<body>
<div class="layout">
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="logo">
      <div class="logo-title">☁ 云湖 SDK</div>
      <div class="logo-sub">Bot 管理控制台</div>
    </div>
    <div class="nav-item active" data-page="overview" onclick="nav(this)">
      <span class="nav-icon">📊</span> 概览
      <span class="status-dot" id="navDot"></span>
    </div>
    <div class="nav-item" data-page="config" onclick="nav(this)">
      <span class="nav-icon">⚙️</span> 配置
    </div>
    <div class="nav-item" data-page="webhook" onclick="nav(this)">
      <span class="nav-icon">🔗</span> Webhook / WS
    </div>
    <div class="nav-item" data-page="send" onclick="nav(this)">
      <span class="nav-icon">✉️</span> 发送消息
    </div>
    <div class="nav-item" data-page="events" onclick="nav(this)">
      <span class="nav-icon">📋</span> 事件日志
      <span id="errBadge" style="display:none;margin-left:auto;background:var(--red);color:#fff;font-size:10px;padding:1px 6px;border-radius:10px">!</span>
    </div>
    <div class="nav-item" data-page="runlog" onclick="nav(this)">
      <span class="nav-icon">🖥</span> 运行日志
    </div>
    <div style="margin-top:auto;padding:16px 20px;border-top:1px solid var(--border)">
      <button class="btn btn-warn" style="width:100%" onclick="restartService()">🔄 重启服务</button>
    </div>
  </aside>

  <!-- Main -->
  <main class="main">

    <!-- ── 概览 ── -->
    <div class="page active" id="page-overview">
      <div class="page-title">概览</div>
      <div class="page-desc">云湖机器人 SDK 运行状态</div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value" id="statStatus">—</div>
          <div class="stat-label">连接状态</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="statRecv">0</div>
          <div class="stat-label">已接收</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="statSent">0</div>
          <div class="stat-label">已发送</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="statErr">0</div>
          <div class="stat-label">错误数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="statWsConn">—</div>
          <div class="stat-label">WS 连接数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="statUptime">—</div>
          <div class="stat-label">运行时间</div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">🚀 快速开始</div>
        <p style="color:var(--text2);font-size:13px;line-height:1.9">
          1. 前往 <b style="color:var(--text)">配置</b> 页填入机器人 Token 并保存<br>
          2. 前往 <b style="color:var(--text)">Webhook / WS</b> 页，将 Webhook URL 填入云湖控制台<br>
          3. 在 <b style="color:var(--text)">事件日志</b> 页实时查看收发消息与报错<br>
          4. 在 <b style="color:var(--text)">运行日志</b> 页查看进程实时输出
        </p>
      </div>
      <div class="card">
        <div class="card-title">📡 最近事件</div>
        <div class="event-log" id="overviewLog" style="max-height:260px">
          <div style="color:var(--text2);text-align:center;padding:16px">暂无事件</div>
        </div>
      </div>
    </div>

    <!-- ── 配置 ── -->
    <div class="page" id="page-config">
      <div class="page-title">配置</div>
      <div class="page-desc">设置机器人参数，保存后重启生效（标★项需重启）</div>
      <div class="alert" id="configAlert"></div>

      <div class="card">
        <div class="card-title">🔑 机器人 Token</div>
        <div class="form-group">
          <label>Token（从云湖官网控制台获取）</label>
          <div class="input-row">
            <input type="password" id="inputToken" placeholder="输入 Bot Token...">
            <button class="btn btn-outline" onclick="toggleVis('inputToken')">👁</button>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-primary" onclick="saveToken()">💾 保存 Token</button>
          <button class="btn btn-outline" onclick="testToken()">🔍 测试连接</button>
        </div>
        <div id="testResult" style="margin-top:10px;display:none"></div>
      </div>

      <div class="card">
        <div class="card-title">🌐 端口 &amp; 路径 ★</div>
        <div class="grid-2">
          <div class="form-group">
            <label>Webhook 监听端口 ★</label>
            <input type="number" id="inputWebhookPort" placeholder="8080">
          </div>
          <div class="form-group">
            <label>Webhook 路径 ★</label>
            <input type="text" id="inputWebhookPath" placeholder="/webhook">
          </div>
          <div class="form-group">
            <label>WebSocket 路径 ★</label>
            <input type="text" id="inputWsPath" placeholder="/ws">
          </div>
          <div class="form-group">
            <label>WS 鉴权 Token（留空不验证）★</label>
            <div class="input-row">
              <input type="password" id="inputWsToken" placeholder="留空不鉴权">
              <button class="btn btn-outline" onclick="toggleVis('inputWsToken')">👁</button>
            </div>
          </div>
          <div class="form-group">
            <label>WebUI 端口 ★</label>
            <input type="number" id="inputWebuiPort" placeholder="8081">
          </div>
          <div class="form-group">
            <label>WebUI 访问密码（留空不验证）★</label>
            <div class="input-row">
              <input type="password" id="inputWebuiPassword" placeholder="留空不设密码">
              <button class="btn btn-outline" onclick="toggleVis('inputWebuiPassword')">👁</button>
            </div>
          </div>
        </div>
        <p style="color:var(--text2);font-size:12px;margin-bottom:12px">⚠ 标 ★ 项修改后需点击重启才能生效</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-primary" onclick="saveAllConfig()">💾 保存配置</button>
          <button class="btn btn-warn" onclick="restartService()">🔄 保存并重启</button>
        </div>
      </div>
    </div>

    <!-- ── Webhook / WS ── -->
    <div class="page" id="page-webhook">
      <div class="page-title">Webhook / WS</div>
      <div class="page-desc">接入地址与测试</div>
      <div class="card">
        <div class="card-title">🔗 Webhook 地址</div>
        <p style="color:var(--text2);font-size:12px;margin-bottom:12px">将此地址填入云湖控制台「消息订阅 URL」</p>
        <div class="url-display">
          <span id="webhookUrl">加载中...</span>
          <button class="btn btn-outline" style="white-space:nowrap;flex-shrink:0" onclick="copyText('webhookUrl')">📋 复制</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">🔌 WebSocket 地址</div>
        <p style="color:var(--text2);font-size:12px;margin-bottom:12px">客户端连接此地址接收事件 / 发送指令</p>
        <div class="url-display">
          <span id="wsUrl">加载中...</span>
          <button class="btn btn-outline" style="white-space:nowrap;flex-shrink:0" onclick="copyText('wsUrl')">📋 复制</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">🧪 模拟 Webhook 推送</div>
        <p style="color:var(--text2);font-size:12px;margin-bottom:12px">手动注入测试事件，验证接收流程</p>
        <textarea id="testPayload" style="height:140px">{
  "version": "1.0",
  "header": {
    "eventId": "test_001",
    "eventTime": 1700000000000,
    "eventType": "message.receive.normal"
  },
  "event": {
    "sender": {"senderId": "user001", "senderType": "user", "senderUserLevel": "member", "senderNickname": "测试用户"},
    "chat": {"chatId": "group001", "chatType": "group"},
    "message": {"msgId": "msg001", "parentId": "", "sendTime": 1700000000000, "chatId": "group001", "chatType": "group", "contentType": "text", "content": {"text": "你好！"}, "commandId": 0, "commandName": ""}
  }
}</textarea>
        <button class="btn btn-outline" style="margin-top:10px" onclick="sendTestEvent()">🚀 注入测试事件</button>
        <div id="testEventResult" style="margin-top:8px;font-size:12px;display:none"></div>
      </div>
    </div>

    <!-- ── 发送消息 ── -->
    <div class="page" id="page-send">
      <div class="page-title">发送消息</div>
      <div class="page-desc">通过 API 直接向用户或群发送消息</div>
      <div class="alert" id="sendAlert"></div>
      <div class="card">
        <div class="card-title">✉️ 发送</div>
        <div class="form-group">
          <label>接收者 ID（用户 ID 或群 ID）</label>
          <input type="text" id="sendRecvId" placeholder="如：7058262">
        </div>
        <div class="input-row" style="margin-bottom:14px">
          <div>
            <label>接收者类型</label>
            <select id="sendRecvType">
              <option value="user">user（私聊）</option>
              <option value="group">group（群聊）</option>
            </select>
          </div>
          <div>
            <label>消息类型</label>
            <select id="sendContentType">
              <option value="text">文本</option>
              <option value="markdown">Markdown</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>消息内容</label>
          <textarea id="sendContent" placeholder="输入消息内容..."></textarea>
        </div>
        <button class="btn btn-primary" onclick="sendMessage()">📤 发送</button>
        <div id="sendResult" style="margin-top:12px;display:none;font-size:12px;font-family:monospace;background:var(--surface2);border-radius:8px;padding:12px;white-space:pre-wrap"></div>
      </div>
    </div>

    <!-- ── 事件日志 ── -->
    <div class="page" id="page-events">
      <div class="page-title">事件日志</div>
      <div class="page-desc">Webhook 收发事件记录，点击条目展开原始数据</div>
      <div class="log-toolbar">
        <div class="log-tabs">
          <span class="log-tab active" data-tab="all" onclick="setTab(this)">全部</span>
          <span class="log-tab" data-tab="recv" onclick="setTab(this)">📥 接收</span>
          <span class="log-tab" data-tab="send" onclick="setTab(this)">📤 发送</span>
          <span class="log-tab" data-tab="error" onclick="setTab(this)">⚠ 报错</span>
        </div>
        <button class="btn btn-outline" onclick="refreshEvents()">🔄 刷新</button>
        <button class="btn btn-outline" onclick="clearServerLog()">🗑 清空</button>
        <label style="display:flex;align-items:center;gap:6px;margin:0;cursor:pointer;font-size:13px;color:var(--text2)">
          <input type="checkbox" id="autoRefresh" checked onchange="toggleAutoRefresh()"> 自动刷新
        </label>
      </div>
      <div class="event-log" id="eventLog">
        <div style="color:var(--text2);text-align:center;padding:16px">暂无事件</div>
      </div>
    </div>

    <!-- ── 运行日志 ── -->
    <div class="page" id="page-runlog">
      <div class="page-title">运行日志</div>
      <div class="page-desc">实时查看进程 stdout / logging 输出（终端关闭后仍可查看）</div>
      <div class="card" style="padding:14px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px">
          <div style="display:flex;align-items:center;gap:10px">
            <span class="log-sse-dot" id="sseDot"></span>
            <span style="font-size:12px;color:var(--text2)" id="sseStatus">未连接</span>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <select id="logLevelFilter" style="width:auto;padding:5px 10px;font-size:12px" onchange="applyLogFilter()">
              <option value="ALL">全部级别</option>
              <option value="DEBUG">DEBUG+</option>
              <option value="INFO">INFO+</option>
              <option value="WARNING">WARNING+</option>
              <option value="ERROR">ERROR+</option>
            </select>
            <label style="display:flex;align-items:center;gap:5px;margin:0;cursor:pointer;font-size:12px;color:var(--text2)">
              <input type="checkbox" id="logAutoScroll" checked> 自动滚动
            </label>
            <button class="btn btn-outline" style="padding:5px 12px;font-size:12px" onclick="clearRunLog()">🗑 清空</button>
            <button class="btn btn-outline" style="padding:5px 12px;font-size:12px" onclick="reconnectSSE()">🔄 重连</button>
          </div>
        </div>
        <div class="run-log" id="runLog"></div>
      </div>
    </div>

  </main>
</div>

<script>
// 全局状态
let config = {};
let autoRefreshTimer = null;
let currentTab = 'all';
let allEvents = [];
const startTime = Date.now();
let sseSource = null;
let runLogLines = [];   // {level, msg, t}
const MAX_RUN_LINES = 500;
let logLevelFilter = 'ALL';
const LEVEL_ORDER = {DEBUG:10, INFO:20, WARNING:30, ERROR:40, CRITICAL:50};

// 导航
function nav(el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const page = el.dataset.page;
  document.getElementById('page-' + page).classList.add('active');
  el.classList.add('active');
  if (page === 'events') refreshEvents();
  if (page === 'overview') refreshOverview();
  if (page === 'runlog') ensureSSE();
}

// 配置
async function loadConfig() {
  const res = await fetch('/api/config');
  config = await res.json();
  document.getElementById('inputToken').value = config.token || '';
  document.getElementById('inputWebhookPort').value = config.webhook_port || 8080;
  document.getElementById('inputWebhookPath').value = config.webhook_path || '/webhook';
  document.getElementById('inputWsPath').value = config.ws_path || '/ws';
  document.getElementById('inputWsToken').value = config.ws_token || '';
  document.getElementById('inputWebuiPort').value = config.webui_port || 8081;
  document.getElementById('inputWebuiPassword').value = config.webui_password || '';
  updateUrls();
}

function updateUrls() {
  const wport = config.webhook_port || 8080;
  const wpath = config.webhook_path || '/webhook';
  const wspath = config.ws_path || '/ws';
  const wstoken = config.ws_token || '';
  document.getElementById('webhookUrl').textContent = `http://YOUR_SERVER_IP:${wport}${wpath}`;
  let wsUrl = `ws://YOUR_SERVER_IP:${wport}${wspath}`;
  if (wstoken) wsUrl += `?token=${wstoken}`;
  document.getElementById('wsUrl').textContent = wsUrl;
}

function toggleVis(id) {
  const inp = document.getElementById(id);
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function saveToken() {
  const token = document.getElementById('inputToken').value.trim();
  if (!token) { showAlert('configAlert','error','请输入 Token'); return; }
  const res = await fetch('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({...config, token})
  });
  const data = await res.json();
  if (data.ok) { config.token = token; showAlert('configAlert','success','Token 已保存'); }
  else showAlert('configAlert','error', data.msg);
}

async function testToken() {
  const token = document.getElementById('inputToken').value.trim();
  if (!token) { showAlert('configAlert','error','请先输入 Token'); return; }
  const btn = event.currentTarget;
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 测试中...';
  const res = await fetch('/api/test-token', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({token})
  });
  const data = await res.json();
  btn.disabled = false; btn.innerHTML = '🔍 测试连接';
  const el = document.getElementById('testResult');
  el.style.display = 'block';
  if (data.ok) {
    el.innerHTML = `<span class="badge badge-green">✓ ${data.msg}</span>`;
    document.getElementById('navDot').classList.add('online');
    document.getElementById('statStatus').textContent = '在线';
  } else {
    el.innerHTML = `<span class="badge badge-red">✗ ${data.msg}</span>`;
    document.getElementById('statStatus').textContent = '离线';
  }
}

async function saveAllConfig() {
  const cfg = {
    ...config,
    token: document.getElementById('inputToken').value.trim(),
    webhook_port: parseInt(document.getElementById('inputWebhookPort').value) || 8080,
    webhook_path: document.getElementById('inputWebhookPath').value || '/webhook',
    ws_path: document.getElementById('inputWsPath').value || '/ws',
    ws_token: document.getElementById('inputWsToken').value,
    webui_port: parseInt(document.getElementById('inputWebuiPort').value) || 8081,
    webui_password: document.getElementById('inputWebuiPassword').value,
  };
  const res = await fetch('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(cfg)
  });
  const data = await res.json();
  if (data.ok) {
    Object.assign(config, cfg);
    updateUrls();
    showAlert('configAlert','success','配置已保存，标 ★ 项需重启生效');
  } else showAlert('configAlert','error', data.msg);
}

function copyText(id) {
  navigator.clipboard.writeText(document.getElementById(id).textContent).then(() => {
    const el = document.getElementById(id).nextElementSibling;
    const orig = el.textContent;
    el.textContent = '✓ 已复制';
    setTimeout(() => el.textContent = orig, 2000);
  });
}

// 发送消息
async function sendMessage() {
  const recvId = document.getElementById('sendRecvId').value.trim();
  const recvType = document.getElementById('sendRecvType').value;
  const contentType = document.getElementById('sendContentType').value;
  const content = document.getElementById('sendContent').value.trim();
  if (!recvId || !content) { showAlert('sendAlert','error','请填写接收者 ID 和消息内容'); return; }
  const res = await fetch('/api/send', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({recvId, recvType, contentType, content})
  });
  const data = await res.json();
  const el = document.getElementById('sendResult');
  el.style.display = 'block';
  el.textContent = JSON.stringify(data, null, 2);
  if (data.code === 1) showAlert('sendAlert','success','消息发送成功');
  else showAlert('sendAlert','error',`发送失败: ${data.msg}`);
}

// 事件日志
function setTab(el) {
  document.querySelectorAll('.log-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  currentTab = el.dataset.tab;
  renderEventLog();
}

async function refreshEvents() {
  const allRes = await fetch('/api/events');
  allEvents = await allRes.json();
  const recvCount = allEvents.filter(e => e.direction==='recv').length;
  const sentCount = allEvents.filter(e => e.direction==='send').length;
  const errCount  = allEvents.filter(e => e.direction==='error').length;
  document.getElementById('statRecv').textContent = recvCount;
  document.getElementById('statSent').textContent = sentCount;
  document.getElementById('statErr').textContent  = errCount;
  document.getElementById('errBadge').style.display = errCount>0 ? '' : 'none';
  document.getElementById('errBadge').textContent = errCount;
  renderEventLog();
  renderLogItems('overviewLog', allEvents.slice(0, 6));
}

function renderEventLog() {
  const filtered = currentTab==='all' ? allEvents : allEvents.filter(e=>e.direction===currentTab);
  renderLogItems('eventLog', filtered);
}

function renderLogItems(containerId, events) {
  const el = document.getElementById(containerId);
  if (!events || !events.length) {
    el.innerHTML = '<div style="color:var(--text2);text-align:center;padding:16px">暂无记录</div>';
    return;
  }
  el.innerHTML = events.map(e => {
    const t = new Date(e.time).toLocaleTimeString('zh-CN',{hour12:false});
    const dir = e.direction || 'recv';
    const dirLabel = dir==='recv'?'📥 接收':dir==='send'?'📤 发送':'⚠ 错误';
    const detail = JSON.stringify(e.data,null,2);
    return `<div class="event-item ${dir}" onclick="toggleDetail(this)">
      <div class="event-header">
        <span class="event-time">${t}</span>
        <span class="event-type-tag">${e.type||'unknown'}</span>
        <span style="color:var(--text2);font-size:10px">${dirLabel}</span>
      </div>
      <div class="event-summary">${escHtml(e.summary||'')}</div>
      <div class="event-detail">${escHtml(detail)}</div>
    </div>`;
  }).join('');
}

function toggleDetail(el){ el.classList.toggle('expanded'); }
function escHtml(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function clearServerLog() {
  await fetch('/api/events/clear',{method:'POST'});
  allEvents=[];
  renderEventLog();
  renderLogItems('overviewLog',[]);
}

function toggleAutoRefresh() {
  document.getElementById('autoRefresh').checked ? startAutoRefresh() : stopAutoRefresh();
}
function startAutoRefresh(){ stopAutoRefresh(); autoRefreshTimer=setInterval(refreshEvents,3000); }
function stopAutoRefresh(){ if(autoRefreshTimer){clearInterval(autoRefreshTimer);autoRefreshTimer=null;} }

// 运行日志 SSE
function ensureSSE() {
  if (sseSource && sseSource.readyState !== EventSource.CLOSED) return;
  reconnectSSE();
}

async function initRunLog() {
  // 先拉取历史记录
  try {
    const res = await fetch('/api/logs');
    const data = await res.json();
    runLogLines = data;
    renderRunLog();
  } catch(e) {}
}

function reconnectSSE() {
  if (sseSource) { sseSource.close(); sseSource = null; }
  sseSource = new EventSource('/api/logs/stream');
  const dot = document.getElementById('sseDot');
  const statusEl = document.getElementById('sseStatus');
  sseSource.onopen = () => {
    dot.classList.add('live');
    statusEl.textContent = '实时连接中';
  };
  sseSource.onmessage = (e) => {
    try {
      const entry = JSON.parse(e.data);
      runLogLines.push(entry);
      if (runLogLines.length > MAX_RUN_LINES) runLogLines.shift();
      appendRunLogLine(entry);
    } catch(err) {}
  };
  sseSource.onerror = () => {
    dot.classList.remove('live');
    statusEl.textContent = '连接断开，将自动重连...';
    setTimeout(reconnectSSE, 3000);
  };
}

function levelNum(l) { return LEVEL_ORDER[l] || 0; }

function applyLogFilter() {
  logLevelFilter = document.getElementById('logLevelFilter').value;
  renderRunLog();
}

function renderRunLog() {
  const el = document.getElementById('runLog');
  const minLevel = logLevelFilter === 'ALL' ? 0 : (LEVEL_ORDER[logLevelFilter] || 0);
  const filtered = runLogLines.filter(l => levelNum(l.level) >= minLevel);
  if (!filtered.length) {
    el.innerHTML = '<span style="color:#64748b">暂无日志...</span>';
    return;
  }
  el.innerHTML = filtered.map(l =>
    `<div class="run-log-line ${l.level}">${escHtml(l.msg)}</div>`
  ).join('');
  if (document.getElementById('logAutoScroll').checked) el.scrollTop = el.scrollHeight;
}

function appendRunLogLine(entry) {
  const minLevel = logLevelFilter === 'ALL' ? 0 : (LEVEL_ORDER[logLevelFilter] || 0);
  if (levelNum(entry.level) < minLevel) return;
  const el = document.getElementById('runLog');
  // Remove placeholder if present
  if (el.innerHTML.includes('暂无日志')) el.innerHTML = '';
  const div = document.createElement('div');
  div.className = `run-log-line ${entry.level}`;
  div.textContent = entry.msg;
  el.appendChild(div);
  // Trim excess lines in DOM
  while (el.children.length > MAX_RUN_LINES) el.removeChild(el.firstChild);
  if (document.getElementById('logAutoScroll').checked) el.scrollTop = el.scrollHeight;
}

function clearRunLog() {
  runLogLines = [];
  document.getElementById('runLog').innerHTML = '<span style="color:#64748b">日志已清空</span>';
  fetch('/api/logs/clear', {method:'POST'}).catch(()=>{});
}

// 重启
async function restartService() {
  if (!confirm('确认要重启服务吗？期间 Webhook 将短暂中断。')) return;
  const btn = event.currentTarget;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 重启中...';
  try {
    await fetch('/api/restart', {method:'POST'});
  } catch(e) {}
  // 等待服务重启后刷新页面
  setTimeout(() => {
    const check = setInterval(async () => {
      try {
        const r = await fetch('/api/status');
        if (r.ok) { clearInterval(check); location.reload(); }
      } catch(e) {}
    }, 1500);
  }, 2000);
}

// 测试 Webhook
async function sendTestEvent() {
  let payload;
  try { payload = JSON.parse(document.getElementById('testPayload').value); }
  catch(e) { showTestEventResult(false,'无效的 JSON'); return; }
  const res = await fetch('/api/test-event',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  showTestEventResult(data.ok, data.msg);
  if(data.ok) refreshEvents();
}
function showTestEventResult(ok, msg) {
  const el = document.getElementById('testEventResult');
  el.style.display = 'block';
  el.innerHTML = ok
    ? `<span class="badge badge-green">✓ ${msg}</span>`
    : `<span class="badge badge-red">✗ ${msg}</span>`;
}

// 概览
async function refreshOverview() {
  const elapsed = Math.floor((Date.now()-startTime)/1000);
  const h=Math.floor(elapsed/3600),m=Math.floor((elapsed%3600)/60),s=elapsed%60;
  document.getElementById('statUptime').textContent = h?`${h}h ${m}m`:m?`${m}m ${s}s`:`${s}s`;
  const res = await fetch('/api/status').catch(()=>null);
  if(res && res.ok){
    const st = await res.json();
    document.getElementById('statWsConn').textContent = st.ws_connections ?? '—';
  }
  await refreshEvents();
}

// 工具
function showAlert(id, type, msg) {
  const el = document.getElementById(id);
  el.className = `alert alert-${type==='error'?'error':type==='success'?'success':'info'} show`;
  el.textContent = msg;
  setTimeout(()=>el.classList.remove('show'), 4000);
}

// 初始化
loadConfig();
initRunLog();
startAutoRefresh();
setInterval(refreshOverview, 5000);
// 启动后也连接 SSE（供后台接收日志，切换到运行日志页时显示）
reconnectSSE();
</script>
</body>
</html>"""


class YunhuWebUI:
    """云湖 SDK WebUI 管理服务"""

    def __init__(
        self,
        webhook: YunhuWebhook,
        bridge=None,
        config_path: str = "yunhu_config.json",
        webui_port: int = 8081,
        webui_host: str = "0.0.0.0",
        password: str = "",
    ):
        self.webhook = webhook
        self.bridge = bridge
        self.config_path = config_path
        self.webui_port = webui_port
        self.webui_host = webui_host
        self.password = password
        self.config = self._load_config()
        self._runner: Optional[web.AppRunner] = None

        # Session cache {token: expiry_timestamp}
        self._sessions: dict = {}
        self._session_ttl = 86400 * 7  # 7天

        # 内存日志处理器
        self._log_handler = _log_handler

    # 配置管理

    def _load_config(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
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

    def _save_config(self, cfg: dict):
        self.config = cfg
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 鉴权中间件

    def _is_auth(self, request: web.Request) -> bool:
        if not self.password:
            return True
        token = request.cookies.get("yunhu_session", "")
        exp = self._sessions.get(token, 0)
        return time.time() < exp

    def _make_session_token(self) -> str:
        return secrets.token_hex(32)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path in ("/api/login",):
            return await handler(request)
        if not self._is_auth(request):
            if request.path.startswith("/api/"):
                return web.json_response({"ok": False, "msg": "未授权"}, status=401)
            return web.Response(text=LOGIN_HTML, content_type="text/html")
        return await handler(request)

    # API 路由

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=WEBUI_HTML, content_type="text/html")

    async def _handle_login(self, request: web.Request) -> web.Response:
        if not self.password:
            return web.json_response({"ok": True})
        try:
            data = await request.json()
            pwd = data.get("password", "")
        except Exception:
            return web.json_response({"ok": False, "msg": "参数错误"})
        if pwd == self.password:
            token = self._make_session_token()
            self._sessions[token] = time.time() + self._session_ttl
            resp = web.json_response({"ok": True})
            resp.set_cookie("yunhu_session", token, max_age=self._session_ttl, httponly=True)
            return resp
        return web.json_response({"ok": False, "msg": "密码错误"})

    async def _handle_get_config(self, request: web.Request) -> web.Response:
        return web.json_response(self.config)

    async def _handle_save_config(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            self._save_config(data)
            # 同步更新当前运行时密码（无需重启即可生效）
            new_pwd = data.get("webui_password", "")
            self.password = new_pwd
            return web.json_response({"ok": True, "msg": "已保存"})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)})

    async def _handle_test_token(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            token = data.get("token", "")
            if not token:
                return web.json_response({"ok": False, "msg": "Token 为空"})
            client = YunhuClient(token)
            ok, msg = await client.test_connection()
            await client.close()
            return web.json_response({"ok": ok, "msg": msg})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)})

    async def _handle_send(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            token = self.config.get("token", "")
            if not token:
                return web.json_response({"code": -1, "msg": "Token 未配置"})
            client = YunhuClient(token)
            content_type = data.get("contentType", "text")
            content = data.get("content", "")
            recv_id = data.get("recvId", "")
            recv_type = data.get("recvType", "user")
            if content_type == "text":
                resp = await client.send_text(recv_id, recv_type, content)
            elif content_type == "markdown":
                resp = await client.send_markdown(recv_id, recv_type, content)
            else:
                resp = await client.send_text(recv_id, recv_type, content)
            await client.close()
            summary = f"[WebUI] → {recv_type}:{recv_id} [{resp.code}] {content[:50]}"
            if resp.code == 1:
                self.webhook.append_log("send", f"send_{content_type}", summary, {
                    "recv_id": recv_id, "recv_type": recv_type,
                    "content": content, "resp_code": resp.code
                })
            else:
                self.webhook.append_log("error", f"send_{content_type}_fail",
                    f"[WebUI] 发送失败: {resp.msg}", {
                        "recv_id": recv_id, "content": content,
                        "resp_code": resp.code, "resp_msg": resp.msg
                    })
            return web.json_response({"code": resp.code, "msg": resp.msg, "data": resp.data})
        except Exception as e:
            self.webhook.append_log("error", "send_exception", f"[WebUI] 发送异常: {e}", {})
            return web.json_response({"code": -1, "msg": str(e)})

    async def _handle_get_events(self, request: web.Request) -> web.Response:
        direction = request.query.get("direction", None)
        return web.json_response(self.webhook.get_event_log(direction))

    async def _handle_clear_events(self, request: web.Request) -> web.Response:
        self.webhook.clear_event_log()
        return web.json_response({"ok": True})

    async def _handle_test_event(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            from models import parse_event
            event = parse_event(data)
            if event:
                from webhook import _make_recv_summary
                summary = _make_recv_summary(event, data)
                self.webhook.append_log("recv", event.event_type, f"[测试] {summary}", data)
                await self.webhook.event_queue.put(event)
                asyncio.create_task(self.webhook._dispatch(event))
                return web.json_response({"ok": True, "msg": "测试事件已注入"})
            else:
                return web.json_response({"ok": False, "msg": "事件解析失败"})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)})

    async def _handle_status(self, request: web.Request) -> web.Response:
        ws_conn = self.bridge.connection_count if self.bridge else 0
        return web.json_response({
            "ws_connections": ws_conn,
            "event_log_count": len(self.webhook._event_log),
        })

    # 运行日志 API

    async def _handle_get_logs(self, request: web.Request) -> web.Response:
        """返回历史运行日志"""
        return web.json_response(self._log_handler.get_all())

    async def _handle_clear_logs(self, request: web.Request) -> web.Response:
        self._log_handler.clear()
        return web.json_response({"ok": True})

    async def _handle_log_stream(self, request: web.Request) -> web.StreamResponse:
        """SSE 实时日志流"""
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        # 先发送历史记录（最近 100 条）
        history = self._log_handler.get_all()[-100:]
        for entry in history:
            line = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            try:
                await response.write(line.encode())
            except Exception:
                return response

        # 订阅新日志
        queue = self._log_handler.subscribe()
        try:
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=25)
                    line = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                    await response.write(line.encode())
                except asyncio.TimeoutError:
                    # 发送心跳，防止连接超时
                    await response.write(b": heartbeat\n\n")
        except Exception:
            pass
        finally:
            self._log_handler.unsubscribe(queue)

        return response

    # 重启 API

    async def _handle_restart(self, request: web.Request) -> web.Response:
        """重启整个服务进程"""
        logger.info("WebUI 触发重启...")
        async def _do_restart():
            await asyncio.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        asyncio.create_task(_do_restart())
        return web.json_response({"ok": True, "msg": "正在重启..."})

    # 启动

    def build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_get("/", self._handle_index)
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_get("/api/config", self._handle_get_config)
        app.router.add_post("/api/config", self._handle_save_config)
        app.router.add_post("/api/test-token", self._handle_test_token)
        app.router.add_post("/api/send", self._handle_send)
        app.router.add_get("/api/events", self._handle_get_events)
        app.router.add_post("/api/events/clear", self._handle_clear_events)
        app.router.add_post("/api/test-event", self._handle_test_event)
        app.router.add_get("/api/status", self._handle_status)
        # 运行日志
        app.router.add_get("/api/logs", self._handle_get_logs)
        app.router.add_get("/api/logs/stream", self._handle_log_stream)
        app.router.add_post("/api/logs/clear", self._handle_clear_logs)
        # 重启
        app.router.add_post("/api/restart", self._handle_restart)
        return app

    async def start(self):
        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.webui_host, self.webui_port)
        await site.start()
        logger.info(f"云湖 WebUI 已启动: http://{self.webui_host}:{self.webui_port}")
        if self.password:
            logger.info("WebUI 已启用密码保护")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()