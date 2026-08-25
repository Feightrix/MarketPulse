import json
import math
import os
import time as time_module
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import options_pattern1_backtest as base
import options_pattern2_vwap_reversion as p2
import options_pattern2_trend_refinement as p2t

RESULT_JSON = "options_pattern2_contract_results.json"
RESULT_MD = "options_pattern2_contract_results.md"
STARTING_BALANCE = 2500.0
LOOKBACK_DAYS = 720
UNDERLYING = "SPY"
FROZEN_TREND_CFG = {
    "max_vwap_slope_atr": 0.50,
    "max_efficiency": 0.65,
    "min_rsi_turn": 3.0,
}

# Contract policy is fixed before seeing option-P/L results.
DTE_MIN = 2
DTE_MAX = 8
TARGET_DTE = 4
OTM_PCT = 0.005
MAX_PREMIUM_FRACTION = 0.20
MIN_PREMIUM_DOLLARS = 30.0
MAX_CANDIDATES_PER_SIGNAL = 12
ENTRY_DELAY_MINUTES = 1
EXIT_BAR_OFFSET_MINUTES = 4
FALLBACK_HALF_SPREAD_PCT = 0.005
FALLBACK_MIN_HALF_SPREAD = 0.01
EST_ROUNDTRIP_FEES = 0.11
CUTOFF_DATE = "2025-08-29"

DATA_BASE = "https://data.alpaca.markets"
PAPER_BASE = "https://paper-api.alpaca.markets"

API_KEY = os.environ.get("ALPACA_OPTIONS_API_KEY_ID")
SECRET_KEY = os.environ.get("ALPACA_OPTIONS_SECRET_KEY")
if not API_KEY or not SECRET_KEY:
    raise RuntimeError("Missing options paper credentials")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY,
    "Accept": "application/json",
    "User-Agent": "MarketPulse-options-contract-research/1.0",
}

_quote_capability = None
_contract_cache = {}


def request_json(url, params=None, timeout=30, allow_errors=()):
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers=HEADERS)
    last = None
    for attempt in range(5):
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in allow_errors:
                return None
            last = exc
            if exc.code == 429 and attempt < 4:
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else (1.0 + attempt * 1.5)
                time_module.sleep(delay)
                continue
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc
        except URLError as exc:
            last = exc
            if attempt < 4:
                time_module.sleep(1.0 + attempt)
                continue
            raise
    raise RuntimeError(f"Request failed: {url}: {last}")


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def utc_iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_frozen_signals():
    old = base.LOOKBACK_DAYS
    base.LOOKBACK_DAYS = LOOKBACK_DAYS
    try:
        raw = base.fetch_bars()
    finally:
        base.LOOKBACK_DAYS = old

    by_day = base.regular_session_bars(raw)
    days = []
    bar_lookup = {}
    for day in sorted(by_day):
        bars = by_day[day]
        if len(bars) < 50:
            continue
        base.add_session_vwap(bars)
        p2.add_atr_rsi(bars)
        days.append((day, bars))
        for b in bars:
            bar_lookup[b["ts"].isoformat()] = b

    signals = p2t.evaluate(days, FROZEN_TREND_CFG)
    enriched = []
    for sig in signals:
        bar = bar_lookup.get(sig["entry_ts"])
        if not bar:
            continue
        item = dict(sig)
        item["underlying_entry_spot"] = round(bar["c"], 4)
        enriched.append(item)
    return enriched, len(days)


def fetch_contracts(signal_date, side, spot):
    key = (signal_date, side, round(spot, 0))
    if key in _contract_cache:
        return _contract_cache[key]

    day = datetime.fromisoformat(signal_date).date()
    exp_gte = day + timedelta(days=DTE_MIN)
    exp_lte = day + timedelta(days=DTE_MAX)
    option_type = "call" if side == "CALL" else "put"
    strike_lo = max(1.0, spot * 0.94)
    strike_hi = spot * 1.06
    contracts = []

    for status in ("inactive", "active"):
        params = {
            "underlying_symbols": UNDERLYING,
            "status": status,
            "expiration_date_gte": exp_gte.isoformat(),
            "expiration_date_lte": exp_lte.isoformat(),
            "type": option_type,
            "strike_price_gte": f"{strike_lo:.2f}",
            "strike_price_lte": f"{strike_hi:.2f}",
            "limit": 1000,
        }
        data = request_json(f"{PAPER_BASE}/v2/options/contracts", params=params)
        if data:
            contracts.extend(data.get("option_contracts", []))

    unique = {c.get("symbol"): c for c in contracts if c.get("symbol")}
    contracts = list(unique.values())
    _contract_cache[key] = contracts
    return contracts


