from roboquant.trackers.metric import Metric


class PriceItemMetric(Metric):
    """Tracks a number of basic progress metrics:
    - total number of events, items, signals and orders

    This tracker adds little overhead to a run, both CPU and memory wise.
    """

    def __init__(self, *symbols: str, price_type="DEFAULT", volume_type="DEFAULT"):
        self.symbols = symbols
        self.price_type = price_type
        self.volume_type = volume_type

    def calc(self, event, account, signals, orders) -> dict[str, float]:
        result = {}
        for symbol, item in event.price_items.items():
            if symbol in self.symbols or not self.symbols:
                prefix = f"item/{symbol.lower()}"
                result[f"{prefix}/price"] = item.price(self.price_type)
                result[f"{prefix}/volume"] = item.volume(self.volume_type)

        return result
