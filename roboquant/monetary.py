import io
import logging
import os
from time import time
import zipfile
from abc import ABC, abstractmethod
from bisect import bisect_left
from collections import defaultdict
from csv import reader
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Any, Dict, List

import requests


logger = logging.getLogger(__name__)


class Currency(str):
    """Currency class represents a monetary currency and is a subclass of `str`.

    It is possible to create an `Amount` using a combination of a `number` and a `Currency`:

        amount1 = 100@USD
        amount2 = 200.50@EUR
    """

    def __rmatmul__(self, other: float | int):
        assert isinstance(other, (float, int))
        return Amount(self, other)

    def __call__(self, other: float | int):
        return Amount(self, other)


# Commonly used currencies
USD = Currency("USD")
"""US Dollar"""

EUR = Currency("EUR")
"""Euro"""

JPY = Currency("JPY")
"""Japanese Yen"""

GBP = Currency("GBP")
"""British Pound"""

CHF = Currency("CHF")
"""Swiss Franc"""

INR = Currency("INR")
"""Indian Rupee"""

AUD = Currency("AUD")
"""Australian Dollar"""

CAD = Currency("CAD")
"""Canadian Dollar"""

NZD = Currency("NZD")
"""New Zealand Dollar"""

CMY = Currency("CMY")
"""Chinese Yuan"""

HKD = Currency("HKD")
"""Hong Kong Dollar"""

BTC = Currency("BTC")
"""Bitcoin"""

ETH = Currency("ETH")
"""Ethereum"""


class CurrencyConverter(ABC):
    """Abstract base class for currency converters"""

    @abstractmethod
    def convert(self, amount: "Amount", to_currency: Currency, dt: datetime) -> float:
        """Convert the monetary amount into another currency at the provided time."""
        ...

    def register(self):
        """Register this converter to be used for conversions between amounts"""
        Amount.register_converter(self)


class NoConversion(CurrencyConverter):
    """The default currency converter that doesn't convert between currencies"""

    def convert(self, amount: "Amount", to_currency: Currency, dt: datetime) -> float:
        raise NotImplementedError("The default NoConversion doesn't support any conversions")


class ECBConversion(CurrencyConverter):
    """CurrencyConverter that retrieves it exchange rates from the ECB (European Central Bank)."""

    __file_name = Path.home() / ".roboquant" / "eurofxref-hist.csv"

    def __init__(self, force_download=False):
        self._rates: Dict[Currency, List[Any]] = {}
        if force_download or not self.up_to_date():
            self._download()
        self._parse()

    def _download(self):
        logging.info("downloading the latest ECB exchange rates")
        r = requests.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip", timeout=10)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            p = Path.home() / ".roboquant"
            z.extractall(p)

    def up_to_date(self):
        """True if there is already a recently (< 12 hours) downloaded file"""
        if not self.__file_name.exists():
            return False
        diff = time() - os.path.getctime(self.__file_name)
        return diff < 12.0 * 3600.0

    @property
    def currencies(self) -> set[Currency]:
        """return the set of supported currencies"""
        return set(self._rates.keys())

    def _parse(self):
        self._rates = {EUR: [(datetime.fromisoformat("2000-01-01T15:00:00+00:00"), 1.0)]}
        with open(self.__file_name, "r", encoding="utf8") as csv_file:
            csv_reader = reader(csv_file)
            header = next(csv_reader)[1:]
            currencies = [Currency(e) for e in header if e]
            for e in header:
                c = Currency(e)
                self._rates[c] = []

            header_len = len(currencies)
            for row in csv_reader:
                d = datetime.fromisoformat(row[0] + "T15:00:00+00:00")
                for idx in range(header_len):
                    v = row[idx + 1]
                    if v and v != "N/A":
                        value = (d, float(v))
                        self._rates[currencies[idx]].append(value)

        for v in self._rates.values():
            v.reverse()

    def _get_rate(self, currency: Currency, dt: datetime) -> float:
        rates = self._rates[currency]
        idx = bisect_left(rates, dt, key=lambda r: r[0])
        idx = min(idx, len(rates) - 1)
        return rates[idx][1]

    def convert(self, amount: "Amount", to_currency: Currency, dt: datetime) -> float:
        return amount.value * self._get_rate(to_currency, dt) / self._get_rate(amount.currency, dt)


