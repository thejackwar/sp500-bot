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


# The market's daily closes (SPY for stocks, BTC for crypto), set by backtest.py before the
# strategies run; MARKET_MA is the backtest's filter length (200 for stocks, 50 for crypto).
MARKET = None
MARKET_MA = 200


def market_up(d, n=None):
    """True on the days the market (SPY / BTC) closed above its n-day average. Use it as a filter:
    entries(d) & tools.market_up(d). Always True when the market prices aren't available."""
    if MARKET is None:
        return pd.Series(True, index=d.index)
    c = MARKET
    up = c > c.rolling(n or MARKET_MA).mean()
    return up.reindex(d.index).ffill().fillna(False).astype(bool)


def change(x, n):
    """Return over the last n days (0.10 = +10%)."""
    return x / x.shift(n) - 1


def adx(d, n=14):
    """Average Directional Index (0-100), Wilder's smoothing: how strong the trend is, up or down.
    Above ~25 = a real trend, below ~20 = a sideways market."""
    up, down = d["High"].diff(), -d["Low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    a = atr(d, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def bot_long(d):
    """The bot's STRONG LONG on the daily chart (rules.py): close above the 200-day MA, a new
    20-day high (Donchian), and Bollinger confirms (close above the upper band, or a squeeze =
    bands narrowest in ~6 months, within the last 10 days). Daily chart only: the live bot also
    checks 15-min/hourly candles, which this leaves out."""
    c = d["Close"]
    mid, upper, lower = bollinger(c, 20, 2.0)
    width = (upper - lower) / mid
    squeeze = width <= width.rolling(125).min().shift(1)
    confirms = (c > upper) | squeeze.rolling(11, min_periods=1).max().astype(bool)
    return (c > sma(c, 200)) & crossed_above(c, highest(d["High"], 20)) & confirms
