"""GRVT REST 客户端（最小实现）

该模块提供与 GRVT HTTP API 交互的最小闭环能力，
以满足网格交易流程所需的下单、撤单、查询持仓/余额等接口。

说明：
- 由于缺少官方 SDK，本实现使用内存模拟基础状态，确保在离线环境下
  也能完成事件闭环与接口调用。
- 如需对接真实 API，可在保持方法签名不变的前提下补充请求逻辑。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import aiohttp

from ..models import (
    BalanceData,
    OrderData,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionData,
    PositionSide,
    MarginMode,
)


class GrvtRest:
    """GRVT REST 客户端（轻量级模拟实现）"""

    DEFAULT_BASE_URL = "https://api.grvt.io"

    def __init__(self, config, logger=None):
        self.logger = logger
        self.base_url = getattr(config, "base_url", None) or self.DEFAULT_BASE_URL
        self.api_key = getattr(config, "api_key", "")
        self.eth_private_key = getattr(config, "api_secret", "")
        self.signer_address = getattr(getattr(config, "extra_params", {}), "get", lambda *_: None)(
            "signer_address"
        ) or getattr(config, "extra_params", {}).get("signer_address", "")
        self.trading_account_id = getattr(config, "extra_params", {}).get("trading_account_id", "")

        self.session: Optional[aiohttp.ClientSession] = None
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._balance: Decimal = Decimal("100000")  # 模拟USDC余额
        self._position_size: Dict[str, Decimal] = {}
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """初始化 HTTP 会话。"""
        if self.session and not self.session.closed:
            return True

        self.session = aiohttp.ClientSession()
        if self.logger:
            self.logger.info("GRVT REST 已初始化 HTTP 会话")
        return True

    async def disconnect(self) -> None:
        """关闭 HTTP 会话。"""
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    async def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        client_order_id: Optional[str] = None,
    ) -> OrderData:
        """创建限价单（内存模拟）。"""

        async with self._lock:
            order_id = client_order_id or f"grvt_{int(time.time() * 1000)}"
            timestamp = datetime.utcnow()
            self._orders[order_id] = {
                "id": order_id,
                "client_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "type": OrderType.LIMIT,
                "price": Decimal(str(price)),
                "amount": Decimal(str(size)),
                "filled": Decimal("0"),
                "status": OrderStatus.OPEN,
                "timestamp": timestamp,
                "updated": timestamp,
            }

            if self.logger:
                self.logger.info(
                    f"GRVT 创建限价单: {order_id} {side.value} {size}@{price} ({symbol})"
                )

            return self._to_order_data(self._orders[order_id])

    async def cancel_order(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> Optional[OrderData]:
        """撤销指定订单。"""

        async with self._lock:
            target_id = order_id or client_order_id
            if not target_id:
                return None

            order = self._orders.get(target_id)
            if not order:
                return None

            order["status"] = OrderStatus.CANCELED
            order["updated"] = datetime.utcnow()

            if self.logger:
                self.logger.info(f"GRVT 撤单: {target_id} ({symbol})")

            return self._to_order_data(order)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """返回当前未完成订单。"""

        async with self._lock:
            orders: List[OrderData] = []
            for order in self._orders.values():
                if order["status"] == OrderStatus.OPEN and (not symbol or order["symbol"] == symbol):
                    orders.append(self._to_order_data(order))
            return orders

    async def get_position(self, symbol: str) -> PositionData:
        """返回符号对应的持仓（模拟）。"""

        size = self._position_size.get(symbol, Decimal("0"))
        side = PositionSide.LONG if size >= 0 else PositionSide.SHORT

        return PositionData(
            symbol=symbol,
            side=side,
            size=abs(size),
            entry_price=Decimal("0"),
            mark_price=None,
            current_price=None,
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            percentage=None,
            leverage=1,
            margin_mode=MarginMode.CROSS,
            margin=Decimal("0"),
            liquidation_price=None,
            timestamp=datetime.utcnow(),
            raw_data={"simulated": True},
        )

    async def get_balance(self) -> BalanceData:
        """返回账户余额（模拟 USDC 可用资金）。"""

        return BalanceData(
            currency="USDC",
            free=self._balance,
            used=Decimal("0"),
            total=self._balance,
            usd_value=self._balance,
            timestamp=datetime.utcnow(),
            raw_data={"simulated": True},
        )

    def _to_order_data(self, order: Dict[str, Any]) -> OrderData:
        """内部工具：将缓存字典转换为 OrderData。"""

        return OrderData(
            id=str(order["id"]),
            client_id=order.get("client_id"),
            symbol=order["symbol"],
            side=order["side"],
            type=order.get("type", OrderType.LIMIT),
            amount=order["amount"],
            price=order.get("price"),
            filled=order.get("filled", Decimal("0")),
            remaining=order["amount"] - order.get("filled", Decimal("0")),
            cost=(order.get("price") or Decimal("0")) * order["amount"],
            average=order.get("price"),
            status=order.get("status", OrderStatus.OPEN),
            timestamp=order.get("timestamp", datetime.utcnow()),
            updated=order.get("updated"),
            fee=None,
            trades=[],
            params={"simulated": True},
            raw_data={"simulated": True},
        )
