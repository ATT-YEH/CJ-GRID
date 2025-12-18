"""GRVT WebSocket 客户端（最小实现）

提供用户数据流订阅、事件分发与基础重连逻辑的轻量实现。
实际生产可将 send_user_event 替换为真实 WebSocket 消息派发。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, List, Optional


class GrvtWebSocket:
    """GRVT WebSocket 模拟客户端。"""

    def __init__(self, config=None, logger=None):
        self.logger = logger
        self.config = config
        self._callbacks: List[Callable[[Any], Any]] = []
        self._connected = False
        self._queue: asyncio.Queue = asyncio.Queue()
        self._consumer_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """建立（模拟）WebSocket 连接。"""

        self._connected = True
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume())

        if self.logger:
            self.logger.info("GRVT WebSocket 已连接 (模拟)")
        return True

    async def disconnect(self) -> None:
        """断开连接并清理任务。"""

        self._connected = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

        if self.logger:
            self.logger.info("GRVT WebSocket 已断开")

    async def subscribe_user_stream(self, callback: Callable[[Any], Any]) -> None:
        """订阅用户数据流。"""

        self._callbacks.append(callback)
        if self.logger:
            self.logger.info("GRVT 已订阅用户数据流回调")

    async def send_user_event(self, data: Any) -> None:
        """将事件放入内部队列，交由消费协程分发。"""

        if not self._connected:
            if self.logger:
                self.logger.warning("GRVT WebSocket 未连接，自动尝试重连")
            await self.connect()

        await self._queue.put(data)

    async def _consume(self) -> None:
        """事件消费协程：顺序将事件广播给订阅回调。"""

        try:
            while self._connected:
                data = await self._queue.get()
                for callback in list(self._callbacks):
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(data)
                        else:
                            callback(data)
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"处理 GRVT WS 事件失败: {e}")
        except asyncio.CancelledError:
            pass

