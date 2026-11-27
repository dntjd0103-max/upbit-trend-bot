"""
공포·탐욕 지수 백테스트: python fng_test.py

추세+모멘텀 전략에 공포·탐욕 지수를 붙였을 때 좋아지는지 확인합니다.
데이터 출처: Alternative.me Crypto Fear & Greed Index
"""
import os
import sys
import webbrowser
from types import SimpleNamespace

import numpy as np
import pandas as pd

import config
from backtest import run_backtest
from data import load_candles, load_fng
from indicators import add_all
from strategies import _stateful, buy_and_hold, trend_momentum

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results")
COINS = ["KRW-BTC", "KRW-ETH"]


def cfg(**over):
    c = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    c.INTERVAL = "day"
    for k, v in over.items():
        setattr(c, k, v)
    return c


def tm_greed_filter(df):
    """추세+모멘텀 + 탐욕 필터: 지수가 80 넘는 과열 구간에서는 새로 사지 않음"""
    c = df["close"]
    enter = (c > df["sma200"]) & (df["ema12"] > df["ema26"]) & (df["fng"] <= 80)
    exit_ = (df["ema12"] < df["ema26"]) | (c < df["sma200"])
    return _stateful(enter, exit_)


def tm_fear_entry(df):
    """추세+모멘텀 + 공포 조기진입: 지수 25 미만(공포)에서 단기 추세가 돌아서면 200일선 아래여도 매수.
    이렇게 들어간 포지션은 단기 추세(EMA12<EMA26)가 꺾일 때만 청산"""
    c, f = df["close"].to_numpy(), df["fng"].to_numpy()
    s200, e12, e26 = df["sma200"].to_numpy(), df["ema12"].to_numpy(), df["ema26"].to_numpy()
    pos, state, mode = np.zeros(len(df)), 0, None
    for i in range(len(df)):
        up = e12[i] > e26[i]
        if state == 0:
            if c[i] > s200[i] and up:
                state, mode = 1, "trend"
            elif f[i] < 25 and up:
                state, mode = 1, "fear"
        else:
            if not up or (mode == "trend" and c[i] < s200[i]):
                state, mode = 0, None
            elif mode == "fear" and c[i] > s200[i]:
                mode = "trend"
        pos[i] = state
    return pd.Series(pos, index=df.index)


def fng_contrarian(df):
    """순수 역발상: 지수 20 미만(극단적 공포)에서 매수, 70 초과(탐욕)에서 매도"""
    return _stateful(df["fng"] < 20, df["fng"] > 70)


STRATS = [("보유", buy_and_hold), ("추세+모멘텀(기준)", trend_momentum),
          ("+탐욕 필터", tm_greed_filter), ("+공포 조기진입", tm_fear_entry), ("공포 역발상(단독)", fng_contrarian)]


def main(offline=None):
    fng = pd.read_csv(os.path.join(offline, "fear_greed.csv"), index_col=0, parse_dates=True)["fng"] if offline else load_fng()
    rows = []
    for coin in COINS:
        raw = (pd.read_csv(os.path.join(offline, f"{coin}.csv"), index_col=0, parse_dates=True) if offline
               else load_candles(coin, "day", 3300))
        df = add_all(raw)
        df["fng"] = fng.reindex(df.index).ffill()
        df = df.iloc[200:].dropna(subset=["fng"])
        k = int(len(df) * (1 - config.TEST_RATIO))
        sigs = {n: fn(df) for n, fn in STRATS}
        for period, part in [("전체", df), ("검증", df.iloc[k:])]:
            for n, _ in STRATS:
                c = cfg(STOP_LOSS=None, POSITION_SIZE=1.0) if n == "보유" else cfg()
                s = run_backtest(part, sigs[n], n, c).stats
                rows.append({"코인": coin, "기간": period, "시작": f"{part.index[0]:%Y-%m}", "전략": n,
                             "총수익률%": s["총수익률"] * 100, "연환산%": s["연환산수익률"] * 100,
                             "MDD%": s["최대낙폭(MDD)"] * 100, "샤프": s["샤프지수"], "거래수": s["거래횟수"],
                             "승률%": s["승률"] * 100 if pd.notna(s["승률"]) else None})
    t = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    t.to_csv(os.path.join(OUT, "fng_test.csv"), index=False, encoding="utf-8-sig")
    print(t.round(1).to_string(index=False))
    html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>공포·탐욕 지수 백테스트</title>
<style>body{{font-family:'Malgun Gothic',sans-serif;max-width:1000px;margin:30px auto;padding:0 16px}}
.t{{border-collapse:collapse;font-size:13px}} .t td,.t th{{border:1px solid #ddd;padding:5px 9px;text-align:right}} .t th{{background:#f5f5f5}}</style>
<h1>공포·탐욕 지수를 붙인 전략 비교 (일봉)</h1>
{t.to_html(index=False, classes='t', float_format=lambda x: f'{x:,.1f}', na_rep='-')}
<p style="color:#777;font-size:13px">공포·탐욕 지수 데이터 출처: <a href="https://alternative.me/crypto/fear-and-greed-index/">Alternative.me</a></p></html>"""
    path = os.path.join(OUT, "fng_test.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n보고서 저장: {path}")
    if not offline and "--no-open" not in sys.argv:
        webbrowser.open("file://" + path)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--offline") + 1] if "--offline" in sys.argv else None)