def rank_contracts(contracts, signal_date, side, spot):
    day = datetime.fromisoformat(signal_date).date()
    target_strike = spot * (1.0 + OTM_PCT if side == "CALL" else 1.0 - OTM_PCT)

    ranked = []
    for c in contracts:
        try:
            exp = datetime.fromisoformat(c["expiration_date"]).date()
            strike = float(c["strike_price"])
        except Exception:
            continue
        dte = (exp - day).days
        if dte < DTE_MIN or dte > DTE_MAX:
            continue
        oi = int(c.get("open_interest") or 0)
        score = (
            abs(dte - TARGET_DTE),
            abs(strike - target_strike),
            -oi,
        )
        ranked.append((score, c, dte, strike))
    ranked.sort(key=lambda x: x[0])
    return ranked[:MAX_CANDIDATES_PER_SIGNAL]


def fetch_option_bars(symbol, start_dt, end_dt):
    params = {
        "symbols": symbol,
        "timeframe": "1Min",
        "start": utc_iso(start_dt),
        "end": utc_iso(end_dt),
        "limit": 10000,
        "sort": "asc",
    }
    data = request_json(f"{DATA_BASE}/v1beta1/options/bars", params=params)
    raw = (data or {}).get("bars", {})
    if isinstance(raw, dict):
        bars = raw.get(symbol, [])
    elif isinstance(raw, list):
        bars = raw
    else:
        bars = []
    for b in bars:
        if "t" in b:
            b["_ts"] = parse_ts(b["t"])
    return bars


def fetch_quote_near(symbol, target_dt, field):
    global _quote_capability
    if _quote_capability is False:
        return None

    start = target_dt - timedelta(seconds=20)
    end = target_dt + timedelta(seconds=100)
    params = {
        "symbols": symbol,
        "start": utc_iso(start),
        "end": utc_iso(end),
        "limit": 5000,
        "sort": "asc",
    }
    try:
        data = request_json(
            f"{DATA_BASE}/v1beta1/options/quotes",
            params=params,
            allow_errors=(400, 404, 405),
        )
    except RuntimeError:
        data = None

    if data is None:
        _quote_capability = False
        return None
    _quote_capability = True
    raw = data.get("quotes", {})
    quotes = raw.get(symbol, []) if isinstance(raw, dict) else []
    best = None
    for q in quotes:
        try:
            ts = parse_ts(q["t"])
            px = float(q.get(field) or 0.0)
        except Exception:
            continue
        if ts >= target_dt and px > 0:
            best = (px, ts)
            break
    return best


def first_bar_at_or_after(bars, target_dt):
    for b in bars:
        if b.get("_ts") and b["_ts"] >= target_dt:
            return b
    return None


def fallback_fill(bar, is_entry):
    base_px = float(bar.get("o") if is_entry else bar.get("c"))
    haircut = max(FALLBACK_MIN_HALF_SPREAD, base_px * FALLBACK_HALF_SPREAD_PCT)
    return base_px + haircut if is_entry else max(0.01, base_px - haircut)


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot, strike, years, rate, sigma, is_call):
    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    if is_call:
        return spot * normal_cdf(d1) - strike * math.exp(-rate * years) * normal_cdf(d2)
    return strike * math.exp(-rate * years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def implied_vol_and_delta(spot, strike, premium, dte, side):
    years = max(dte, 0.25) / 365.0
    rate = 0.04
    is_call = side == "CALL"
    intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    if premium <= intrinsic + 1e-6:
        return None, None
    lo, hi = 0.01, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        px = bs_price(spot, strike, years, rate, mid, is_call)
        if px < premium:
            lo = mid
        else:
            hi = mid
    sigma = (lo + hi) / 2.0
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * root_t)
    delta = normal_cdf(d1) if is_call else normal_cdf(d1) - 1.0
    return round(sigma, 4), round(delta, 4)


