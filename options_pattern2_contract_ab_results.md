# MarketPulse — Pattern 2 Contract Policy A/B (Corrected)

**Research only. Underlying signals are frozen; order submission remains disabled.**

- Prior 720-day control reproduced: **YES**
- Robust challenger found: **NO**
- Policy promoted: **NONE**

## Best Development Challenger
- Policy: **itm_4d_delta60**
- Development P/L: **$170.49**
- External validation P/L: **$13.29**
- Full 900-day P/L: **$183.78**

## True Control
- Development P/L: **$16.96**
- External validation P/L: **$-1.71**
- Full 900-day P/L: **$15.25**

Fills use actual 1-minute historical option trade bars with the same conservative 0.5% execution haircut and estimated fees as the original control. Delta is reconstructed from actual entry premium as a proxy; historical Greeks are not assumed.
