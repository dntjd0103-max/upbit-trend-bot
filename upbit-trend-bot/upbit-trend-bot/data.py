"""
업비트 공개 API에서 캔들(시세) 데이터를 가져옵니다.
API 키가 필요 없는 공개 데이터라 무료이고 계좌와도 상관없습니다.
"""
import os
import time
from datetime import datetime

import pandas as pd
import requests

BASE_URL = "https://api.upbit.com/v1/candles"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _endpoint(interval: str) -> str:
    if interval == "day":
        return f"{BASE_URL}/days"
    if interval.startswith("minute"):
        unit = interval.replace("minute", "")
        return f"{BASE_URL}/minutes/{unit}"
    raise ValueError(f"지원하지 않는 캔들 단위입니다: {interval}")


def fetch_candles(market: str, interval: str, count: int) -> pd.DataFrame:
    """업비트에서 최근 count개의 캔들을 가져옵니다 (한 번에 200개씩 나눠 요청)."""
    url = _endpoint(interval)
    rows = []
    to = None
    while len(rows) < count:
        params = {"market": market, "count": min(200, count - len(rows))}
        if to:
            params["to"] = to
        for attempt in range(5):
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:          # 요청이 너무 잦으면 잠깐 쉬었다가 재시도
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
            break
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        # 다음 요청은 이번에 받은 가장 오래된 캔들 이전부터
        to = batch[-1]["candle_date_time_utc"].replace("T", " ")
        time.sleep(0.15)                      # 업비트 요청 제한(초당 10회) 준수
        print(f"  데이터 수신 중... {len(rows)}/{count}", end="\r")
    print()

    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "candle_date_time_kst": "time",
        "opening_price": "open",
        "high_price": "high",
        "low_price": "low",
        "trade_price": "close",
        "candle_acc_trade_volume": "volume",
    })[["time", "open", "high", "low", "close", "volume"]]
    df["time"] = pd.to_datetime(df["time"])
    df = df.drop_duplicates("time").sort_values("time").set_index("time")
    return df.astype(float)


def load_candles(market: str, interval: str, count: int, refresh: bool = False) -> pd.DataFrame:
    """저장된 데이터가 있으면 그걸 쓰고, 없거나 refresh=True면 새로 받아 저장합니다."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{market}_{interval}_{count}.csv")
    if os.path.exists(path) and not refresh:
        age_hours = (time.time() - os.path.getmtime(path)) / 3600
        if age_hours < 12:
            print(f"저장된 데이터 사용: {path}")
            return pd.read_csv(path, index_col="time", parse_dates=True)
    print(f"업비트에서 {market} {interval} 캔들 {count}개를 가져옵니다...")
    df = fetch_candles(market, interval, count)
    df.to_csv(path)
    print(f"저장 완료: {path}  ({df.index[0]} ~ {df.index[-1]})")
    return df


if __name__ == "__main__":
    import config
    d = load_candles(config.MARKET, config.INTERVAL, config.CANDLE_COUNT, refresh=True)
    print(d.tail())
    print(f"현재 시각 {datetime.now():%Y-%m-%d %H:%M}, 캔들 {len(d)}개")


def load_fng(refresh: bool = False) -> pd.Series:
    """
    크립토 공포·탐욕 지수 (0=극단적 공포, 100=극단적 탐욕), 2018년 2월부터 일별.
    데이터 출처: Alternative.me (https://alternative.me/crypto/fear-and-greed-index/)
    인덱스는 업비트 일봉과 같은 '한국시간 오전 9시'로 맞춥니다.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "fear_greed.csv")
    if os.path.exists(path) and not refresh and (time.time() - os.path.getmtime(path)) / 3600 < 12:
        return pd.read_csv(path, index_col="time", parse_dates=True)["fng"]
    r = requests.get("https://api.alternative.me/fng/", params={"limit": 0}, timeout=15)
    r.raise_for_status()
    rows = r.json()["data"]
    s = pd.Series({pd.to_datetime(int(d["timestamp"]), unit="s") + pd.Timedelta(hours=9): float(d["value"])
                   for d in rows}, name="fng").sort_index()
    s.index.name = "time"
    s.to_csv(path)
    print(f"공포·탐욕 지수 저장: {s.index[0]:%Y-%m-%d} ~ {s.index[-1]:%Y-%m-%d} ({len(s)}일)")
    return s
