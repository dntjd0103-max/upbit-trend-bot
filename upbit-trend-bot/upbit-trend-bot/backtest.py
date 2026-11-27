"""
백테스트 엔진 (현물, 롱 전용 — 업비트 원화마켓과 같은 조건).

규칙
- 캔들 i 종가 시점의 신호로 결정 → 캔들 i+1 시가에 체결 (미래참조 없음)
- 매수·매도마다 수수료 + 슬리피지 차감
- 손절/익절은 캔들 안에서 가격이 닿으면 그 가격에 체결 (갭이면 시가)
- 손절로 나간 뒤에는 신호가 한 번 0으로 꺼졌다 다시 켜질 때까지 재진입 안 함
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BARS_PER_YEAR = {"day": 365, "minute240": 365 * 6, "minute60": 365 * 24,
                 "minute30": 365 * 48, "minute15": 365 * 96}


@dataclass
class Result:
    name: str
    equity: pd.Series
    trades: pd.DataFrame
    stats: dict = field(default_factory=dict)


def run_backtest(df: pd.DataFrame, signal: pd.Series, name: str, cfg) -> Result:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig = signal.reindex(df.index).fillna(0).to_numpy()
    cost = cfg.FEE_RATE + cfg.SLIPPAGE

    cash, coin = float(cfg.INITIAL_CASH), 0.0
    entry_px = entry_cash = None
    entry_time = None
    blocked = False                     # 손절 후 재진입 대기 상태
    equity = np.empty(len(df))
    trades = []

    def sell(px, i, reason):
        nonlocal cash, coin, entry_px
        proceeds = coin * px * (1 - cost)
        trades.append({"진입시각": entry_time, "청산시각": df.index[i], "진입가": entry_px,
                       "청산가": px, "수익률": proceeds / entry_cash - 1, "청산사유": reason})
        cash += proceeds
        coin, entry_px = 0.0, None

    for i in range(len(df)):
        want = sig[i - 1] if i > 0 else 0          # 직전 캔들 종가에서 낸 신호
        if want == 0:
            blocked = False

        # 1) 시가 체결
        if coin == 0 and want == 1 and not blocked:
            spend = cash * cfg.POSITION_SIZE
            if spend >= 5000:                      # 업비트 최소 주문금액
                coin = spend * (1 - cost) / o[i]
                cash -= spend
                entry_px, entry_cash, entry_time = o[i], spend, df.index[i]
        elif coin > 0 and want == 0:
            sell(o[i], i, "신호")

        # 2) 캔들 내 손절/익절
        if coin > 0:
            if cfg.STOP_LOSS is not None:
                stop = entry_px * (1 - cfg.STOP_LOSS)
                if l[i] <= stop:
                    sell(min(o[i], stop), i, "손절")
                    blocked = True
            if coin > 0 and cfg.TAKE_PROFIT is not None:
                tp = entry_px * (1 + cfg.TAKE_PROFIT)
                if h[i] >= tp:
                    sell(max(o[i], tp), i, "익절")
                    blocked = True

        equity[i] = cash + coin * c[i]

    eq = pd.Series(equity, index=df.index)
    tr = pd.DataFrame(trades)
    return Result(name, eq, tr, compute_stats(eq, tr, cfg))


def compute_stats(eq: pd.Series, trades: pd.DataFrame, cfg) -> dict:
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).total_seconds() / (365 * 24 * 3600)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    bpy = BARS_PER_YEAR.get(cfg.INTERVAL, 365)
    sharpe = rets.mean() / rets.std() * np.sqrt(bpy) if rets.std() > 0 else np.nan
    n = len(trades)
    win = (trades["수익률"] > 0).mean() if n else np.nan
    return {
        "총수익률": total,
        "연환산수익률": cagr,
        "최대낙폭(MDD)": mdd,
        "샤프지수": sharpe,
        "거래횟수": n,
        "승률": win,
        "평균거래수익": trades["수익률"].mean() if n else np.nan,
        "최종자산(원)": eq.iloc[-1],
    }


def simulate_weights(df: pd.DataFrame, weight: pd.Series, cfg, start=None, capital=None):
    """
    목표 비중(0~1) 전략 백테스트. 실거래 엔진(trader.py)과 같은 규칙:
    - 일봉 i 종가에 정한 비중을 i+1 시가에 맞춤 (REBALANCE_BAND 이상 차이 날 때만)
    - 캔들 안에서 평균매수가 대비 STOP_LOSS 닿으면 손절, 비중이 0으로 꺼질 때까지 재진입 안 함
    반환: (자산곡선, 매매일지 DataFrame)
    """
    from strategies import market_regime
    w = weight.reindex(df.index).fillna(0).clip(0, 1).to_numpy()
    o, l, c = (df[k].to_numpy() for k in ("open", "low", "close"))
    cost = cfg.FEE_RATE + cfg.SLIPPAGE
    s = 0 if start is None else max(1, df.index.searchsorted(pd.Timestamp(start)))
    cash = float(capital or cfg.INITIAL_CASH)
    qty = basis = 0.0
    entry = None
    blocked, pos_id, next_pos = False, None, 1
    peak = None
    ts = getattr(cfg, "TRAILING_STOP", None)
    h = df["high"].to_numpy()
    eq, log = [], []

    def ctx(i):
        r = df.iloc[i - 1]
        return {"목표비중": round(float(w[i - 1]), 3), "장세": market_regime(r),
                "200일선대비%": round(float(r["close"] / r["sma200"] - 1) * 100, 1),
                "변동성%": round(float(r["rvol20"]) * 100, 0), "ADX": round(float(r["adx14"]), 0)}

    def buy(i, krw, px, why):
        nonlocal cash, qty, basis, entry, pos_id, next_pos, peak
        q = krw * (1 - cost) / px
        if qty == 0:
            pos_id, next_pos = next_pos, next_pos + 1
            peak = px
        qty += q; basis += krw; cash -= krw; entry = basis / qty
        log.append({"시각": df.index[i], "코인": "", "포지션": pos_id, "구분": "매수", "사유": why, "체결가": px,
                    "금액": krw, "실현수익률%": None, **ctx(i)})

    def sell(i, q, px, why):
        nonlocal cash, qty, basis, entry, pos_id
        q = min(q, qty)
        proceeds = q * px * (1 - cost)
        b = entry * q
        cash += proceeds; qty -= q; basis -= b
        log.append({"시각": df.index[i], "코인": "", "포지션": pos_id, "구분": "매도", "사유": why, "체결가": px,
                    "금액": proceeds, "실현수익률%": (proceeds / b - 1) * 100, **ctx(i)})
        if qty * px < 1:
            qty = basis = 0.0; entry = None; pos_id = None

    for i in range(s, len(df)):
        tgt = w[i - 1]
        if tgt == 0:
            blocked = False
        if blocked:
            tgt = 0.0
        val = cash + qty * o[i]
        cur = qty * o[i] / val if val > 0 else 0
        if tgt == 0 and qty > 0:
            sell(i, qty, o[i], "추세 이탈 (전량 매도)")
        elif tgt > 0 and (qty == 0 or abs(tgt - cur) > cfg.REBALANCE_BAND):
            diff = tgt * val - qty * o[i]
            if diff >= 5000:
                buy(i, min(diff, cash), o[i], "추세 진입" if qty == 0 else "비중 확대 (변동성 감소)")
            elif -diff >= 5000:
                sell(i, -diff / o[i], o[i], "비중 축소 (변동성 증가)")
        if qty > 0 and cfg.STOP_LOSS and l[i] <= entry * (1 - cfg.STOP_LOSS):
            sell(i, qty, min(o[i], entry * (1 - cfg.STOP_LOSS)), f"손절 -{cfg.STOP_LOSS * 100:.0f}%")
            blocked = True
        elif qty > 0 and ts and l[i] <= peak * (1 - ts):
            sell(i, qty, min(o[i], peak * (1 - ts)), f"이익 보호 (최고가 대비 -{ts * 100:.0f}%)")
            blocked = True
        if qty > 0:
            peak = max(peak, h[i])
        eq.append(cash + qty * c[i])
    return pd.Series(eq, index=df.index[s:]), pd.DataFrame(log)
