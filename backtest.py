import io
import json
import math
import urllib.request
from itertools import product

import numpy as np
import pandas as pd

DATA_URL = "https://raw.githubusercontent.com/vivek-v-rao/OHLC-Vol/refs/heads/main/prices_ohlc.csv"
SYMBOLS = ["SPY", "QQQ", "GLD", "HYG", "USO"]
COST_BPS_ONE_WAY = 5.0
STARTING_CAPITAL = 100.0


def load_data():
    with urllib.request.urlopen(DATA_URL, timeout=30) as r:
        raw = r.read()
    df = pd.read_csv(io.BytesIO(raw), header=[0, 1], index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index)
    adj_close = pd.DataFrame(index=df.index)
    adj_open = pd.DataFrame(index=df.index)
    for s in SYMBOLS:
        close = pd.to_numeric(df[(s, "Close")], errors="coerce")
        aclose = pd.to_numeric(df[(s, "Adj Close")], errors="coerce")
        opn = pd.to_numeric(df[(s, "Open")], errors="coerce")
        factor = aclose / close
        adj_close[s] = aclose
        adj_open[s] = opn * factor
    good = adj_close.notna().all(axis=1) & adj_open.notna().all(axis=1)
    return adj_open.loc[good], adj_close.loc[good]


def desired_weights(close, i, short_lb, long_lb, trend_lb, top_n, target_vol, vol_lb):
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
        daily = close[chosen].pct_change().iloc[signal_i - vol_lb + 1:signal_i + 1]
        vols = daily.std(ddof=0) * math.sqrt(252)
        basket_vol = float(np.sqrt(np.mean(np.square(vols.values))))
        if np.isfinite(basket_vol) and basket_vol > 0:
            gross = min(1.0, target_vol / basket_vol)
    each = gross / len(chosen)
    for s in chosen:
        w[s] = each
    return w


def run_strategy(opn, close, params, start=None, end=None, record_series=False):
    start_ts = pd.Timestamp(start) if start else close.index[0]
    end_ts = pd.Timestamp(end) if end else close.index[-1]
    mask = (close.index >= start_ts) & (close.index <= end_ts)
    idx = close.index[mask]
    if len(idx) < 300:
        return None

    # Include enough pre-period history for signals.
    first_loc = close.index.get_loc(idx[0])
    last_loc = close.index.get_loc(idx[-1])
    warmup = max(params["short_lb"], params["long_lb"], params["trend_lb"], params["vol_lb"]) + 5
    sim_start = max(warmup, first_loc)

    nav = 1.0
    current = pd.Series(0.0, index=close.columns)
    nav_hist, ret_hist, dates = [], [], []
    rebalances = 0
    total_turnover = 0.0

    for i in range(sim_start, last_loc + 1):
        d = close.index[i]
        if d < start_ts:
            continue
        prev_nav = nav

        prev_close = close.iloc[i - 1]
        today_open = opn.iloc[i]
        overnight_asset_ret = today_open / prev_close - 1.0
        nav *= 1.0 + float((current * overnight_asset_ret).sum())

        if (i - sim_start) % params["rebalance"] == 0:
            desired = desired_weights(
                close, i,
                params["short_lb"], params["long_lb"], params["trend_lb"],
                params["top_n"], params["target_vol"], params["vol_lb"]
            )
            turnover = float((desired - current).abs().sum())
            if turnover > 1e-10:
                nav *= max(0.0, 1.0 - turnover * COST_BPS_ONE_WAY / 10000.0)
                rebalances += 1
                total_turnover += turnover
            current = desired

        intraday_asset_ret = close.iloc[i] / today_open - 1.0
        nav *= 1.0 + float((current * intraday_asset_ret).sum())

        daily_ret = nav / prev_nav - 1.0
        nav_hist.append(nav)
        ret_hist.append(daily_ret)
        dates.append(d)

    if len(nav_hist) < 100:
        return None
    nav_s = pd.Series(nav_hist, index=pd.DatetimeIndex(dates))
    ret_s = pd.Series(ret_hist, index=nav_s.index)
    metrics = calc_metrics(nav_s, ret_s, rebalances, total_turnover)
    if record_series:
        metrics["daily_returns"] = ret_s
        metrics["nav_series"] = nav_s
    return metrics


