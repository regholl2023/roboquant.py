from datetime import datetime, timedelta
import logging
from decimal import Decimal
from enum import Flag, auto
import random

from roboquant.event import Event
from roboquant.order import Order
from roboquant.signal import Signal
from .trader import Trader
from ..account import Account
from ..event import PriceItem

logger = logging.getLogger(__name__)


class _PositionChange(Flag):
    ENTRY_LONG = auto()
    ENTRY_SHORT = auto()
    EXIT_LONG = auto()
    EXIT_SHORT = auto()

    _ENTRY = ENTRY_LONG | ENTRY_SHORT
    _EXIT = EXIT_LONG | EXIT_SHORT

    @property
    def is_entry(self):
        """Return True is the status is open, False otherwise"""
        return self in _PositionChange._ENTRY

    @property
    def is_exit(self):
        """Return True is the status is closed, False otherwise"""
        return self in _PositionChange._EXIT

    @staticmethod
    def get_change(is_buy: bool, pos_size: Decimal) -> "_PositionChange":
        """Determine the kind of change a certain action would have on the position"""
        if pos_size.is_zero():
            return _PositionChange.ENTRY_LONG if is_buy else _PositionChange.ENTRY_SHORT
        if pos_size > 0:
            return _PositionChange.ENTRY_LONG if is_buy else _PositionChange.EXIT_LONG

        return _PositionChange.EXIT_SHORT if is_buy else _PositionChange.ENTRY_SHORT


class _Context:

    def __init__(self, signal: Signal, position: Decimal) -> None:
        self.signal = signal
        self.position = position

    def log(self, rule: str, **kwargs):
        if logger.isEnabledFor(logging.INFO):
            extra = ' '.join(f"{k}={v}" for k, v in kwargs.items())
            logger.info(
                "Discarded signal because %s [symbol=%s rating=%s type=%s position=%s %s]",
                rule,
                self.signal.symbol,
                self.signal.rating,
                self.signal.type,
                self.position,
                extra,
            )


