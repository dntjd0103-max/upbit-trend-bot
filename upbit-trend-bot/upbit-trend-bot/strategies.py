"""
규칙 기반 매매 전략 모음.

각 전략은 캔들마다 '이 캔들이 끝난 시점에 코인을 들고 있고 싶은가'를
1(보유) 또는 0(현금)으로 돌려줍니다. 실제 주문은 백테스트 엔진이
'다음 캔들 시가'에 체결시키므로 미래 정보를 쓰지 않습니다.

새 전략을 추가하려면 함수를 하나 만들고 맨 아래 STRATEGIES 에 등록하면 됩니다.
"""
import numpy as np
import pandas as pd


def _stateful(enter: pd.Series, exit_: pd.Series) -> pd.Series:
    """진입 조건이 참이면 1, 청산 조건이 참이면 0, 둘 다 아니면 이전 상태 유지"""
    pos = np.zeros(len(enter))
    state = 0
    e, x = enter.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()
    for i in range(len(pos)):
        if state == 0 and e[i]:
            state = 1
        elif state == 1 and x[i]:
            state = 0
        pos[i] = state
    return pd.Series(pos, index=enter.index)


def buy_and_hold(df):
    """비교 기준: 처음에 사서 끝까지 보유"""
    return pd.Series(1.0, index=df.index)


def ma_cross(df):
    """이동평균 골든/데드크로스: 20선이 50선 위면 보유"""
    return (df["sma20"] > df["sma50"]).astype(float).where(df["sma50"].notna(), 0.0)


def rsi_reversion(df):
    """RSI 역추세: RSI 30 아래(과매도)에서 매수, 55 위로 회복하면 매도"""
    return _stateful(df["rsi14"] < 30, df["rsi14"] > 55)


def trend_momentum(df):
    """추세+모멘텀: 가격이 200선 위(상승장)이고 EMA12>EMA26일 때만 보유"""
    enter = (df["close"] > df["sma200"]) & (df["ema12"] > df["ema26"])
    exit_ = (df["ema12"] < df["ema26"]) | (df["close"] < df["sma200"])
    return _stateful(enter, exit_)


def bollinger_trend_dip(df):
    """상승장 눌림목: 200선 위에서 볼린저 하단 근처로 내려오면 매수, 중심선 회복 시 매도"""
    enter = (df["close"] > df["sma200"]) & (df["close"] < df["bb_low"] * 1.01)
    exit_ = df["close"] > df["bb_mid"]
    return _stateful(enter, exit_)


STRATEGIES = {
    "보유(Buy&Hold)": buy_and_hold,
    "이평 크로스(20/50)": ma_cross,
    "RSI 역추세": rsi_reversion,
    "추세+모멘텀": trend_momentum,
    "상승장 눌림목(볼린저)": bollinger_trend_dip,
}


# ── 최종 전략 (모의투자·실거래에서 사용) ─────────────────────────
def trend_momentum_voltarget(df, target_vol=0.6, min_adx=None):
    """
    추세+모멘텀 + 변동성 비중조절.
    - 언제 들고 있을지: 추세+모멘텀과 동일 (200일선 위 AND EMA12 > EMA26)
      min_adx 를 주면 '새로 들어갈 때만' 추세 강도(ADX)가 그 이상이어야 함 (횡보장 진입 방지)
    - 얼마나 들고 있을지: 목표 변동성 ÷ 최근 20일 변동성 (최대 100%)
      시장이 거칠면 비중을 줄이고, 잔잔하면 늘립니다.
    반환값: 0~1 사이 목표 비중
    """
    if min_adx:
        enter = (df["close"] > df["sma200"]) & (df["ema12"] > df["ema26"]) & (df["adx14"] >= min_adx)
        exit_ = (df["ema12"] < df["ema26"]) | (df["close"] < df["sma200"])
        on = _stateful(enter, exit_)
    else:
        on = trend_momentum(df)
    size = (target_vol / df["rvol20"]).clip(upper=1.0)
    return (on * size).fillna(0.0)


def final_weight(df, cfg):
    """config 설정대로 최종 전략 비중 계산 (모의·실거래·대시보드 공통)"""
    return trend_momentum_voltarget(df, cfg.VOL_TARGET, getattr(cfg, "ENTRY_MIN_ADX", None))


def market_regime(row) -> str:
    """대시보드용 장세 판단 (매매 결정에는 쓰지 않음)"""
    if pd.isna(row.get("sma200")):
        return "판단 불가"
    above = row["close"] > row["sma200"]
    strong = row.get("adx14", 0) >= 20
    if above and strong:
        return "상승 추세"
    if above:
        return "상승장 속 횡보"
    if strong:
        return "하락 추세"
    return "하락장 속 횡보"