class StaticConversion(CurrencyConverter):
    """Currency converter that uses static rates to convert between different currencies.
    This converter doesn't take time into consideration.
    """

    def __init__(self, base_currency: Currency, rates: dict[Currency, float]):
        super().__init__()
        self.base_currency = base_currency
        self.rates = rates
        self.rates[base_currency] = 1.0

    def convert(self, amount: "Amount", to_currency: Currency, dt: datetime) -> float:
        return amount.value * self.rates[to_currency] / self.rates[amount.currency]


class One2OneConversion(CurrencyConverter):
    """Currency converter that always converts 1 to 1 between currencies.
    So for example, 1 USD equals 1 EUR equals 1 GPB"""

    def convert(self, amount: "Amount", to_currency: str, dt: datetime) -> float:
        return amount.value


@dataclass(frozen=True, slots=True)
class Amount:
    """A monetary value denoted in a single `Currency`. Amounts are immutable"""

    currency: Currency
    value: float
    __converter: ClassVar[CurrencyConverter] = NoConversion()

    @staticmethod
    def register_converter(converter: CurrencyConverter):
        """Register a new currency converter to handle conversions between different currencies.
        It will replace the current registered converter.
        """
        Amount.__converter = converter

    def items(self):
        return [(self.currency, self.value)]

    def amounts(self):
        return [self]

    def __add__(self, other: "Amount") -> "Wallet":
        """Add another amount to this amount.
        This will always return a wallet, even if both amounts are denoted in the same currency.
        """
        return Wallet(self, other)

    def __matmul__(self, other: Currency) -> "Amount":
        dt = datetime.now(tz=timezone.utc)
        return Amount(other, self.convert_to(other, dt))

    def convert_to(self, currency: Currency, dt: datetime) -> float:
        """Convert this amount to another currency and return the monetary value.
        If an exchange rate is required, it will invoke the registered `Amount.converter` under the hood.
        """
        if currency == self.currency:
            return self.value
        if self.value == 0.0:
            return 0.0

        return Amount.__converter.convert(self, currency, dt)

    def __repr__(self) -> str:
        return f"{self.value:,.2f}@{self.currency}"


class Wallet(defaultdict[Currency, float]):
    """A wallet holds monetary values of different currencies.
    You can add wallets together, subtract them, or convert them to a single currency.
    """

    def __init__(self, *amounts: Amount):
        super().__init__(float)
        for amount in amounts:
            self[amount.currency] += amount.value

    def amounts(self):
        """Return the amounts contained in this wallet"""
        return [Amount(k, v) for k, v in self.items()]

    def __iadd__(self, other: "Amount | Wallet"):
        for k, v in other.items():
            self[k] += v
        return self

    def __isub__(self, other: "Amount | Wallet"):
        for k, v in other.items():
            self[k] -= v
        return self

    def __add__(self, other: "Amount | Wallet"):
        result = self.deepcopy()
        for k, v in other.items():
            result[k] += v
        return result

    def __sub__(self, other: "Amount | Wallet"):
        result = self.deepcopy()
        for k, v in other.items():
            result[k] -= v
        return result

    def __matmul__(self, other: Currency) -> Amount:
        dt = datetime.now(tz=timezone.utc)
        return Amount(other, self.convert_to(other, dt))

    def deepcopy(self) -> "Wallet":
        result = Wallet()
        result.update(self)
        return result

    def convert_to(self, currency: Currency, dt: datetime) -> float:
        """convert all the amounts hold in this wallet to a single currency and return the value"""
        return sum(amount.convert_to(currency, dt) for amount in self.amounts())

    def __repr__(self) -> str:
        return " + ".join([f"{a}" for a in self.amounts()])
