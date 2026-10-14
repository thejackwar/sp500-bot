"""Price formatting shared by stocks and crypto.
Prices of $1 or more keep 2 decimals (all stock alerts look exactly as before); smaller prices,
like DOGE at $0.21, get enough decimals to show 4 significant digits (0.2134)."""
import math


def px(x):
    x = float(x)
    a = abs(x)
    if a >= 1 or a == 0 or not math.isfinite(a):
        return f"{x:.2f}"
    return f"{x:.{min(10, 3 - math.floor(math.log10(a)))}f}"
