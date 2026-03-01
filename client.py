# -*- coding: utf-8 -*-
"""
云湖机器人 API 客户端
封装所有云湖开放平台接口
"""
import asyncio
import os
import aiohttp
import logging
from typing import Optional, Union
from models import (
    SendMessageRequest, BatchSendRequest, EditMessageRequest,
    RecallMessageRequest, BoardRequest, ApiResponse
)

logger = logging.getLogger("yunhu.client")

BASE_URL = "https://chat-go.jwzhd.com/open-apis/v1"


class YunhuClient:
    """云湖机器人 API 客户端"""

    def __init__(self, token: str, timeout: int = 10, upload_timeout: int = 120):
        self.token = token
        # 普通 API 请求超时（发消息、查消息等）
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        # 上传专用超时，视频/文件可能较大，需要更长时间
        self.upload_timeout = aiohttp.ClientTimeout(
            total=upload_timeout,
            connect=10,
            sock_connect=10,
            sock_read=upload_timeout,
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def _get_upload_session(self) -> aiohttp.ClientSession:
        """上传操作使用独立的长超时 Session，避免影响普通 API 调用"""
        return aiohttp.ClientSession(timeout=self.upload_timeout)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, path: str, data: dict) -> ApiResponse:
        session = await self._get_session()
        url = f"{BASE_URL}{path}?token={self.token}"
        try:
            async with session.post(url, json=data) as resp:
                result = await resp.json()
                return ApiResponse(**result)
        except Exception as e:
            logger.error(f"POST {path} failed: {e}")
            return ApiResponse(code=-1, msg=str(e), data=None)

    async def _get(self, path: str, params: dict) -> ApiResponse:
        session = await self._get_session()
        params["token"] = self.token
        url = f"{BASE_URL}{path}"
        try:
            async with session.get(url, params=params) as resp:
                result = await resp.json()
                return ApiResponse(**result)
        except Exception as e:
            logger.error(f"GET {path} failed: {e}")
            return ApiResponse(code=-1, msg=str(e), data=None)

    # 发送消息
    async def send_text(
        self, recv_id: str, recv_type: str, text: str,
        parent_id: str = "", buttons: list = None
    ) -> ApiResponse:
        """发送文本消息"""
        content = {"text": text}
        if buttons:
            content["buttons"] = buttons
        return await self._post("/bot/send", {
            "recvId": recv_id, "recvType": recv_type,
            "contentType": "text", "content": content,
            **({"parentId": parent_id} if parent_id else {})
        })

    async def send_markdown(
        self, recv_id: str, recv_type: str, text: str,
        parent_id: str = "", buttons: list = None
    ) -> ApiResponse:
        """发送 Markdown 消息"""
        content = {"text": text}
        if buttons:
            content["buttons"] = buttons
        return await self._post("/bot/send", {
            "recvId": recv_id, "recvType": recv_type,
            "contentType": "markdown", "content": content,
            **({"parentId": parent_id} if parent_id else {})
        })

    async def send_image(
        self, recv_id: str, recv_type: str, image_key: str,
        parent_id: str = ""
    ) -> ApiResponse:
        """发送图片消息（需先上传获取 imageKey）"""
        return await self._post("/bot/send", {
            "recvId": recv_id, "recvType": recv_type,
            "contentType": "image", "content": {"imageKey": image_key},
            **({"parentId": parent_id} if parent_id else {})
        })

    async def send_file(
        self, recv_id: str, recv_type: str, file_key: str,
        parent_id: str = ""
    ) -> ApiResponse:
        """发送文件消息（需先上传获取 fileKey）"""
        return await self._post("/bot/send", {
            "recvId": recv_id, "recvType": recv_type,
            "contentType": "file", "content": {"fileKey": file_key},
            **({"parentId": parent_id} if parent_id else {})
        })

    async def send_video(
        self, recv_id: str, recv_type: str, video_key: str,
        parent_id: str = ""
    ) -> ApiResponse:
        """发送视频消息（需先上传获取 videoKey）"""
        return await self._post("/bot/send", {
            "recvId": recv_id, "recvType": recv_type,
            "contentType": "video", "content": {"videoKey": video_key},
            **({"parentId": parent_id} if parent_id else {})
        })

    async def batch_send_text(
        self, recv_ids: list, recv_type: str, text: str
    ) -> ApiResponse:
        """批量发送文本消息"""
        return await self._post("/bot/batch_send", {
            "recvIds": recv_ids, "recvType": recv_type,
            "contentType": "text", "content": {"text": text}
        })

    #编辑 / 撤回消息

    async def edit_message(
        self, msg_id: str, recv_id: str, recv_type: str,
        content_type: str, content: dict
    ) -> ApiResponse:
        """编辑消息"""
        return await self._post("/bot/edit", {
            "msgId": msg_id, "recvId": recv_id, "recvType": recv_type,
            "contentType": content_type, "content": content
        })

    async def recall_message(
        self, msg_id: str, chat_id: str, chat_type: str
    ) -> ApiResponse:
        """撤回消息"""
        return await self._post("/bot/recall", {
            "msgId": msg_id, "chatId": chat_id, "chatType": chat_type
        })

    #消息列表

    async def get_messages(
        self, chat_id: str, chat_type: str,
        message_id: str = "", before: int = 0, after: int = 0
    ) -> ApiResponse:
        """获取消息列表"""
        params = {"chat-id": chat_id, "chat-type": chat_type}
        if message_id:
            params["message-id"] = message_id
        if before:
            params["before"] = before
        if after:
            params["after"] = after
        return await self._get("/bot/messages", params)

    #上传接口
    @staticmethod
    def _guess_mime(filename: str) -> str:
        """根据文件名后缀猜测 MIME type"""
        ext = os.path.splitext(filename)[-1].lower()
        return {
            # 图片
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",  ".gif": "image/gif",
            ".webp": "image/webp", ".bmp": "image/bmp",
            # 视频
            ".mp4": "video/mp4", ".mov": "video/quicktime",
            ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
            ".flv": "video/x-flv", ".webm": "video/webm",
        }.get(ext, "application/octet-stream")

    async def upload_image(self, image_path: str) -> ApiResponse:
        """上传图片，返回 imageKey"""
        url = f"{BASE_URL}/image/upload?token={self.token}"
        async with await self._get_upload_session() as session:
            try:
                filename = image_path.split("/")[-1]
                mime = self._guess_mime(filename)
                with open(image_path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("image", f, filename=filename, content_type=mime)
                    async with session.post(url, data=form) as resp:
                        if resp.status == 413:
                            return ApiResponse(code=-1, msg="上传文件过大 (413)", data=None)
                        result = await resp.json(content_type=None)
                        return ApiResponse(**result)
            except asyncio.TimeoutError:
                logger.error("Upload image failed: 上传超时，请检查网络或增大 upload_timeout")
                return ApiResponse(code=-1, msg="上传超时", data=None)
            except Exception as e:
                logger.error(f"Upload image failed: {e}")
                return ApiResponse(code=-1, msg=str(e), data=None)

    async def upload_image_bytes(self, data: bytes, filename: str = "image.png") -> ApiResponse:
        """上传图片字节，返回 imageKey"""
        url = f"{BASE_URL}/image/upload?token={self.token}"
        async with await self._get_upload_session() as session:
            try:
                mime = self._guess_mime(filename)
                form = aiohttp.FormData()
                form.add_field("image", data, filename=filename, content_type=mime)
                async with session.post(url, data=form) as resp:
                    if resp.status == 413:
                        return ApiResponse(code=-1, msg="上传文件过大 (413)", data=None)
                    result = await resp.json(content_type=None)
                    return ApiResponse(**result)
            except asyncio.TimeoutError:
                logger.error("Upload image bytes failed: 上传超时")
                return ApiResponse(code=-1, msg="上传超时", data=None)
            except Exception as e:
                logger.error(f"Upload image bytes failed: {e}")
                return ApiResponse(code=-1, msg=str(e), data=None)

    async def upload_file(self, file_path: str) -> ApiResponse:
        """上传文件，返回 fileKey"""
        url = f"{BASE_URL}/file/upload?token={self.token}"
        async with await self._get_upload_session() as session:
            try:
                filename = file_path.split("/")[-1]
                with open(file_path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("file", f, filename=filename,
                                   content_type="application/octet-stream")
                    async with session.post(url, data=form) as resp:
                        if resp.status == 413:
                            return ApiResponse(code=-1, msg="上传文件过大 (413)", data=None)
                        result = await resp.json(content_type=None)
                        return ApiResponse(**result)
            except asyncio.TimeoutError:
                logger.error("Upload file failed: 上传超时，请检查网络或增大 upload_timeout")
                return ApiResponse(code=-1, msg="上传超时", data=None)
            except Exception as e:
                logger.error(f"Upload file failed: {e}")
                return ApiResponse(code=-1, msg=str(e), data=None)

    async def upload_video(self, video_path: str) -> ApiResponse:
        """上传视频，返回 videoKey"""
        url = f"{BASE_URL}/video/upload?token={self.token}"
        async with await self._get_upload_session() as session:
            try:
                filename = video_path.split("/")[-1]
                mime = self._guess_mime(filename) 
                with open(video_path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("video", f, filename=filename, content_type=mime)
                    async with session.post(url, data=form) as resp:
                        if resp.status == 413:
                            return ApiResponse(code=-1, msg="上传文件过大 (413)", data=None)
                        result = await resp.json(content_type=None)
                        return ApiResponse(**result)
            except asyncio.TimeoutError:
                logger.error("Upload video failed: 上传超时，请检查网络或增大 upload_timeout（当前默认 120s）")
                return ApiResponse(code=-1, msg="上传超时", data=None)
            except Exception as e:
                logger.error(f"Upload video failed: {e}")
                return ApiResponse(code=-1, msg=str(e), data=None)

    #看板接口

    async def set_board(
        self, chat_id: str, chat_type: str,
        content_type: str, content: str,
        member_id: str = "", expire_time: int = 0
    ) -> ApiResponse:
        """设置用户看板"""
        payload = {
            "chatId": chat_id, "chatType": chat_type,
            "contentType": content_type, "content": content
        }
        if member_id:
            payload["memberId"] = member_id
        if expire_time:
            payload["expireTime"] = expire_time
        return await self._post("/bot/board", payload)

    async def set_board_all(
        self, content_type: str, content: str, expire_time: int = 0
    ) -> ApiResponse:
        """设置全局看板"""
        payload = {"contentType": content_type, "content": content}
        if expire_time:
            payload["expireTime"] = expire_time
        return await self._post("/bot/board-all", payload)

    async def dismiss_board(
        self, chat_id: str, chat_type: str, member_id: str = ""
    ) -> ApiResponse:
        """取消用户看板"""
        payload = {"chatId": chat_id, "chatType": chat_type}
        if member_id:
            payload["memberId"] = member_id
        return await self._post("/bot/board-dismiss", payload)

    async def dismiss_board_all(self) -> ApiResponse:
        """取消全部看板"""
        return await self._post("/bot/board-all-dismiss", {})

    #连接测试

    async def test_connection(self) -> tuple[bool, str]:
        """测试 Token 是否有效，返回 (成功, 消息)"""
        # 尝试获取消息列表（随便一个不存在的ID），如果token无效会返回1003
        resp = await self._get("/bot/messages", {
            "chat-id": "test", "chat-type": "user", "before": "1"
        })
        if resp.code == 1003:
            return False, "Token 无效（未授权）"
        elif resp.code == -1:
            return False, f"连接失败: {resp.msg}"
        else:
            # code 1002 (参数有误) 也说明 token 是有效的，服务器有响应
            return True, f"连接成功（code={resp.code}）"