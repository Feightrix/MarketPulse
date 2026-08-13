import math
import numpy as np
import pandas as pd
import backtest


def desired_weights_fast(close, i, short_lb, long_lb, trend_lb, top_n, target_vol, vol_lb):
    signal_i = i - 1
    if signal_i < max(short_lb, long_lb, trend_lb, vol_lb) + 2:
        return pd.Series(0.0, index=close.columns)

    px = close.iloc[signal_i]
    mom_s = px / close.iloc[signal_i - short_lb] - 1.0
    mom_l = px / close.iloc[signal_i - long_lb] - 1.0
    sma = close.iloc[signal_i - trend_lb + 1:signal_i + 1].mean()
    trend = px / sma - 1.0
    score = 0.45 * mom_s + 0.55 * mom_l
    eligible = (mom_l > 0) & (trend > 0)
    ranked = score[eligible].sort_values(ascending=False)
    chosen = list(ranked.head(top_n).index)
    w = pd.Series(0.0, index=close.columns)
    if not chosen:
        return w

    gross = 1.0
    if target_vol is not None:
        # Only calculate the small rolling window needed for this signal.
        window = close[chosen].iloc[signal_i - vol_lb:signal_i + 1]
        daily = window.pct_change().dropna()
        vols = daily.std(ddof=0) * math.sqrt(252)
        basket_vol = float(np.sqrt(np.mean(np.square(vols.values))))
        if np.isfinite(basket_vol) and basket_vol > 0:
            gross = min(1.0, target_vol / basket_vol)
    each = gross / len(chosen)
    for s in chosen:
        w[s] = each
    return w


backtest.desired_weights = desired_weights_fast
backtest.main()