def calc_metrics(nav, rets, rebalances, turnover):
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 252)
    total_return = float(nav.iloc[-1] - 1.0)
    cagr = float(nav.iloc[-1] ** (1.0 / years) - 1.0)
    vol = float(rets.std(ddof=0) * math.sqrt(252))
    sharpe = float((rets.mean() / rets.std(ddof=0)) * math.sqrt(252)) if rets.std(ddof=0) > 0 else 0.0
    dd = nav / nav.cummax() - 1.0
    max_dd = float(dd.min())
    monthly = nav.resample("ME").last().pct_change().dropna()
    positive_months = float((monthly > 0).mean()) if len(monthly) else 0.0
    worst_month = float(monthly.min()) if len(monthly) else 0.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annual_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "positive_months": positive_months,
        "worst_month": worst_month,
        "rebalances": int(rebalances),
        "turnover": float(turnover),
        "ending_100": float(STARTING_CAPITAL * nav.iloc[-1]),
    }


def spy_baseline(close, start, end):
    p = close["SPY"].loc[pd.Timestamp(start):pd.Timestamp(end)].dropna()
    r = p.pct_change().fillna(0.0)
    nav = (1.0 + r).cumprod()
    return calc_metrics(nav, r, 1, 1.0)


def score_train(m):
    return m["sharpe"] + 0.70 * m["cagr"] - 0.55 * abs(m["max_drawdown"])


def score_robust(train, val):
    if train["cagr"] <= 0 or val["cagr"] <= 0:
        return -999
    worst_sharpe = min(train["sharpe"], val["sharpe"])
    worst_cagr = min(train["cagr"], val["cagr"])
    worst_dd = max(abs(train["max_drawdown"]), abs(val["max_drawdown"]))
    return worst_sharpe + 0.55 * worst_cagr - 0.50 * worst_dd


def bootstrap_profit_probability(ret_s, days=252, trials=5000, block=5, seed=20260813):
    x = ret_s.dropna().values
    if len(x) < block + 2:
        return 0.0
    rng = np.random.default_rng(seed)
    wins = 0
    for _ in range(trials):
        out = []
        while len(out) < days:
            j = int(rng.integers(0, len(x) - block + 1))
            out.extend(x[j:j + block])
        out = np.array(out[:days])
        terminal = float(np.prod(1.0 + out))
        wins += terminal > 1.0
    return wins / trials


def fmt_pct(x):
    return f"{x * 100:.2f}%"


