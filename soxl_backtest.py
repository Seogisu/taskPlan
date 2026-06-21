#!/usr/bin/env python3
"""
SOXL "buy the -15% day, take profit at +10%" backtest.

Strategy (as requested):
  - Universe: SOXL (Direxion Daily Semiconductor Bull 3x), since inception 2010-03-11.
  - Signal : any session whose close-to-close return <= -15%  ("상장 이후 15% 하락한 날").
  - Entry  : buy at that day's CLOSE.
  - Exit   : take profit when price reaches +10% above the entry price ("10% 익절"),
             filled intraday via a limit order at entry*1.10 (the day High touches the target).
  - One position at a time, fully compounded (each closed trade reinvests all capital).
  - Costs  : commissions and taxes EXCLUDED (gross returns), per request.

Data: split-adjusted daily OHLC stitched from two public GitHub-hosted CSVs
(identical underlying data, validated on their overlap):
  - 2010-03-11 .. 2019-05-16 : mohaoqing/Simple-Trader
  - 2019-05-17 .. 2026-05-15 : kinryukii/us-tech-quant-v19
Both expose split-adjusted Close/High (they match exactly at the splice), so no
rescaling is needed. "Adj Close" (dividend-adjusted) is deliberately NOT used, because
the strategy trades actual prices and dividends are excluded alongside fees/taxes.
"""

import csv, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

SOURCES = {
    "early": (  # 2010-03-11 .. 2023-12-15
        os.path.join(DATA, "soxl_2010_2023_mohaoqing.csv"),
        "https://raw.githubusercontent.com/mohaoqing/Simple-Trader/"
        "f65fc925536ffcaa5b6f8ab4c541ba56c44615a3/data/SOXL.csv",
    ),
    "late": (  # 2019-05-16 .. 2026-05-15
        os.path.join(DATA, "soxl_2019_2026_kinryukii.csv"),
        "https://raw.githubusercontent.com/kinryukii/us-tech-quant-v19/"
        "c451923075327916094e9be621052658d872081b/data/v16/prices_full/SOXL.csv",
    ),
}

SPLICE = "2019-05-16"   # use 'early' through this date, 'late' strictly after
DROP   = -0.15          # entry trigger: daily return <= -15%
TAKE   = 0.10           # take profit: +10%


def load(path, url):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
            f.write(r.read())
    rows = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            d = row["Date"][:10]
            rows[d] = (float(row["High"]), float(row["Close"]))
    return rows


def build_series(start=None):
    early = load(*SOURCES["early"])
    late = load(*SOURCES["late"])
    # validate the splice: split-adjusted closes must agree on the overlap
    common = sorted(set(early) & set(late))
    diffs = [abs(early[d][1] - late[d][1]) / late[d][1] for d in common]
    maxdiff = max(diffs) if diffs else 0.0
    series = {}
    for d, v in early.items():
        if d <= SPLICE:
            series[d] = v
    for d, v in late.items():
        if d > SPLICE:
            series[d] = v
    # keep one extra session before `start` so the first in-window day has a
    # valid close-to-close return; that pre-start day is never an entry itself.
    if start:
        dates_all = sorted(series)
        keep = [d for d in dates_all if d >= start]
        if keep:
            i0 = dates_all.index(keep[0])
            if i0 > 0:
                keep = [dates_all[i0 - 1]] + keep
        series = {d: series[d] for d in keep}
        # the warm-up day naturally cannot be an entry: the backtest loop starts
        # with prev_close=None, so no signal fires on the first session.
    dates = sorted(series)
    return dates, series, common, maxdiff


