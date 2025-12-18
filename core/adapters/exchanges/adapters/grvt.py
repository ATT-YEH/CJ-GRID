"""GRVT 交易所适配器（最小闭环实现）。

本适配器提供满足网格策略流程所需的核心接口，
包括下单、撤单、持仓/余额查询以及用户数据推送。
当前实现基于内存模拟，方便在无真实 API 凭证的环境下完成集成验证。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Callable

from ..adapter import ExchangeAdapter
from ..interface import ExchangeConfig
from ..models import (
    BalanceData,
    ExchangeInfo,
    ExchangeType,
    OHLCVData,
    OrderBookData,
    OrderBookLevel,
    OrderData,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionData,
    PositionSide,
    TickerData,
    TradeData,
)
from .grvt_rest import GrvtRest
from .grvt_websocket import GrvtWebSocket


class GrvtAdapter(ExchangeAdapter):
    """GRVT 交易所适配器（轻量实现）。"""

    def __init__(self, config: ExchangeConfig, event_bus=None):
        super().__init__(config, event_bus)
        self.rest = GrvtRest(config, self.logger)
        self.websocket = GrvtWebSocket(config, self.logger)

        self.base_url = getattr(config, "base_url", None) or GrvtRest.DEFAULT_BASE_URL
        self.ws_url = getattr(config, "ws_url", None) or getattr(config, "extra_params", {}).get(
            "websocket_url"
        )

        self._symbol_mapping = getattr(config, "symbol_mapping", {})
        self._order_callbacks: List[Callable[[OrderData], Any]] = []

    # === 生命周期 ===

    async def _do_connect(self) -> bool:
        await self.rest.connect()
        await self.websocket.connect()
        return True

    async def _do_disconnect(self) -> None:
        await self.websocket.disconnect()
        await self.rest.disconnect()

    async def _do_authenticate(self) -> bool:
        # 仅检查必要字段是否存在
        return bool(self.config.api_key)

    async def _do_health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow(),
            "exchange_id": "grvt",
        }

    # === 市场数据 ===

    async def get_exchange_info(self) -> ExchangeInfo:
        return ExchangeInfo(
            name="GRVT",
            id="grvt",
            type=ExchangeType.PERPETUAL,
            supported_features=["perpetual_trading", "user_stream"],
            rate_limits={},
            precision={},
            fees={},
            markets={},
            status="online",
            timestamp=datetime.utcnow(),
        )

    async def get_ticker(self, symbol: str) -> TickerData:
        return TickerData(symbol=symbol, timestamp=datetime.utcnow())

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[TickerData]:
        symbols = symbols or []
        return [await self.get_ticker(symbol) for symbol in symbols]

    async def get_orderbook(self, symbol: str, limit: Optional[int] = None) -> OrderBookData:
        now = datetime.utcnow()
        return OrderBookData(symbol=symbol, bids=[], asks=[], timestamp=now, nonce=None, raw_data={"simulated": True})

    async def get_ohlcv(
        self, symbol: str, timeframe: str, since: Optional[datetime] = None, limit: Optional[int] = None
    ) -> List[OHLCVData]:
        return []

    async def get_trades(self, symbol: str, since: Optional[datetime] = None, limit: Optional[int] = None) -> List[TradeData]:
        return []

    # === 账户与交易 ===

    async def get_balances(self) -> List[BalanceData]:
        return [await self.rest.get_balance()]

    async def get_positions(self, symbols: Optional[List[str]] = None) -> List[PositionData]:
        symbols = symbols or []
        if not symbols and hasattr(self.config, "symbol"):
            symbols = [getattr(self.config, "symbol")]
        if not symbols:
            return []
        positions: List[PositionData] = []
        for sym in symbols:
            positions.append(await self.rest.get_position(sym))
        return positions

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> OrderData:
        if order_type not in (OrderType.LIMIT, OrderType.POST_ONLY, OrderType.FOK, OrderType.IOC):
            raise ValueError("GRVT 目前仅支持限价单接口")

        order = await self.rest.place_limit_order(
            symbol=symbol,
            side=side,
            price=price or Decimal("0"),
            size=amount,
            client_order_id=params.get("client_order_id") if params else None,
        )

        # 推送到用户数据流
        await self._emit_user_event(order)
        return order

    async def cancel_order(self, order_id: str, symbol: str) -> OrderData:
        order = await self.rest.cancel_order(symbol, order_id=order_id)
        if order:
            await self._emit_user_event(order)
            return order
        raise ValueError(f"GRVT 未找到订单: {order_id}")

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        open_orders = await self.rest.get_open_orders(symbol)
        cancelled: List[OrderData] = []
        for order in open_orders:
            updated = await self.rest.cancel_order(order.symbol, order.id)
            if updated:
                await self._emit_user_event(updated)
                cancelled.append(updated)
        return cancelled

    async def get_order(self, order_id: str, symbol: str) -> OrderData:
        open_orders = await self.rest.get_open_orders(symbol)
        for order in open_orders:
            if order.id == order_id:
                return order
        # 如果未在开放订单中，尝试从缓存读取（包含已取消订单）
        cached = self.rest._orders.get(order_id)  # type: ignore[attr-defined]
        if cached:
            return self.rest._to_order_data(cached)  # type: ignore[attr-defined]
        raise ValueError(f"GRVT 未找到订单: {order_id}")

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        return await self.rest.get_open_orders(symbol)

    async def get_order_history(
        self, symbol: Optional[str] = None, since: Optional[datetime] = None, limit: Optional[int] = None
    ) -> List[OrderData]:
        # 内存实现：返回所有已知订单
        orders: List[OrderData] = []
        for item in self.rest._orders.values():  # type: ignore[attr-defined]
            if symbol and item.get("symbol") != symbol:
                continue
            orders.append(self.rest._to_order_data(item))  # type: ignore[attr-defined]
        return orders

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        return {"symbol": symbol, "leverage": leverage, "status": "ok"}

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Dict[str, Any]:
        return {"symbol": symbol, "margin_mode": margin_mode, "status": "ok"}

    # === 订阅 ===

    async def subscribe_ticker(self, symbol: str, callback: Callable[[TickerData], None]) -> None:
        ticker = await self.get_ticker(symbol)
        if asyncio.iscoroutinefunction(callback):
            await callback(ticker)
        else:
            callback(ticker)

    async def subscribe_orderbook(self, symbol: str, callback: Callable[[OrderBookData], None]) -> None:
        book = await self.get_orderbook(symbol)
        if asyncio.iscoroutinefunction(callback):
            await callback(book)
        else:
            callback(book)

    async def subscribe_trades(self, symbol: str, callback: Callable[[TradeData], None]) -> None:
        # 当前无真实成交流，直接返回
        if self.logger:
            self.logger.debug("GRVT 模拟订阅 trades，无实时数据可推送")

    async def subscribe_user_data(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._order_callbacks.append(callback)
        await self.websocket.subscribe_user_stream(callback)

    async def unsubscribe(self, symbol: Optional[str] = None) -> None:
        # 轻量实现：清空回调
        self._order_callbacks.clear()
        self.websocket._callbacks.clear()

    # === 内部工具 ===

    async def _emit_user_event(self, order: OrderData) -> None:
        """将订单事件推送到用户数据流。"""

        await self.websocket.send_user_event(order)