def main():
    opn, close = load_data()
    data_end = close.index[-1].strftime("%Y-%m-%d")
    train = ("2010-01-04", "2018-12-31")
    val = ("2019-01-01", "2022-12-31")
    holdout = ("2023-01-01", data_end)

    grid = []
    for short_lb, long_lb, trend_lb, top_n, rebalance, target_vol, vol_lb in product(
        [63, 126], [126, 189, 252], [150, 200], [1, 2], [5, 10, 21], [None, 0.12, 0.18], [20, 60]
    ):
        if short_lb >= long_lb:
            continue
        grid.append({
            "short_lb": short_lb,
            "long_lb": long_lb,
            "trend_lb": trend_lb,
            "top_n": top_n,
            "rebalance": rebalance,
            "target_vol": target_vol,
            "vol_lb": vol_lb,
        })

    train_ranked = []
    for p in grid:
        m = run_strategy(opn, close, p, *train)
        if m:
            train_ranked.append((score_train(m), p, m))
    train_ranked.sort(key=lambda x: x[0], reverse=True)
    finalists = train_ranked[:30]

    evaluated = []
    for train_score, p, tm in finalists:
        vm = run_strategy(opn, close, p, *val)
        if vm:
            evaluated.append((score_robust(tm, vm), p, tm, vm))
    evaluated.sort(key=lambda x: x[0], reverse=True)
    robust_score, best, train_m, val_m = evaluated[0]

    hold_m = run_strategy(opn, close, best, *holdout, record_series=True)
    full_m = run_strategy(opn, close, best, "2010-01-04", data_end, record_series=True)
    hold_daily = hold_m.pop("daily_returns")
    hold_m.pop("nav_series")
    full_daily = full_m.pop("daily_returns")
    full_m.pop("nav_series")

    prob_1y = bootstrap_profit_probability(full_daily, days=252, trials=5000, block=5)
    prob_3m = bootstrap_profit_probability(full_daily, days=63, trials=5000, block=5)

    baselines = {
        "train": spy_baseline(close, *train),
        "validation": spy_baseline(close, *val),
        "holdout": spy_baseline(close, *holdout),
    }

    passed = (
        train_m["cagr"] > 0 and val_m["cagr"] > 0 and hold_m["cagr"] > 0
        and hold_m["max_drawdown"] > -0.25
        and hold_m["sharpe"] > 0.45
        and prob_1y >= 0.60
    )

    results = {
        "data_source": DATA_URL,
        "data_end": data_end,
        "assumed_one_way_slippage_bps": COST_BPS_ONE_WAY,
        "starting_capital": STARTING_CAPITAL,
        "parameter_combinations_tested": len(grid),
        "selection_process": "Tune on 2010-2018, select robust finalist using 2019-2022, then reveal untouched 2023-data_end holdout.",
        "best_parameters": best,
        "train": train_m,
        "validation": val_m,
        "holdout": hold_m,
        "full_period": full_m,
        "spy_baseline": baselines,
        "bootstrap_probability_profitable_3m": prob_3m,
        "bootstrap_probability_profitable_1y": prob_1y,
        "phase2_pass": bool(passed),
        "guaranteed_profit": False,
    }
    with open("backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    lines = [
        "# MarketPulse Phase 2 — Strategy Validation",
        "",
        f"**Data through:** {data_end}",
        f"**Parameter combinations tested:** {len(grid)}",
        f"**Trading friction assumed:** {COST_BPS_ONE_WAY:.0f} bps one-way per unit of turnover",
        "",
        "## Selected strategy",
        "",
        f"- Short momentum lookback: {best['short_lb']} trading days",
        f"- Long momentum lookback: {best['long_lb']} trading days",
        f"- Trend filter: {best['trend_lb']}-day moving average",
        f"- Hold top: {best['top_n']} qualifying asset(s)",
        f"- Rebalance every: {best['rebalance']} trading days",
        f"- Volatility target: {('None' if best['target_vol'] is None else fmt_pct(best['target_vol']))}",
        f"- Volatility lookback: {best['vol_lb']} trading days",
        "- Universe: SPY, QQQ, GLD, HYG, USO",
        "- Goes to cash when no asset has both positive long momentum and a positive trend filter",
        "",
        "## Results",
        "",
        "| Period | CAGR | Sharpe | Max drawdown | Positive months | $100 became | SPY CAGR | SPY drawdown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, m, b in [
        ("Train 2010-2018", train_m, baselines["train"]),
        ("Validation 2019-2022", val_m, baselines["validation"]),
        (f"Untouched holdout 2023-{data_end}", hold_m, baselines["holdout"]),
    ]:
        lines.append(
            f"| {label} | {fmt_pct(m['cagr'])} | {m['sharpe']:.2f} | {fmt_pct(m['max_drawdown'])} | "
            f"{fmt_pct(m['positive_months'])} | ${m['ending_100']:.2f} | {fmt_pct(b['cagr'])} | {fmt_pct(b['max_drawdown'])} |"
        )
    lines += [
        "",
        "## Robustness check",
        "",
        f"- Block-bootstrap estimated probability of a positive 3-month result: **{fmt_pct(prob_3m)}**",
        f"- Block-bootstrap estimated probability of a positive 1-year result: **{fmt_pct(prob_1y)}**",
        f"- Phase 2 validation gate: **{'PASS' if passed else 'FAIL'}**",
        "",
        "## Important",
        "",
        "This backtest does not guarantee future profit. Historical data, parameter selection, execution assumptions, slippage, taxes, market structure changes, and future regimes can all cause live performance to differ materially. MarketPulse will not move to real-money automation solely because a backtest passes.",
    ]
    with open("backtest_summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