def backtest(dates, series):
    capital = 1.0
    holding = False
    entry = target = 0.0
    entry_date = None
    signals = 0
    trades = []  # (entry_date, entry_px, exit_date, exit_px, ret, hold_days)
    prev_close = None

    for i, d in enumerate(dates):
        high, close = series[d]

        # check exit first (a position opened on a prior day)
        if holding and high >= target:
            ret = TAKE  # filled at the limit = entry*1.10
            hd = i - entry_idx
            capital *= (1 + ret)
            trades.append((entry_date, entry, d, target, ret, hd, "tp"))
            holding = False

        # entry signal (only if flat) — evaluated on close-to-close return
        if prev_close is not None and not holding:
            r = close / prev_close - 1.0
            if r <= DROP:
                signals += 1
                holding = True
                entry = close
                target = entry * (1 + TAKE)
                entry_date = d
                entry_idx = i

        prev_close = close

    # mark-to-market any open position at the last close
    open_trade = None
    if holding:
        last_d = dates[-1]
        last_close = series[last_d][1]
        ret = last_close / entry - 1.0
        hd = (len(dates) - 1) - entry_idx
        capital_mtm = capital * (1 + ret)
        open_trade = (entry_date, entry, last_d, last_close, ret, hd)
    else:
        capital_mtm = capital

    return capital, capital_mtm, signals, trades, open_trade


def main():
    start_arg = sys.argv[1] if len(sys.argv) > 1 else None
    dates, series, common, maxdiff = build_series(start_arg)
    cap_closed, cap_mtm, signals, trades, open_trade = backtest(dates, series)

    end = dates[-1]
    # first in-window trading day (skip the warm-up session if one was added)
    first = next((d for d in dates if not start_arg or d >= start_arg), dates[0])
    start = start_arg or first
    # buy & hold over the same window (split-adjusted close, dividends excluded)
    bh = series[end][1] / series[first][1] - 1.0

    import statistics
    holds = [t[5] for t in trades]

    print("=" * 70)
    print("SOXL  |  buy on -15% day (close), take profit +10%  |  fees & tax excluded")
    print("=" * 70)
    print(f"Data window      : {start}  ->  {end}  ({len(dates)} trading days)")
    print(f"Splice overlap   : {len(common)} common days, max split-adj close diff = {maxdiff:.4%}")
    print(f"Entry rule       : daily close-to-close return <= {DROP:.0%}")
    print(f"Exit rule        : +{TAKE:.0%} limit (intraday high touches entry*{1+TAKE:.2f})")
    print("-" * 70)
    print(f"-15% down days (signals taken) : {signals}")
    print(f"Completed +10% trades          : {len(trades)}")
    if holds:
        print(f"Hold period (trading days)     : min {min(holds)}, "
              f"median {int(statistics.median(holds))}, max {max(holds)}, "
              f"mean {statistics.mean(holds):.1f}")
    print("-" * 70)
    print("RESULT (compounded, one position at a time, reinvested):")
    print(f"  Closed trades only : x{cap_closed:.4f}   ({cap_closed-1:+.2%} total)")
    if open_trade:
        ed, ep, xd, xp, r, hd = open_trade
        print(f"  Open position      : entered {ed} @ {ep:.4f}, marked at {xd} close "
              f"{xp:.4f} ({r:+.2%}, held {hd}d)")
    print(f"  Incl. open (MTM)   : x{cap_mtm:.4f}   ({cap_mtm-1:+.2%} total)")
    print("-" * 70)
    yrs = (_to_ord(end) - _to_ord(start)) / 365.25
    cagr = cap_mtm ** (1 / yrs) - 1
    print(f"Strategy CAGR (~{yrs:.1f}y): {cagr:+.2%}")
    print(f"Buy & hold same window : {bh:+.2%}  (x{1+bh:.2f})")
    print("=" * 70)

    # dump trade log
    out = os.path.join(HERE, "trades.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_date", "entry_px", "exit_date", "exit_px", "return", "hold_days", "type"])
        for t in trades:
            w.writerow([t[0], f"{t[1]:.4f}", t[2], f"{t[3]:.4f}", f"{t[4]:.4f}", t[5], t[6]])
        if open_trade:
            ed, ep, xd, xp, r, hd = open_trade
            w.writerow([ed, f"{ep:.4f}", xd, f"{xp:.4f}", f"{r:.4f}", hd, "open_mtm"])
    print(f"Trade log written: {out}")


def _to_ord(d):
    import datetime
    return datetime.date(*map(int, d.split("-"))).toordinal()


if __name__ == "__main__":
    main()
