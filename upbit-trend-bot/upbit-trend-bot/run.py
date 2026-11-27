"""
실행 파일: python run.py

1) 업비트에서 시세를 받아오고
2) 보조지표를 계산한 뒤
3) 모든 전략을 백테스트해서
4) results/report.html 로 결과 보고서를 만듭니다.

옵션
  python run.py --refresh      저장된 데이터를 무시하고 새로 받기
  python run.py --csv 파일.csv  업비트 대신 가지고 있는 CSV 로 테스트
"""
import argparse
import base64
import io
import os
import sys
import webbrowser
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import config
from backtest import run_backtest
from data import load_candles
from indicators import add_all
from strategies import STRATEGIES

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "results")

from matplotlib import font_manager
_have = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = [f for f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR")
                               if f in _have] or ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def cfg_for(name):
    """Buy&Hold 는 순수 비교 기준이라 손절/익절을 적용하지 않음"""
    c = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    if name.startswith("보유"):
        c.STOP_LOSS = None
        c.TAKE_PROFIT = None
        c.POSITION_SIZE = 1.0
    return c


def fmt(stats):
    out = {}
    for k, v in stats.items():
        if pd.isna(v):
            out[k] = "-"
        elif k in ("거래횟수",):
            out[k] = f"{int(v)}"
        elif k == "최종자산(원)":
            out[k] = f"{v:,.0f}"
        elif k == "샤프지수":
            out[k] = f"{v:.2f}"
        else:
            out[k] = f"{v * 100:+.1f}%" if k != "승률" else f"{v * 100:.0f}%"
    return out


def chart(results, title):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for r in results:
        ax.plot(r.equity.index, r.equity / r.equity.iloc[0] * 100, label=r.name,
                lw=2.2 if r.name.startswith("보유") else 1.4,
                ls="--" if r.name.startswith("보유") else "-")
    ax.set_title(title)
    ax.set_ylabel("자산 (시작=100)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def run_period(df, label):
    results = []
    for name, fn in STRATEGIES.items():
        sig = fn(df)
        results.append(run_backtest(df, sig, name, cfg_for(name)))
    table = pd.DataFrame({r.name: fmt(r.stats) for r in results}).T
    print(f"\n=== {label}: {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} ===")
    print(table.to_string())
    return results, table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    if a.csv:
        raw = pd.read_csv(a.csv, index_col=0, parse_dates=True)
    else:
        raw = load_candles(config.MARKET, config.INTERVAL, config.CANDLE_COUNT, a.refresh)

    df = add_all(raw).iloc[200:]                 # 200선이 계산되기 전 구간은 제외
    split = int(len(df) * (1 - config.TEST_RATIO))
    periods = [("전체 기간", df), ("개발 구간(앞 70%)", df.iloc[:split]),
               ("검증 구간(뒤 30%, 처음 보는 데이터)", df.iloc[split:])]

    os.makedirs(OUT_DIR, exist_ok=True)
    sections = []
    for label, part in periods:
        results, table = run_period(part, label)
        img = chart(results, f"{config.MARKET} {label}")
        sections.append(f"<h2>{label} <small>{part.index[0]:%Y-%m-%d} ~ {part.index[-1]:%Y-%m-%d}</small></h2>"
                        f"<img src='data:image/png;base64,{img}'>{table.to_html(classes='t')}")
        if label == "전체 기간":
            for r in results:
                if len(r.trades):
                    r.trades.to_csv(os.path.join(OUT_DIR, f"거래내역_{r.name}.csv".replace("/", "_")),
                                    index=False, encoding="utf-8-sig")

    s = config
    html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>백테스트 결과</title>
<style>body{{font-family:'Malgun Gothic',sans-serif;max-width:1100px;margin:30px auto;padding:0 16px;color:#222}}
img{{width:100%}} .t{{border-collapse:collapse;font-size:14px;margin:10px 0 30px}}
.t td,.t th{{border:1px solid #ddd;padding:6px 10px;text-align:right}} .t th{{background:#f5f5f5}}
small{{color:#888;font-weight:normal}} .note{{background:#fff8e6;padding:12px 16px;border-radius:8px;font-size:14px}}</style>
<h1>{s.MARKET} 전략 백테스트</h1>
<p>캔들 {s.INTERVAL} · 시작자금 {s.INITIAL_CASH:,}원 · 수수료 {s.FEE_RATE*100:.2f}% + 슬리피지 {s.SLIPPAGE*100:.2f}% ·
손절 {('-'+str(int(s.STOP_LOSS*100))+'%') if s.STOP_LOSS else '없음'} · 매수비중 {int(s.POSITION_SIZE*100)}%</p>
<div class="note">읽는 법: <b>검증 구간</b>에서도 <b>보유(Buy&Hold)</b>보다 MDD가 작거나 샤프지수가 높아야 의미 있는 전략입니다.
개발 구간에서만 좋은 전략은 과거에 끼워 맞춰진 것일 가능성이 큽니다. 과거 성과는 미래 수익을 보장하지 않습니다.</div>
{''.join(sections)}</html>"""
    path = os.path.join(OUT_DIR, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n보고서 저장: {path}")
    if not a.no_open:
        webbrowser.open("file://" + path)


if __name__ == "__main__":
    sys.exit(main())
