"""
매매 복기와 전략 점검.

1) round_trips: 매매일지(매수·매도 기록)를 '포지션 단위'로 묶고, 각 포지션을 자동으로 복기합니다.
2) suggestions: 최근 포지션들에서 반복되는 문제를 찾아 전략 수정안을 제안하고,
   그 수정안을 최근 3년 데이터로 미리 돌려본 결과(전/후)를 함께 보여줍니다.
   제안은 자동으로 적용되지 않습니다. config.py 를 직접 바꿔야 적용됩니다.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd

import config


def _cfg(**over):
    c = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    for k, v in over.items():
        setattr(c, k, v)
    return c


def round_trips(trades: pd.DataFrame, candles: dict) -> pd.DataFrame:
    """trades: 시각, 코인, 포지션, 구분, 사유, 체결가, 금액, 장세, ADX ... / candles: {코인: 일봉 DataFrame}"""
    if trades is None or trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["시각"] = pd.to_datetime(t["시각"])
    out = []
    for (coin, pos), g in t.groupby(["코인", "포지션"], sort=False):
        g = g.sort_values("시각")
        buys, sells = g[g["구분"] == "매수"], g[g["구분"] == "매도"]
        cost, proceeds = buys["금액"].sum(), sells["금액"].sum()
        qty_b = (buys["금액"] / buys["체결가"]).sum()
        avg = cost / qty_b if qty_b else np.nan
        first = buys.iloc[0] if len(buys) else g.iloc[0]
        closed = len(sells) > 0 and str(sells.iloc[-1]["사유"]).startswith(("추세 이탈", "손절", "이익 보호"))
        start, end = first["시각"], (sells.iloc[-1]["시각"] if closed else pd.Timestamp.now())
        df = candles.get(coin)
        mfe = mae = after = np.nan
        last_px = np.nan
        if df is not None and len(df):
            win = df[(df.index >= start.normalize()) & (df.index <= end)]
            if len(win):
                mfe = (win["high"].max() / avg - 1) * 100
                mae = (win["low"].min() / avg - 1) * 100
            last_px = df["close"].iloc[-1]
            if closed:
                post = df[(df.index > end) & (df.index <= end + pd.Timedelta(days=10))]
                if len(post):
                    after = (post["high"].max() / avg - 1) * 100      # 청산 후 10일 최고가가 평균 매수가 대비 어디까지 갔나
        if closed:
            ret = (proceeds / cost - 1) * 100
        else:
            held_qty = qty_b - (sells["금액"] / sells["체결가"]).sum() if len(sells) else qty_b
            ret = ((proceeds + held_qty * last_px) / cost - 1) * 100 if not np.isnan(last_px) else np.nan
        days = (end - start).days
        exit_reason = sells.iloc[-1]["사유"] if closed else "보유 중"
        row = {"코인": coin, "포지션": int(pos), "진입일": start.strftime("%Y-%m-%d"),
               "청산일": end.strftime("%Y-%m-%d") if closed else "-", "보유일": days,
               "진입장세": first.get("장세", "-"), "진입ADX": first.get("ADX", np.nan),
               "비중조정": max(len(g) - 2, 0) if closed else max(len(g) - 1, 0),
               "수익률%": ret, "최고%": mfe, "최저%": mae, "청산후10일%": after,
               "청산사유": exit_reason, "closed": closed}
        row["복기유형"], row["복기"] = _review(row)
        out.append(row)
    return pd.DataFrame(out)


def _review(r):
    ret, mfe, after, days = r["수익률%"], r["최고%"], r["청산후10일%"], r["보유일"]
    reason = str(r["청산사유"])
    if not r["closed"]:
        return "보유 중", f"현재 {ret:+.1f}%. 보유 중 최고 {mfe:+.1f}%, 최저 {r['최저%']:+.1f}%."
    if reason.startswith("손절"):
        if after == after and after > 0:
            return "손절 후 반등", (f"손절 후 10일 안에 가격이 매수가보다 {after:+.1f}% 위까지 회복했어요. "
                                 "일시적 급락에 털렸을 수 있어요.")
        return "손절 (정상)", f"손절로 {ret:+.1f}%에서 끊었어요. 이후 10일 동안 매수가를 회복하지 못해 손절이 추가 손실을 막았어요."
    if mfe == mfe and mfe >= 15 and ret < mfe - 12:
        return "수익 반납", f"보유 중 최고 +{mfe:.1f}%까지 갔다가 {ret:+.1f}%로 마감. 상승분 상당 부분을 돌려줬어요."
    if ret > 0 and days >= 15:
        return "추세 수익", f"{days}일 동안 추세를 타서 {ret:+.1f}%. 전략이 의도대로 동작한 거래예요."
    if ret > 0:
        return "짧은 수익", f"{days}일 만에 {ret:+.1f}%로 마감. 추세가 길게 이어지지 않았어요."
    if days < 15:
        adx = r["진입ADX"]
        why = f"진입 시 ADX {adx:.0f}로 추세가 약했어요." if adx == adx and adx < 20 else "진입 직후 추세가 꺾였어요."
        return "휩쏘 손실", f"{days}일 만에 {ret:+.1f}%로 청산. {why} 횡보장에서 신호가 켜졌다 꺼진 전형적인 경우."
    return "추세 손실", f"{days}일 보유 후 {ret:+.1f}%. 추세가 이어지지 못했어요."


# ── 전략 점검 / 제안 ────────────────────────────────────────
def _whatif(candles, years=3, **over):
    """최근 n년 동안 BTC·ETH 반반 포트폴리오로 현재 설정 vs 수정 설정 비교"""
    from backtest import simulate_weights
    from strategies import final_weight
    res = {}
    for label, c in (("현재", _cfg()), ("수정안", _cfg(**over))):
        eqs = []
        for coin in candles:
            df = candles.get(coin)
            if df is None:
                continue
            start = df.index[-1] - pd.Timedelta(days=365 * years)
            eq, _ = simulate_weights(df, final_weight(df, c), c, start=start, capital=1_000_000)
            eqs.append(eq / eq.iloc[0])
        if not eqs:
            return None
        p = sum(e.reindex(eqs[0].index).ffill() for e in eqs) / len(eqs)
        yrs = (p.index[-1] - p.index[0]).days / 365
        res[label] = {"연수익%": ((p.iloc[-1]) ** (1 / yrs) - 1) * 100, "MDD%": (p / p.cummax() - 1).min() * 100}
    return res


def suggestions(trips: pd.DataFrame, candles: dict, source: str, account_dd=None):
    """반복되는 문제를 찾아 제안 목록 반환"""
    out = []
    closed = trips[trips["closed"]] if len(trips) else trips
    recent = closed.tail(8) if len(closed) else closed
    n = len(recent)

    if n:
        whip = recent[recent["복기유형"] == "휩쏘 손실"]
        weak = whip[whip["진입ADX"] < 20]
        if len(whip) >= 3 and len(weak) >= 2 and not getattr(config, "ENTRY_MIN_ADX", None):
            out.append({
                "제목": "횡보장 진입 줄이기",
                "근거": f"최근 {n}개 포지션 중 {len(whip)}개가 짧은 손실(휩쏘)이었고, 그중 {len(weak)}개는 진입 시 추세 강도(ADX)가 20 미만이었어요.",
                "제안": "추세 강도가 약할 때는 새로 진입하지 않도록 진입 필터를 추가해요.",
                "적용": "config.py 에서 ENTRY_MIN_ADX = 20",
                "whatif": _whatif(candles, ENTRY_MIN_ADX=20)})

        stops = recent[recent["복기유형"].isin(["손절 후 반등", "손절 (정상)"])]
        rebound = stops[stops["복기유형"] == "손절 후 반등"]
        if len(stops) >= 2 and len(rebound) >= 2 and config.STOP_LOSS and config.STOP_LOSS < 0.08:
            out.append({
                "제목": "손절폭 넓히기",
                "근거": f"최근 손절 {len(stops)}번 중 {len(rebound)}번이 손절 직후 다시 반등했어요.",
                "제안": f"손절선을 -{config.STOP_LOSS * 100:.0f}%에서 -8%로 넓혀 일시적 흔들림에 덜 털리게 해요.",
                "적용": "config.py 에서 STOP_LOSS = 0.08",
                "whatif": _whatif(candles, STOP_LOSS=0.08)})

        give = recent[recent["복기유형"] == "수익 반납"]
        if len(give) >= 2 and not getattr(config, "TRAILING_STOP", None):
            out.append({
                "제목": "이익 보호 장치 추가",
                "근거": f"최근 {n}개 포지션 중 {len(give)}개가 큰 수익을 냈다가 상당 부분을 돌려주고 끝났어요.",
                "제안": "보유 중 최고가 대비 15% 빠지면 매도하는 '이익 보호(트레일링 스톱)'를 추가해요.",
                "적용": "config.py 에서 TRAILING_STOP = 0.15",
                "whatif": _whatif(candles, TRAILING_STOP=0.15)})

    if account_dd is not None and account_dd <= -25:
        out.insert(0, {
            "제목": "⚠ 예상보다 큰 손실 — 점검 필요",
            "근거": f"계좌 최대 낙폭이 {account_dd:.1f}%로, 백테스트에서 본 범위(약 -24%)를 넘었어요.",
            "제안": "전략이 지금 시장과 맞지 않거나 실행에 문제가 있을 수 있어요. 금액을 줄이거나 STOP.txt 로 잠시 멈추고 매매일지를 확인하세요.",
            "적용": "폴더에 STOP.txt 파일 만들기 (실거래 즉시 정지)", "whatif": None})

    note = None
    if not out:
        if n < 3:
            note = f"{source} 기준 마감된 포지션이 {n}개뿐이라 아직 패턴을 판단하기 일러요. 거래가 쌓이면 자동으로 분석해요."
        else:
            note = f"{source} 기준 최근 {n}개 포지션에서 반복되는 문제가 보이지 않아요. 현재 설정을 유지하세요."
    return out, note