def simulate_signal(sig):
    entry_ts = parse_ts(sig["entry_ts"])
    exit_ts = parse_ts(sig["exit_ts"])
    signal_date = entry_ts.date().isoformat()
    spot = float(sig["underlying_entry_spot"])

    contracts = fetch_contracts(signal_date, sig["side"], spot)
    if not contracts:
        return None, "no_contracts"
    ranked = rank_contracts(contracts, signal_date, sig["side"], spot)
    if not ranked:
        return None, "no_ranked_contracts"

    entry_ref = entry_ts + timedelta(minutes=ENTRY_DELAY_MINUTES)
    exit_ref = exit_ts + timedelta(minutes=EXIT_BAR_OFFSET_MINUTES)
    if exit_ref <= entry_ref:
        exit_ref = entry_ref + timedelta(minutes=1)
    end_fetch = exit_ref + timedelta(minutes=2)

    for _, c, dte, strike in ranked:
        symbol = c["symbol"]
        bars = fetch_option_bars(symbol, entry_ts, end_fetch)
        if not bars:
            continue
        entry_bar = first_bar_at_or_after(bars, entry_ref)
        exit_bar = first_bar_at_or_after(bars, exit_ref)
        if entry_bar is None or exit_bar is None:
            continue

        q_entry = fetch_quote_near(symbol, entry_ref, "ap")
        q_exit = fetch_quote_near(symbol, exit_ref, "bp") if _quote_capability is not False else None
        if q_entry and q_exit:
            entry_fill = q_entry[0]
            exit_fill = q_exit[0]
            fill_mode = "historical_bid_ask"
        else:
            entry_fill = fallback_fill(entry_bar, True)
            exit_fill = fallback_fill(exit_bar, False)
            fill_mode = "trade_bar_conservative"

        if entry_fill <= 0:
            continue
        premium_dollars = entry_fill * 100.0
        if premium_dollars < MIN_PREMIUM_DOLLARS:
            continue
        if premium_dollars > STARTING_BALANCE * MAX_PREMIUM_FRACTION:
            continue

        gross = (exit_fill - entry_fill) * 100.0
        net = gross - EST_ROUNDTRIP_FEES
        iv, delta = implied_vol_and_delta(spot, strike, entry_fill, dte, sig["side"])
        return {
            "date": signal_date,
            "side": sig["side"],
            "underlying_entry_spot": round(spot, 4),
            "underlying_exit_reason": sig["exit_reason"],
            "underlying_r": sig["r"],
            "entry_ts": sig["entry_ts"],
            "exit_ts": sig["exit_ts"],
            "contract": symbol,
            "expiration_date": c["expiration_date"],
            "dte": dte,
            "strike": strike,
            "moneyness_pct": round((strike / spot - 1.0) * 100.0, 3),
            "entry_fill": round(entry_fill, 4),
            "exit_fill": round(exit_fill, 4),
            "premium_dollars": round(premium_dollars, 2),
            "gross_pl_dollars": round(gross, 2),
            "net_pl_dollars": round(net, 2),
            "return_on_premium_pct": round(net / premium_dollars * 100.0, 2),
            "fill_mode": fill_mode,
            "iv_proxy": iv,
            "delta_proxy": delta,
        }, None
    return None, "no_liquid_affordable_contract"


def summarize(trades):
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "net_pl_dollars": 0.0, "ending_balance_dollars": STARTING_BALANCE,
            "return_pct": 0.0, "profit_factor": None, "avg_win_dollars": 0.0,
            "avg_loss_dollars": 0.0, "max_drawdown_dollars": 0.0,
            "avg_premium_dollars": 0.0, "avg_return_on_premium_pct": 0.0,
        }
    ordered = sorted(trades, key=lambda x: x["entry_ts"])
    pls = [t["net_pl_dollars"] for t in ordered]
    wins = [x for x in pls if x > 0]
    losses = [x for x in pls if x <= 0]
    equity = STARTING_BALANCE
    peak = equity
    max_dd = 0.0
    for pl in pls:
        equity += pl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": len(pls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(pls) * 100.0, 2),
        "net_pl_dollars": round(sum(pls), 2),
        "ending_balance_dollars": round(STARTING_BALANCE + sum(pls), 2),
        "return_pct": round(sum(pls) / STARTING_BALANCE * 100.0, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "avg_win_dollars": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_dollars": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_dollars": round(max_dd, 2),
        "avg_premium_dollars": round(sum(t["premium_dollars"] for t in ordered) / len(ordered), 2),
        "avg_return_on_premium_pct": round(sum(t["return_on_premium_pct"] for t in ordered) / len(ordered), 2),
    }


