"""
견고성 검사: python robustness.py

1) 여러 코인에서 같은 전략이 통하는지
2) 손절폭·매수비중을 바꿔도 결과가 유지되는지
3) 지표 기간(200일선, EMA 조합)을 바꿔도 결과가 유지되는지
를 한 번에 확인하고 results/robustness.html 로 보고서를 만듭니다.

한 가지 설정에서만 좋고 조금만 바꿔도 무너지는 전략은 '우연'일 가능성이 큽니다.
"""
import os
import sys
import webbrowser
from types import SimpleNamespace

import pandas as pd

import config
from backtest import run_backtest
from data import load_candles
from indicators import add_all, ema, sma
from strategies import _stateful, buy_and_hold, ma_cross, trend_momentum

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results")
COINS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA", "KRW-DOGE"]


def base_cfg(**over):
    c = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    c.INTERVAL = "day"
    for k, v in over.items():
        setattr(c, k, v)
    return c


def trend_momentum_p(df, trend_n=200, fast=12, slow=26):
    """추세+모멘텀 전략의 기간을 바꿀 수 있는 버전"""
    c = df["close"]
    t, f, s = sma(c, trend_n), ema(c, fast), ema(c, slow)
    enter = (c > t) & (f > s)
    exit_ = (f < s) | (c < t)
    return _stateful(enter, exit_)


def split(df):
    k = int(len(df) * (1 - config.TEST_RATIO))
    return df.iloc[:k], df.iloc[k:]


def row(r, **extra):
    s = r.stats
    return {**extra, "총수익률%": s["총수익률"] * 100, "연환산%": s["연환산수익률"] * 100,
            "MDD%": s["최대낙폭(MDD)"] * 100, "샤프": s["샤프지수"], "거래수": s["거래횟수"],
            "승률%": (s["승률"] * 100) if pd.notna(s["승률"]) else None}


def coin_test(data):
    rows = []
    bh = base_cfg(STOP_LOSS=None, TAKE_PROFIT=None, POSITION_SIZE=1.0)
    for coin, df in data.items():
        dev, test = split(df)
        for period, part in [("전체", df), ("검증", test)]:
            for name, fn, cfg in [("보유", buy_and_hold, bh), ("이평크로스", ma_cross, base_cfg()),
                                  ("추세+모멘텀", trend_momentum, base_cfg())]:
                r = run_backtest(part, fn(part), name, cfg)
                rows.append(row(r, 코인=coin, 기간=period, 시작=f"{part.index[0]:%Y-%m}", 전략=name))
    return pd.DataFrame(rows)


def risk_grid(data, coins=("KRW-BTC", "KRW-ETH")):
    rows = []
    for coin in coins:
        df = data.get(coin)
        if df is None:
            continue
        sig = trend_momentum(df)
        for sl in [None, 0.03, 0.05, 0.08, 0.10, 0.15]:
            for ps in [0.3, 0.5, 1.0]:
                r = run_backtest(df, sig, "tm", base_cfg(STOP_LOSS=sl, POSITION_SIZE=ps))
                rows.append(row(r, 코인=coin, 손절=("없음" if sl is None else f"-{int(sl*100)}%"),
                                매수비중=f"{int(ps*100)}%"))
    return pd.DataFrame(rows)


def param_grid(data, coins=("KRW-BTC", "KRW-ETH")):
    rows = []
    for coin in coins:
        df = data.get(coin)
        if df is None:
            continue
        _, test = split(df)
        for n in [100, 150, 200, 250]:
            for fast, slow in [(8, 21), (12, 26), (20, 50)]:
                sig = trend_momentum_p(df, n, fast, slow)   # 지표는 전체 데이터로 계산 (검증 구간 초반 공백 방지)
                for period, part in [("전체", df), ("검증", test)]:
                    r = run_backtest(part, sig, "p", base_cfg())
                    rows.append(row(r, 코인=coin, 추세선=f"{n}일", EMA=f"{fast}/{slow}", 기간=period))
    return pd.DataFrame(rows)


def html_table(df):
    return df.to_html(index=False, float_format=lambda x: f"{x:,.1f}", classes="t", na_rep="-")


def main(offline_dir=None):
    data = {}
    for coin in COINS:
        try:
            if offline_dir:
                raw = pd.read_csv(os.path.join(offline_dir, f"{coin}.csv"), index_col=0, parse_dates=True)
            else:
                raw = load_candles(coin, "day", config.CANDLE_COUNT if config.INTERVAL == "day" else 3300)
        except Exception as e:                      # 상장 안 된 코인 등은 건너뜀
            print(f"{coin} 건너뜀: {e}")
            continue
        df = add_all(raw).iloc[200:]
        if len(df) < 300:
            print(f"{coin} 데이터가 너무 짧아 건너뜀")
            continue
        data[coin] = df

    os.makedirs(OUT, exist_ok=True)
    a, b, c = coin_test(data), risk_grid(data), param_grid(data)
    for name, t in [("1_코인별", a), ("2_손절_비중", b), ("3_지표기간", c)]:
        t.to_csv(os.path.join(OUT, f"robustness_{name}.csv"), index=False, encoding="utf-8-sig")
        print(f"\n=== {name} ===")
        print(t.round(1).to_string(index=False))

    html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>견고성 검사</title>
<style>body{{font-family:'Malgun Gothic',sans-serif;max-width:1100px;margin:30px auto;padding:0 16px;color:#222}}
.t{{border-collapse:collapse;font-size:13px;margin:10px 0 30px}} .t td,.t th{{border:1px solid #ddd;padding:5px 9px;text-align:right}}
.t th{{background:#f5f5f5}} .note{{background:#fff8e6;padding:12px 16px;border-radius:8px;font-size:14px}}</style>
<h1>추세+모멘텀 전략 견고성 검사 (일봉)</h1>
<div class="note">좋은 전략은 코인이 바뀌어도, 손절폭이나 지표 기간을 조금 바꿔도 비슷하게 동작해야 합니다.
특정 설정 하나에서만 튀는 결과는 믿지 마세요.</div>
<h2>1. 코인별 비교</h2>{html_table(a)}
<h2>2. 손절폭 × 매수비중</h2>{html_table(b)}
<h2>3. 지표 기간 바꾸기</h2>{html_table(c)}</html>"""
    path = os.path.join(OUT, "robustness.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n보고서 저장: {path}")
    if not offline_dir and "--no-open" not in sys.argv:
        webbrowser.open("file://" + path)


if __name__ == "__main__":
    off = sys.argv[sys.argv.index("--offline") + 1] if "--offline" in sys.argv else None
    main(off)
