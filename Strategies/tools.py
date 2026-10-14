"""Indicator helpers for strategy files. Each takes pandas Series and returns a Series
with one value per day (using only that day and earlier days, never the future)."""
import pandas as pd


def sma(x, n):
    """Simple moving average of the last n days."""
    return x.rolling(n).mean()


def ema(x, n):
    """Exponential moving average."""
    return x.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    """Relative Strength Index (0-100), Wilder's smoothing."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def atr(d, n=14):
    """Average True Range of a daily DataFrame (High, Low, Close)."""
    c = d["Close"]
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - c.shift()).abs(), (d["Low"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def highest(x, n):
    """Highest value of the PREVIOUS n days (today not included)."""
    return x.rolling(n).max().shift(1)


def lowest(x, n):
    """Lowest value of the PREVIOUS n days (today not included)."""
    return x.rolling(n).min().shift(1)


def bollinger(close, n=20, k=2.0):
    """(middle, upper, lower) Bollinger Bands."""
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def crossed_above(a, b):
    """True on the day a goes from <= b to > b (b can be a Series or a number)."""
    return (a > b) & (a.shift() <= (b.shift() if isinstance(b, pd.Series) else b))


def crossed_below(a, b):
    """True on the day a goes from >= b to < b."""
    return (a < b) & (a.shift() >= (b.shift() if isinstance(b, pd.Series) else b))
