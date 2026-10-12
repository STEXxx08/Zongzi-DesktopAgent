import re
import time

import requests

from condata.pipeline.web_search import ExchangeRatePrediction
from web_search.exchange_rate.config import ExchangeRateCoreConfig


class ExchangeRateModel:
    CURRENCIES = {
        "人民币": "CNY", "元": "CNY", "cny": "CNY", "rmb": "CNY",
        "美元": "USD", "美金": "USD", "usd": "USD",
        "欧元": "EUR", "eur": "EUR",
        "日元": "JPY", "日币": "JPY", "jpy": "JPY",
        "英镑": "GBP", "gbp": "GBP",
        "港币": "HKD", "港元": "HKD", "hkd": "HKD",
        "韩元": "KRW", "krw": "KRW",
        "澳元": "AUD", "澳币": "AUD", "aud": "AUD",
        "加元": "CAD", "cad": "CAD",
        "新加坡元": "SGD", "新币": "SGD", "sgd": "SGD",
    }

    def __init__(self, config: ExchangeRateCoreConfig):
        self.config = config
        self._session = requests.Session()
        self._cache: dict[str, tuple[float, float, str]] = {}

    def _parse(self, text: str) -> tuple[float, str, str]:
        amount_match = re.search(r"(?<![A-Za-z])([0-9]+(?:\.[0-9]+)?)", text)
        amount = float(amount_match.group(1)) if amount_match else 1.0
        found = []
        lowered = text.casefold()
        for name, code in sorted(self.CURRENCIES.items(), key=lambda item: -len(item[0])):
            start = 0
            while True:
                index = lowered.find(name.casefold(), start)
                if index < 0:
                    break
                found.append((index, code))
                start = index + len(name)
        currencies = []
        for _, code in sorted(found):
            if code not in currencies:
                currencies.append(code)
        base = currencies[0] if currencies else self.config.default_base.upper()
        quote = currencies[1] if len(currencies) > 1 else self.config.default_quote.upper()
        return amount, base, quote

    def convert(self, text: str) -> ExchangeRatePrediction:
        amount, base, quote = self._parse(text)
        if base == quote:
            return ExchangeRatePrediction(
                query=text, amount=amount, base=base, quote=quote,
                rate=1.0, converted_amount=amount, source="identity",
            )
        key = f"{base}/{quote}"
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] <= self.config.cache_ttl_seconds:
            rate, date = cached[1], cached[2]
            return ExchangeRatePrediction(
                query=text, amount=amount, base=base, quote=quote, rate=rate,
                converted_amount=amount * rate, date=date, source="Frankfurter",
                cached=True,
            )
        response = self._session.get(
            f"{self.config.endpoint.rstrip('/')}/{base}/{quote}",
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        rate = float(payload["rate"])
        date = str(payload.get("date") or "")
        self._cache[key] = (time.monotonic(), rate, date)
        return ExchangeRatePrediction(
            query=text, amount=amount, base=base, quote=quote, rate=rate,
            converted_amount=amount * rate, date=date, source="Frankfurter",
        )