class FlexTrader(Trader):
    """Implementation of a Trader that has configurable rules to modify which signals are converted into orders.
    This implementation will not generate orders if there is not a price in the event for the underlying symbol.

    The configurable parameters include:

    - one_order_only: don't create new orders for a symbol if there is already an open orders for that same symbol
    - size_fractions: enable fractional order sizes (if size_fractions is larger than 0), default is 0
    - safety_margin_perc: the safety margin as percentage of equity that should remain available (to avoid margin calls),
    default is 0.05 (5%)
    - max_position_perc: the max percentage of the equity to allocate to a single position, default is 0.1 (10%)
    - max_order_perc: the max percentage of the equity to allocate to a new order, default is 0.05 (5%)
    - min_order_perc: the min percentage of the equity to allocate to a new order, default is 0.02 (2%)
    - shorting: allow orders that could result in a short position, default is false
    - price_type: the price type to use when determining order value, for example "CLOSE". Default is "DEFAULT"

    It might be sometimes challenging to understand wby a signal isn't converted into an order. The flex-trader logs
    at INFO level when certain rules have been fired.

    Setting higher logging:
        logging.basicConfig(level=logging.WARNING)
        logging.getLogger("roboquant.traders.flextrader").setLevel(logging.INFO)
    """

    def __init__(
        self,
        one_order_only=True,
        size_fractions=0,
        safety_margin_perc=0.05,
        shorting=False,
        max_order_perc=0.05,
        min_order_perc=0.02,
        max_position_perc=0.1,
        price_type="DEFAULT",
        shuffle_signals=False,
    ) -> None:
        super().__init__()
        self.one_order_only = one_order_only
        self.size_digits: int = size_fractions
        self.safety_margin_perc: float = safety_margin_perc
        self.shorting = shorting
        self.max_order_perc = max_order_perc
        self.min_order_perc = min_order_perc
        self.max_position_perc = max_position_perc
        self.price_type = price_type
        self.shuffle_signals = shuffle_signals

    def _get_order_size(self, rating: float, contract_price: float, max_order_value: float) -> Decimal:
        """Return the order size"""
        size = Decimal(rating * max_order_value / contract_price)
        rounded_size = round(size, self.size_digits)
        return rounded_size

    def create_orders(self, signals: list[Signal], event: Event, account: Account) -> list[Order]:
        # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        if not signals:
            return []

        if self.shuffle_signals:
            random.shuffle(signals)

        orders: list[Order] = []
        equity = account.equity()
        max_order_value = equity * self.max_order_perc
        min_order_value = equity * self.min_order_perc
        max_pos_value = equity * self.max_position_perc
        available = account.buying_power - self.safety_margin_perc * equity

        for signal in signals:
            symbol = signal.symbol
            pos_size = account.get_position_size(symbol)
            ctx = _Context(signal, pos_size)

            change = _PositionChange.get_change(signal.is_buy, pos_size)

            logger.info("available=%s signal=%s pos=%s change=%s", available, signal, pos_size, change)

            if self.one_order_only and account.has_open_order(symbol):
                ctx.log("one order only")
                continue

            item = event.price_items.get(symbol)
            if item is None:
                ctx.log("no price is available")
                continue

            price = item.price(self.price_type)

            if not self.shorting and change == _PositionChange.ENTRY_SHORT:
                ctx.log("no shorting")
                continue

            if change.is_exit:
                # Closing orders don't require or use buying power
                if not signal.is_exit:
                    ctx.log("no exit signal")
                    continue

                rounded_size = round(-pos_size * abs(Decimal(signal.rating)), self.size_digits)
                if rounded_size.is_zero():
                    ctx.log("cannot exit with order size zero")
                    continue
                new_orders = self._get_orders(symbol, rounded_size, item, signal, event.time)
                orders += new_orders
            else:
                if available < 0:
                    ctx.log("no more available buying power")
                    continue

                if not signal.is_entry:
                    ctx.log("no entry signal")
                    continue

                if available < min_order_value:
                    ctx.log("available buying power below minimum order value")
                    continue

                available_order_value = min(available, max_order_value, max_pos_value - abs(account.position_value(symbol)))
                if available_order_value < min_order_value:
                    ctx.log("calculated available order value below minimum order value")
                    continue

                contract_price = account.contract_value(symbol, Decimal(1), price)
                order_size = self._get_order_size(signal.rating, contract_price, available_order_value)

                if order_size.is_zero():
                    ctx.log("calculated order size is zero")
                    continue

                order_value = abs(account.contract_value(symbol, order_size, price))
                if abs(order_value) > available:
                    ctx.log(
                        "order value above available buying power",
                        order_value=order_value,
                        available=available,
                    )
                    continue
                if abs(order_value) < min_order_value:
                    ctx.log(
                        "order value below minimum order value",
                        order_value=order_value,
                        min_order_value=min_order_value,
                    )
                    continue

                new_orders = self._get_orders(symbol, order_size, item, signal, event.time)
                if new_orders:
                    orders += new_orders
                    available -= order_value

        return orders

    def _get_orders(self, symbol: str, size: Decimal, item: PriceItem, signal: Signal, time: datetime) -> list[Order]:
        # pylint: disable=unused-argument
        """Return zero or more orders for the provided symbol and size.

        Default is a MarketOrder. Overwrite this method to create different order types.
        """

        return [Order(symbol, size)]

    def __repr__(self) -> str:
        attrs = " ".join([f"{k}={v}" for k, v in self.__dict__.items() if not k.startswith("_")])
        return f"FlexTrader({attrs})"


class FlexLimitOrderTrader(FlexTrader):
    """A FlexTrader version that returns a limit order"""

    def __init__(
        self,
        one_order_only=True,
        size_fractions=0,
        safety_margin_perc=0.05,
        shorting=False,
        max_order_perc=0.05,
        min_order_perc=0.02,
        max_position_perc=0.05,
        price_type="DEFAULT",
        shuffle_signals=False,
        gtd=timedelta(days=3),
    ) -> None:
        super().__init__(
            one_order_only,
            size_fractions,
            safety_margin_perc,
            shorting,
            max_order_perc,
            min_order_perc,
            max_position_perc,
            price_type,
            shuffle_signals,
        )
        self.gtd_timedelta = gtd

    def _get_orders(self, symbol: str, size: Decimal, item: PriceItem, signal: Signal, time: datetime) -> list[Order]:
        # pylint: disable=unused-argument
        """Return a single limit-order with the limit the current price a GTD with a configurable 3 days from now."""
        gtd = time + self.gtd_timedelta
        limit = item.price(self.price_type)
        return [Order(symbol, size, limit, gtd)]
