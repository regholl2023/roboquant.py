from array import array
from datetime import timedelta
import threading
import time
from typing import Literal

from alpaca.data.live.crypto import CryptoDataStream
from alpaca.data.live.stock import StockDataStream
from alpaca.data.live.option import OptionDataStream

from roboquant.feeds import AggregatorFeed
from roboquant.feeds.feedutil import get_sp500_symbols
from roboquant.config import Config
from roboquant.event import Event, Quote, Trade, Bar
from roboquant.feeds.eventchannel import EventChannel

from roboquant.feeds.feed import Feed


class AlpacaLiveFeed(Feed):

    def __init__(self, market: Literal["stock", "crypto", "option"] = "stock") -> None:
        super().__init__()
        config = Config()
        api_key = config.get("alpaca.public.key")
        secret_key = config.get("alpaca.secret.key")
        match market:
            case "stock":
                self.stream = StockDataStream(api_key, secret_key)
            case "crypto":
                self.stream = CryptoDataStream(api_key, secret_key)
            case "option":
                self.stream = OptionDataStream(api_key, secret_key)
            case _:
                raise ValueError(f"unsupported value market is {market}")

        thread = threading.Thread(None, self.stream.run, daemon=True)
        thread.start()
        self._channel = None

    def play(self, channel: EventChannel):
        self._channel = channel
        while not channel.is_closed:
            time.sleep(1)
        self._channel = None

    async def __handle_trades(self, data):
        if self._channel:
            item = Trade(data.symbol, data.price, data.size)
            event = Event(data.timestamp, [item])
            self._channel.put(event)

    async def __handle_bars(self, data):
        if self._channel:
            item = Bar(data.symbol, array("f", [data.open, data.high, data.low, data.close, data.volume]))
            event = Event(data.timestamp, [item])
            self._channel.put(event)

    async def __handle_quotes(self, data):
        if self._channel:
            item = Quote(data.symbol, array("f", [data.ask_price, data.ask_size, data.bid_price, data.bid_size]))
            event = Event(data.timestamp, [item])
            self._channel.put(event)

    def subscribe_trades(self, *symbols: str):
        self.stream.subscribe_trades(self.__handle_trades, *symbols)

    def subscribe_quotes(self, *symbols: str):
        self.stream.subscribe_quotes(self.__handle_quotes, *symbols)

    def subscribe_bars(self, *symbols: str):
        self.stream.subscribe_bars(self.__handle_bars, *symbols)


def run():
    alpaca_feed = AlpacaLiveFeed()
    # feed.subscribe_trades("BTC/USD", "ETH/USD")
    stocks = get_sp500_symbols()[:30]
    alpaca_feed.subscribe_quotes(*stocks)

    # feed.subscribe("SPXW240312C05190000")
    feed = AggregatorFeed(alpaca_feed,  timedelta(seconds=15), item_type="quote")

    channel = feed.play_background()
    while event := channel.get(30.0):
        print(event)


if __name__ == "__main__":
    run()
