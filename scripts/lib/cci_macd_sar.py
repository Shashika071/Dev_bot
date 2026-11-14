"""Closed-minute signals for the default settings of v100-cci-macd-sar.pine.

The input is chronological R_100 candles with Unix second timestamps and OHLC
prices in normal price units. A gap starts a new indicator history. Returned
indices identify the closed signal candle, whose close is known at time + 60.
"""

from collections import deque
from math import isfinite


class _ParabolicSar:
    """TradingView ta.sar(0.02, 0.02, 0.20), including reversal and band ordering.

    https://www.tradingview.com/pine-script-reference/v6/#fun_ta.sar
    """

    def __init__(self):
        self.previous_bars = deque(maxlen=2)
        self.value = None
        self.extreme = None
        self.acceleration = 0.02
        self.below = False

    def update(self, high, low, close):
        if self.previous_bars:
            previous_high, previous_low, previous_close = self.previous_bars[-1]
            first_trend_bar = self.value is None
            if first_trend_bar:
                self.below = close > previous_close
                self.extreme = high if self.below else low
                self.value = previous_low if self.below else previous_high

            self.value += self.acceleration * (self.extreme - self.value)
            if self.below and self.value > low:
                self.below = False
                self.value, self.extreme = max(high, self.extreme), low
                self.acceleration = 0.02
                first_trend_bar = True
            elif not self.below and self.value < high:
                self.below = True
                self.value, self.extreme = min(low, self.extreme), high
                self.acceleration = 0.02
                first_trend_bar = True

            if not first_trend_bar:
                if (self.below and high > self.extreme) or (not self.below and low < self.extreme):
                    self.extreme = high if self.below else low
                    self.acceleration = min(0.20, self.acceleration + 0.02)
            for prior_high, prior_low, _ in self.previous_bars:
                self.value = min(self.value, prior_low) if self.below else max(self.value, prior_high)

        self.previous_bars.append((high, low, close))
        return self.value


def _cents(price):
    if not isfinite(price) or price <= 0:
        raise ValueError(f"Invalid R_100 price {price}")
    units = price * 100
    cents = round(units)
    if abs(units - cents) > 1e-7:
        raise ValueError(f"R_100 price off the 0.01 increment: {price}")
    return cents


def signals(candles, warmup=260):
    """Yield (index, 'RISE' or 'FALL') for concurrent zero and MACD crosses."""
    if warmup < 21:
        raise ValueError("Need a previous and current 20-bar CCI")
    previous_time = None
    for index, candle in enumerate(candles):
        timestamp = candle["time"]
        if timestamp % 60 or (previous_time is not None and timestamp <= previous_time):
            raise ValueError(f"Unaligned, duplicate or unordered M1 bar at {timestamp}")
        if candle.get("isClosed", True) is not True:
            raise ValueError(f"Unfinished M1 bar at {timestamp}")
        high, low, close = candle["high"], candle["low"], candle["close"]
        if high < close or low > close:
            raise ValueError(f"Invalid high/low at {timestamp}")
        if previous_time is None or timestamp != previous_time + 60:
            bars = 0
            sar = _ParabolicSar()
            typicals = deque()
            typical_sum = 0
            previous_cci = previous_macd_diff = None
            fast = slow = close
            signal = 0.0
        bars += 1
        previous_time = timestamp

        # The positive CCI denominator does not affect a zero crossing. Integer
        # arithmetic on (H+L+C) gives an exact zero when HLC3 equals its SMA.
        typical = _cents(high) + _cents(low) + _cents(close)
        if len(typicals) == 20:
            typical_sum -= typicals.popleft()
        typicals.append(typical)
        typical_sum += typical
        cci = None
        if len(typicals) == 20:
            centered = 20 * typical - typical_sum
            if centered or any(item != typical for item in typicals):
                cci = centered

        fast += (2 / 13) * (close - fast)
        slow += (2 / 27) * (close - slow)
        macd = fast - slow
        signal += (2 / 10) * (macd - signal)
        macd_diff = macd - signal
        sar_value = sar.update(high, low, close)
        if bars >= warmup and cci is not None and previous_cci is not None:
            if previous_cci <= 0 < cci and previous_macd_diff <= 0 < macd_diff and sar_value < close:
                yield index, "RISE"
            elif previous_cci >= 0 > cci and previous_macd_diff >= 0 > macd_diff and sar_value > close:
                yield index, "FALL"
        previous_cci, previous_macd_diff = cci, macd_diff
