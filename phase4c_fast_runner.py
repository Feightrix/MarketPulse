import numpy as np
import phase4c_leveraged_etf_backtest as m

_CACHE = {}


def pack_for(df):
    key = id(df)
    if key in _CACHE:
        return _CACHE[key]
    dates = df["date"].to_numpy()
    starts = np.r_[0, np.flatnonzero(dates[1:] != dates[:-1]) + 1]
    ends = np.r_[starts[1:], len(df)]
    pack = {c: df[c].to_numpy() for c in df.columns}
    pack["days"] = [(dates[s], int(s), int(e)) for s, e in zip(starts, ends)]
    _CACHE[key] = pack
    return pack


def fast_simulate(df, start, end, or_min, breakout_bps, rvol, stop_atr, target_r, hold, bps=m.BASE_BPS, return_trades=False):
    p = pack_for(df)
    eq = m.START_EQ
    trades = []
    daily_rows = []

    minute = p["minute"]
    sig_high, sig_low = p["sig_high"], p["sig_low"]
    sig_close, sig_prev = p["sig_close"], p["sig_prev_close"]
    sig_vwap, ema9, ema20 = p["sig_vwap"], p["sig_ema9"], p["sig_ema20"]
    sig_vol, sig_vm = p["sig_volume"], p["sig_vol_med20"]

    bo, bh, bl, bc = p["bull_open"], p["bull_high"], p["bull_low"], p["bull_close"]
    bv, ba, bvm = p["bull_volume"], p["bull_atr14"], p["bull_vol_med20"]
    so, sh, sl, sc = p["bear_open"], p["bear_high"], p["bear_low"], p["bear_close"]
    sv, sa, svm = p["bear_volume"], p["bear_atr14"], p["bear_vol_med20"]

    for d, a, b in p["days"]:
        if d < start or d > end or b - a < 60:
            continue

        opening_count = int(np.searchsorted(minute[a:b], 570 + or_min - 1, side="right"))
        if opening_count <= 0:
            continue
        oe = a + opening_count
        or_high = float(np.max(sig_high[a:oe]))
        or_low = float(np.min(sig_low[a:oe]))

        day0 = eq
        day_pnl = 0.0
        ntr = 0
        losses = 0
        cooldown = -1
        i = oe

        while i < b - 1:
            if minute[i] >= 930:
                break
            if ntr >= m.MAX_TRADES_DAY or losses >= m.MAX_CONSEC_LOSSES:
                break
            if eq / day0 - 1 <= -m.DAILY_STOP:
                break
            if i <= cooldown:
                i += 1
                continue

            vals = (sig_close[i], sig_prev[i], sig_vwap[i], ema9[i], ema20[i], sig_vol[i], sig_vm[i])
            if not all(np.isfinite(v) for v in vals) or sig_vm[i] <= 0:
                i += 1
                continue

            vol_ok = sig_vol[i] >= sig_vm[i] * rvol
            up = or_high * (1 + breakout_bps / 10000.0)
            dn = or_low * (1 - breakout_bps / 10000.0)
            bull = vol_ok and sig_prev[i] <= up and sig_close[i] > up and sig_close[i] > sig_vwap[i] and ema9[i] > ema20[i]
            bear = vol_ok and sig_prev[i] >= dn and sig_close[i] < dn and sig_close[i] < sig_vwap[i] and ema9[i] < ema20[i]
            if not bull and not bear:
                i += 1
                continue

            k = i + 1
            if bull:
                side = "bull"
                ep, atr, vm, ev = float(bo[k]), float(ba[i]), float(bvm[i]), float(bv[i])
                lows, highs, closes = bl, bh, bc
            else:
                side = "bear"
                ep, atr, vm, ev = float(so[k]), float(sa[i]), float(svm[i]), float(sv[i])
                lows, highs, closes = sl, sh, sc

            if not np.isfinite(ep) or ep <= 0 or not np.isfinite(atr) or atr <= 0:
                i += 1
                continue
            if not np.isfinite(vm) or vm <= 0 or ev <= 0:
                i += 1
                continue

            stop_dist = max(atr * stop_atr, ep * 0.0015)
            qty = min((eq * m.RISK_PER_TRADE) / stop_dist, (eq * m.GROSS_CAP) / ep)
            if qty <= 0:
                i += 1
                continue

            stop = ep - stop_dist
            target = ep + stop_dist * target_r
            last = min(k + hold - 1, b - 1)
            xi = last
            xp = float(closes[last])
            reason = "TIME"

            for q in range(k, last + 1):
                if lows[q] <= stop:
                    xi, xp, reason = q, stop, "STOP"
                    break
                if highs[q] >= target:
                    xi, xp, reason = q, target, "TARGET"
                    break
                if minute[q] >= 950:
                    xi, xp, reason = q, float(closes[q]), "EOD"
                    break

            gross = qty * (xp - ep)
            cost = (bps / 10000.0) * qty * (ep + xp)
            pnl = gross - cost
            before = eq
            eq += pnl
            day_pnl += pnl
            ntr += 1
            losses = losses + 1 if pnl < 0 else 0
            cooldown = xi + m.COOLDOWN_MIN

            trades.append({
                "date": str(d),
                "family_side": side,
                "entry_minute": int(minute[k]),
                "exit_minute": int(minute[xi]),
                "entry_price": ep,
                "exit_price": xp,
                "qty": float(qty),
                "gross_pnl": float(gross),
                "turnover": float(qty * (ep + xp)),
                "pnl": float(pnl),
                "ret": float(pnl / before),
                "reason": reason,
            })
            i = xi + 1

        daily_rows.append({
            "date": str(d),
            "pnl": float(day_pnl),
            "ret": float(eq / day0 - 1 if day0 else 0.0),
            "trades": int(ntr),
        })

    return m.summarize(trades, daily_rows, return_trades=return_trades)


m.simulate = fast_simulate
m.main()
