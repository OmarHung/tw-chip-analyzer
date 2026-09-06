from collections import deque
from dataclasses import dataclass
import numpy as np

@dataclass
class Tick:
    ts: float
    price: float
    volume: float
    bid1: float | None = None
    ask1: float | None = None

class OrderFlowEngine:
    def __init__(self, maxlen=5000):
        self.ticks = deque(maxlen=maxlen)
        self.cvd = 0.0
        self.large_buy = 0.0
        self.large_sell = 0.0
        self.recent_volumes = deque(maxlen=2000)

    def classify_side(self, tick: Tick) -> int:
        if tick.ask1 is not None and tick.price >= tick.ask1:
            return 1
        if tick.bid1 is not None and tick.price <= tick.bid1:
            return -1

        # fallback tick rule
        if self.ticks:
            prev = self.ticks[-1]
            if tick.price > prev.price:
                return 1
            if tick.price < prev.price:
                return -1
        return 0

    def on_tick(self, tick: Tick):
        side = self.classify_side(tick)
        signed = side * tick.volume
        self.cvd += signed

        threshold = None
        if len(self.recent_volumes) >= 100:
            threshold = float(np.quantile(self.recent_volumes, 0.95))

        if threshold is not None and tick.volume >= threshold:
            if side > 0:
                self.large_buy += tick.volume
            elif side < 0:
                self.large_sell += tick.volume

        self.recent_volumes.append(tick.volume)
        self.ticks.append(tick)

    @staticmethod
    def order_book_imbalance(bid_volumes, ask_volumes) -> float:
        b = float(sum(bid_volumes))
        a = float(sum(ask_volumes))
        return 0.0 if b + a == 0 else (b - a) / (b + a)
