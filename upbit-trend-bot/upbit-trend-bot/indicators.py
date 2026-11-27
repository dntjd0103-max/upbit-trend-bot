"""
보조지표 계산. 모두 '그 시점까지의 데이터'만 사용합니다 (미래 데이터 사용 없음).
"""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    """단순이동평균"""
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """지수이동평균"""
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI (Wilder 방식). 70 이상 과매수, 30 이하 과매도로 흔히 해석"""
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    """MACD 선, 시그널 선, 히스토그램"""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pd.Series, n=20, k=2.0):
    """볼린저 밴드 (중심선, 상단, 하단)"""
    mid = sma(close, n)
    sd = close.rolling(n).std()
    return mid, mid + k * sd, mid - k * sd


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    """ATR: 평균 변동폭. 변동성이 클수록 큼"""
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """자주 쓰는 지표를 한꺼번에 추가"""
    df = df.copy()
    c = df["close"]
    df["sma20"], df["sma50"], df["sma200"] = sma(c, 20), sma(c, 50), sma(c, 200)
    df["ema12"], df["ema26"] = ema(c, 12), ema(c, 26)
    df["rsi14"] = rsi(c, 14)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["bb_mid"], df["bb_up"], df["bb_low"] = bollinger(c)
    df["atr14"] = atr(df, 14)
    df["adx14"] = adx(df, 14)
    df["rvol20"] = realized_vol(c, 20)
    return df


def adx(df: pd.DataFrame, n=14) -> pd.Series:
    """ADX: 추세의 '강도' (방향과 무관). 대략 25 이상 뚜렷한 추세, 20 미만 횡보"""
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    sm = lambda x: x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    a = sm(tr)
    pdi, ndi = 100 * sm(pdm) / a, 100 * sm(ndm) / a
    dx = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)).fillna(0)
    return sm(dx)


def realized_vol(close: pd.Series, n=20) -> pd.Series:
    """최근 n일 변동성 (연율). 비트코인은 보통 40~80%"""
    return close.pct_change().rolling(n).std() * np.sqrt(365)