def write_results(result):
    Path(RESULT_JSON).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    full = result["full_option_sim"]
    old = result["older_external_block"]
    recent = result["recent_block"]
    lines = [
        "# MarketPulse — Pattern 2 Real Options Contract Simulation",
        "",
        "**Research only. No orders are submitted.**",
        "",
        "## Fixed Contract Policy",
        f"- SPY long CALL/PUT contract, **{DTE_MIN}-{DTE_MAX} calendar DTE**, target **{TARGET_DTE} DTE**",
        f"- Target strike: **{OTM_PCT:.2%} OTM**",
        f"- Maximum premium debit: **{MAX_PREMIUM_FRACTION:.0%} of $2,500 = ${STARTING_BALANCE * MAX_PREMIUM_FRACTION:,.0f}**",
        "- Underlying Pattern 2 rules are frozen; option contract layer does not alter the signal",
        "- Exit occurs when the frozen underlying signal exits",
        "- Historical bid/ask is used if Alpaca exposes it; otherwise actual 1-minute option trade bars receive a conservative execution haircut",
        "",
        "## Full Option Simulation",
        f"- Signals generated: **{result['signals_generated']}**",
        f"- Contracts simulated: **{full['trades']}**",
        f"- Win rate: **{full['win_rate_pct']:.2f}%**",
        f"- Net P/L: **${full['net_pl_dollars']:,.2f}**",
        f"- Ending balance: **${full['ending_balance_dollars']:,.2f}**",
        f"- Return: **{full['return_pct']:.2f}%**",
        f"- Profit factor: **{full['profit_factor']}**",
        f"- Average win: **${full['avg_win_dollars']:,.2f}**",
        f"- Average loss: **${full['avg_loss_dollars']:,.2f}**",
        f"- Max drawdown: **${full['max_drawdown_dollars']:,.2f}**",
        f"- Average premium: **${full['avg_premium_dollars']:,.2f}**",
        "",
        "## Older External Block",
        f"- Trades: **{old['trades']}**, win rate **{old['win_rate_pct']:.2f}%**, P/L **${old['net_pl_dollars']:,.2f}**, PF **{old['profit_factor']}**",
        "",
        "## Recent Block",
        f"- Trades: **{recent['trades']}**, win rate **{recent['win_rate_pct']:.2f}%**, P/L **${recent['net_pl_dollars']:,.2f}**, PF **{recent['profit_factor']}**",
        "",
        "## Data Quality",
        f"- Fill modes: **{result['fill_modes']}**",
        f"- Skipped signals: **{result['skipped_signals']}**",
        f"- Skip reasons: **{result['skip_reasons']}**",
        "- Historical delta is not provided by Alpaca; delta/IV fields are Black-Scholes proxies inferred from the selected contract premium.",
    ]
    Path(RESULT_MD).write_text("\n".join(lines) + "\n")


def main():
    signals, sessions = build_frozen_signals()
    trades = []
    skips = Counter()
    for i, sig in enumerate(signals, start=1):
        trade, reason = simulate_signal(sig)
        if trade:
            trades.append(trade)
        else:
            skips[reason or "unknown"] += 1
        if i % 10 == 0:
            print(f"Processed {i}/{len(signals)} signals; simulated={len(trades)} skipped={sum(skips.values())}")

    older = [t for t in trades if t["date"] < CUTOFF_DATE]
    recent = [t for t in trades if t["date"] >= CUTOFF_DATE]
    calls = [t for t in trades if t["side"] == "CALL"]
    puts = [t for t in trades if t["side"] == "PUT"]
    fill_modes = Counter(t["fill_mode"] for t in trades)

    result = {
        "strategy": "options_pattern2_real_contract_sim",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "order_submission_enabled": False,
        "lookback_days": LOOKBACK_DAYS,
        "complete_sessions": sessions,
        "signals_generated": len(signals),
        "contracts_simulated": len(trades),
        "skipped_signals": sum(skips.values()),
        "skip_reasons": dict(skips),
        "fill_modes": dict(fill_modes),
        "historical_quote_endpoint_available": _quote_capability,
        "starting_balance": STARTING_BALANCE,
        "frozen_trend_config": FROZEN_TREND_CFG,
        "contract_policy": {
            "dte_min": DTE_MIN,
            "dte_max": DTE_MAX,
            "target_dte": TARGET_DTE,
            "otm_pct": OTM_PCT,
            "max_premium_fraction": MAX_PREMIUM_FRACTION,
            "entry_delay_minutes": ENTRY_DELAY_MINUTES,
            "exit_bar_offset_minutes": EXIT_BAR_OFFSET_MINUTES,
            "fallback_half_spread_pct": FALLBACK_HALF_SPREAD_PCT,
            "estimated_roundtrip_fees": EST_ROUNDTRIP_FEES,
        },
        "full_option_sim": summarize(trades),
        "older_external_block": summarize(older),
        "recent_block": summarize(recent),
        "direction_split": {
            "calls": summarize(calls),
            "puts": summarize(puts),
        },
        "trades": trades,
    }
    write_results(result)
    print(json.dumps({
        "signals": len(signals),
        "simulated": len(trades),
        "skipped": dict(skips),
        "fill_modes": dict(fill_modes),
        "full": result["full_option_sim"],
        "older": result["older_external_block"],
        "recent": result["recent_block"],
        "calls": result["direction_split"]["calls"],
        "puts": result["direction_split"]["puts"],
    }, indent=2))


if __name__ == "__main__":
    main()
