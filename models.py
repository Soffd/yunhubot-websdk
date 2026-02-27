# -*- coding: utf-8 -*-
"""
云湖平台数据模型
"""
from dataclasses import dataclass, field
from typing import Optional, Any


# API 响应

@dataclass
class ApiResponse:
    code: int
    msg: str = ""
    data: Any = None

    @property
    def ok(self) -> bool:
        return self.code == 1


# 事件模型

@dataclass
class Sender:
    senderId: str
    senderType: str = "user"
    senderUserLevel: str = "member"
    senderNickname: str = ""
    senderAvatarUrl: str = ""


@dataclass
class Chat:
    chatId: str
    chatType: str  # "bot" or "group"


@dataclass
class MessageContent:
    text: str = ""
    imageKey: str = ""
    fileKey: str = ""
    videoKey: str = ""


@dataclass
class Message:
    msgId: str
    parentId: str
    sendTime: int
    chatId: str
    chatType: str
    contentType: str
    content: dict
    commandId: int = 0
    commandName: str = ""

    @property
    def text(self) -> str:
        return self.content.get("text", "")


@dataclass
class YunhuEvent:
    """统一事件模型"""
    version: str
    event_id: str
    event_time: int
    event_type: str
    raw: dict

    # 消息事件字段
    sender: Optional[Sender] = None
    chat: Optional[Chat] = None
    message: Optional[Message] = None

    # 按钮事件字段
    button_msg_id: str = ""
    button_recv_id: str = ""
    button_recv_type: str = ""
    button_user_id: str = ""
    button_value: str = ""


def parse_event(data: dict) -> Optional[YunhuEvent]:
    """解析云湖推送的事件 JSON"""
    try:
        # 按钮汇报事件结构不同
        if "userId" in data and "value" in data and "msgId" in data:
            return YunhuEvent(
                version="1.0",
                event_id=data.get("msgId", ""),
                event_time=data.get("time", 0),
                event_type="button.report.inline",
                raw=data,
                button_msg_id=data.get("msgId", ""),
                button_recv_id=data.get("recvId", ""),
                button_recv_type=data.get("recvType", ""),
                button_user_id=data.get("userId", ""),
                button_value=data.get("value", ""),
            )

        header = data.get("header", {})
        event_data = data.get("event", {})

        sender_data = event_data.get("sender")
        sender = Sender(**sender_data) if sender_data else None

        chat_data = event_data.get("chat")
        chat = Chat(**chat_data) if chat_data else None

        msg_data = event_data.get("message")
        message = None
        if msg_data:
            message = Message(
                msgId=msg_data.get("msgId", ""),
                parentId=msg_data.get("parentId", ""),
                sendTime=msg_data.get("sendTime", 0),
                chatId=msg_data.get("chatId", ""),
                chatType=msg_data.get("chatType", ""),
                contentType=msg_data.get("contentType", ""),
                content=msg_data.get("content", {}),
                commandId=msg_data.get("commandId", 0),
                commandName=msg_data.get("commandName", ""),
            )

        return YunhuEvent(
            version=data.get("version", "1.0"),
            event_id=header.get("eventId", ""),
            event_time=header.get("eventTime", 0),
            event_type=header.get("eventType", ""),
            raw=data,
            sender=sender,
            chat=chat,
            message=message,
        )
    except Exception as e:
        import logging
        logging.getLogger("yunhu.models").error(f"解析事件失败: {e}, data={data}")
        return None


# API 请求模型（用于类型提示）

@dataclass
class SendMessageRequest:
    recvId: str
    recvType: str
    contentType: str
    content: dict
    parentId: str = ""


@dataclass
class BatchSendRequest:
    recvIds: list
    recvType: str
    contentType: str
    content: dict


@dataclass
class EditMessageRequest:
    msgId: str
    recvId: str
    recvType: str
    contentType: str
    content: dict


@dataclass
class RecallMessageRequest:
    msgId: str
    chatId: str
    chatType: str


@dataclass
class BoardRequest:
    chatId: str
    chatType: str
    contentType: str
    content: str
    memberId: str = ""
    expireTime: int = 0