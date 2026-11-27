"""
매매 엔진 (모의투자·실거래 공용).

  python paper_trade.py          모의투자 (가상 계좌, paper 폴더)
  python live_trade.py           실거래   (업비트 실주문, live 폴더)
  python live_trade.py --check   실거래 점검 (주문 없음)
  python live_trade.py --smoke   5,500원 매수→매도로 주문 경로 점검

전략: 추세+모멘텀 + 변동성 비중조절 (strategies.trend_momentum_voltarget)
- 매일 오전 9시 일봉이 마감되면 목표 비중을 계산하고, 현재 비중과 REBALANCE_BAND 이상 차이 나면 맞춥니다.
- 1시간마다 손절선(STOP_LOSS)을 확인합니다.
- 모든 매매는 이유·장세·지표와 함께 trades.csv(매매일지)에 기록되고, 실행 후 dashboard.html 이 갱신됩니다.

실거래 안전장치
- LIVE_MAX_KRW 까지만 사용, 봇이 산 수량만 매도
- STOP.txt 가 있으면 주문 안 함. 주문 오류·잔고 불일치·하루 주문 초과 시 STOP.txt 를 만들고 정지
"""
import json
import math
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

import config
from data import fetch_candles
from indicators import add_all
from notify import send, telegram, telegram_file, telegram_ready
from strategies import final_weight, market_regime

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "STOP.txt")
MIN_ORDER = 5000
FEE = config.FEE_RATE
COST = config.FEE_RATE + config.SLIPPAGE      # 모의투자 체결 비용 가정
IN_ORDER = False


class Halt(Exception):
    """멈춰야 하는 상황"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def price(market):
    r = requests.get("https://api.upbit.com/v1/ticker", params={"markets": market}, timeout=10)
    r.raise_for_status()
    return float(r.json()[0]["trade_price"])


def analyze(market):
    """마감된 일봉 기준 목표 비중과 판단 근거"""
    df = add_all(fetch_candles(market, "day", 400).iloc[:-1])   # 진행 중인 오늘 캔들 제외
    w = final_weight(df, config)
    last = df.iloc[-1]
    return {
        "candle": df.index[-1].strftime("%Y-%m-%d"),
        "weight": round(float(w.iloc[-1]), 3),
        "regime": market_regime(last),
        "close": float(last["close"]),
        "sma200": float(last["sma200"]),
        "gap200": float(last["close"] / last["sma200"] - 1) * 100,
        "ema_up": bool(last["ema12"] > last["ema26"]),
        "adx": float(last["adx14"]),
        "vol": float(last["rvol20"]) * 100,
    }


# ── 브로커: 모의 / 실제 ─────────────────────────────────────
class PaperBroker:
    mode = "paper"

    def __init__(self, st):
        self.st = st

    def buy(self, market, krw):
        px = price(market)
        return krw * (1 - COST) / px, krw            # 수량, 쓴 원화

    def sell(self, market, qty):
        return qty * price(market) * (1 - COST)      # 받은 원화

    def holdings(self, coin):
        return None                                   # 잔고 대조 없음

    def krw(self):
        return float("inf")


class UpbitClient:
    """업비트 개인 API 직접 호출 (JWT 인증). 외부 라이브러리 없이 requests + PyJWT 만 사용"""
    BASE = "https://api.upbit.com/v1"

    def __init__(self, access, secret):
        self.access, self.secret = access, secret

    def _headers(self, params=None):
        import hashlib
        import uuid
        from urllib.parse import unquote, urlencode
        import jwt
        payload = {"access_key": self.access, "nonce": str(uuid.uuid4())}
        if params:
            q = unquote(urlencode(params, doseq=True)).encode("utf-8")
            payload["query_hash"] = hashlib.sha512(q).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = jwt.encode(payload, self.secret, algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode()
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _result(r, what):
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code >= 400 or (isinstance(body, dict) and "error" in body):
            err = body.get("error", {}) if isinstance(body, dict) else {}
            name, msg = err.get("name", f"HTTP {r.status_code}"), err.get("message", r.text[:200])
            hint = {"no_authorization_ip": " → 업비트 Open API 관리에서 허용 IP에 지금 IP를 등록하세요.",
                    "invalid_access_key": " → .env 의 Access Key 를 다시 확인하세요.",
                    "jwt_verification": " → .env 의 Secret Key 를 다시 확인하세요.",
                    "expired_access_key": " → API 키가 만료됐어요. 새로 발급하세요.",
                    "out_of_scope": " → API 키에 필요한 권한(자산 조회·주문 조회·주문하기)이 없어요.",
                    "insufficient_funds_bid": " → 원화 잔고가 부족해요.",
                    "under_min_total_bid": " → 최소 주문금액(5,000원) 미만이에요.",
                    "under_min_total_ask": " → 최소 주문금액(5,000원) 미만이에요."}.get(name, "")
            raise Halt(f"{what} 실패: {msg} ({name}){hint}")
        return body

    def accounts(self):
        r = requests.get(f"{self.BASE}/accounts", headers=self._headers(), timeout=10)
        return self._result(r, "잔고 조회")

    def api_keys(self):
        r = requests.get(f"{self.BASE}/api_keys", headers=self._headers(), timeout=10)
        return self._result(r, "API 키 조회")

    def order(self, params, what):
        r = requests.post(f"{self.BASE}/orders", json=params, headers=self._headers(params), timeout=10)
        return self._result(r, what)


class LiveBroker:
    mode = "live"

    def __init__(self, st):
        a, s = load_env()
        self.up = UpbitClient(a, s)

    def balances(self):
        return {b["currency"]: float(b["balance"]) for b in self.up.accounts()}

    def holdings(self, coin):
        return self.balances().get(coin, 0.0)

    def krw(self):
        return self.balances().get("KRW", 0.0)

    def _settle(self, before, field):
        for _ in range(15):
            time.sleep(1)
            after = self.balances()
            if after.get(field, 0) > before.get(field, 0):
                return after
        raise Halt("주문 후 잔고 변화를 확인하지 못했어요. 업비트 앱에서 체결 여부를 확인하세요. "
                   "체결됐다면 봇 기록과 실제 잔고가 달라졌으니 정리한 뒤 /resume 하세요.")

    def buy(self, market, krw):
        global IN_ORDER
        coin = market.split("-")[1]
        before = self.balances()
        IN_ORDER = True
        self.up.order({"market": market, "side": "bid", "price": str(int(krw)), "ord_type": "price"}, f"{market} 매수")
        after = self._settle(before, coin)
        IN_ORDER = False
        return after.get(coin, 0) - before.get(coin, 0), before.get("KRW", 0) - after.get("KRW", 0)

    def sell(self, market, qty):
        global IN_ORDER
        before = self.balances()
        IN_ORDER = True
        vol = math.floor(qty * 1e8) / 1e8                  # 반올림으로 잔고보다 많이 팔려는 주문 방지
        self.up.order({"market": market, "side": "ask", "volume": f"{vol:.8f}", "ord_type": "market"}, f"{market} 매도")
        after = self._settle(before, "KRW")
        IN_ORDER = False
        return after.get("KRW", 0) - before.get("KRW", 0)


def load_env():
    path = os.path.join(HERE, ".env")
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    a, s = env.get("UPBIT_ACCESS_KEY", ""), env.get("UPBIT_SECRET_KEY", "")
    if not a or not s or "발급받은" in a or "여기에" in a:
        raise Halt(".env 파일에 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 가 없습니다.")
    return a, s


# ── 상태 / 기록 ───────────────────────────────────────────
class Book:
    def __init__(self, mode):
        self.mode = mode
        self.dir = os.path.join(HERE, mode)
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.trades_path = os.path.join(self.dir, "trades.csv")
        self.snap_path = os.path.join(self.dir, "snapshots.csv")
        coins = config.LIVE_COINS if mode == "live" else config.PAPER_COINS
        total = config.LIVE_MAX_KRW if mode == "live" else config.PAPER_CASH
        st = None
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("version") != 2:                # 이전 버전 모의투자 기록은 새로 시작
                os.replace(self.state_path, self.state_path + ".v1.bak")
                st = None
        if st is None:
            st = {"version": 2, "started": now(), "capital": total, "orders": {}, "next_pos": 1, "coins": {}}
        for m in coins:
            st["coins"].setdefault(m, {"budget": total / len(coins), "qty": 0.0, "cost": 0.0, "entry": None,
                                       "pos_id": None, "blocked": False, "last_candle": None})
        if st["capital"] != total:                    # config 에서 한도를 바꾸면 차액을 코인별로 똑같이 반영
            diff = (total - st["capital"]) / len(coins)
            for m in coins:
                st["coins"][m]["budget"] = max(st["coins"][m]["budget"] + diff, 0.0)
            st.setdefault("capital_log", []).append({"시각": now(), "이전": st["capital"], "변경": total})
            st["capital"] = total
        self.st = st

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def log(self, row):
        pd.DataFrame([row]).to_csv(self.trades_path, mode="a", header=not os.path.exists(self.trades_path),
                                   index=False, encoding="utf-8-sig")

    def snapshot(self, rows):
        pd.DataFrame(rows).to_csv(self.snap_path, mode="a", header=not os.path.exists(self.snap_path),
                                  index=False, encoding="utf-8-sig")

    def count_order(self):
        if self.mode != "live":
            return
        day = datetime.now().strftime("%Y-%m-%d")
        self.st["orders"] = {day: self.st["orders"].get(day, 0) + 1}
        if self.st["orders"][day] > config.LIVE_MAX_ORDERS_PER_DAY:
            raise Halt(f"하루 최대 주문 횟수({config.LIVE_MAX_ORDERS_PER_DAY}회) 초과")


LABEL = {"paper": "모의투자", "live": "실거래"}


def trade(book, broker, m, side, krw_or_qty, reason, info, px):
    """매수(원화 금액) 또는 매도(수량) 실행 후 일지 기록"""
    c = book.st["coins"][m]
    book.count_order()
    book.save()
    tag = LABEL[book.mode]
    base = {"시각": now(), "코인": m, "사유": reason, "목표비중": info.get("weight"), "장세": info.get("regime"),
            "200일선대비%": round(info.get("gap200", float("nan")), 1), "변동성%": round(info.get("vol", float("nan")), 0),
            "ADX": round(info.get("adx", float("nan")), 0)}
    if side == "buy":
        qty, cost = broker.buy(m, krw_or_qty)
        if c["qty"] == 0:
            c["pos_id"] = book.st["next_pos"]
            book.st["next_pos"] += 1
            c["peak"] = px
        c["qty"] += qty
        c["cost"] += cost
        c["entry"] = c["cost"] / c["qty"]
        c["budget"] -= cost
        book.log({**base, "포지션": c["pos_id"], "구분": "매수", "체결가": round(cost / qty), "수량": f"{qty:.8f}",
                  "금액": round(cost), "실현수익률%": ""})
        send(f"[{tag}] {m} 매수", f"{cost:,.0f}원 · {reason}")
        print(f"  [{tag} 매수] {m} {cost:,.0f}원 ({reason})")
    else:
        qty = min(krw_or_qty, c["qty"])
        proceeds = broker.sell(m, qty)
        basis = c["entry"] * qty
        ret = (proceeds / basis - 1) * 100
        c["budget"] += proceeds
        c["qty"] -= qty
        c["cost"] -= basis
        pos = c["pos_id"]
        if c["qty"] * px < 1:                         # 전량 청산
            c.update(qty=0.0, cost=0.0, entry=None, pos_id=None, peak=None)
        book.log({**base, "포지션": pos, "구분": "매도", "체결가": round(proceeds / qty), "수량": f"{qty:.8f}",
                  "금액": round(proceeds), "실현수익률%": round(ret, 2)})
        send(f"[{tag}] {m} 매도", f"{proceeds:,.0f}원 · {ret:+.2f}% · {reason}")
        print(f"  [{tag} 매도] {m} {proceeds:,.0f}원 {ret:+.2f}% ({reason})")
    book.save()


def run(mode):
    global IN_ORDER
    IN_ORDER = False
    if mode == "live" and os.path.exists(STOP):
        print(f"[{now()}] STOP.txt 가 있어서 주문하지 않습니다.")
        book = Book(mode)                             # 정지 중에도 '살아 있음'은 기록 (재개 후 멈춤 경보 오작동 방지)
        book.st["last_run"] = now()
        book.save()
        return
    book = Book(mode)
    broker = LiveBroker(book.st) if mode == "live" else PaperBroker(book.st)
    print(f"[{now()}] {LABEL[mode]} 실행")
    last = book.st.get("last_run")
    if last:                                          # 컴퓨터가 꺼져 있던 시간 감지
        gap_h = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        if gap_h >= 3:
            book.st.setdefault("gaps", []).append({"from": last, "to": now(), "hours": round(gap_h, 1)})
            book.st["gaps"] = book.st["gaps"][-20:]
            print(f"  ⚠ {gap_h:.0f}시간 동안 실행되지 않았어요 ({last} 이후)")
            where = "컴퓨터가 꺼져 있거나 절전 중" if sys.platform == "win32" else "서버가 재시작되었거나 네트워크·업비트 점검 중"
            send(f"[{LABEL[mode]}] {gap_h:.0f}시간 동안 멈춰 있었어요",
                 f"{last[5:16]} 이후 실행 기록이 없어요. {where}이었을 수 있어요. 그동안 손절 확인을 못 했어요.")
    snaps = []
    new_day = False
    for m, c in book.st["coins"].items():
        coin = m.split("-")[1]
        held = broker.holdings(coin)
        if held is not None and c["qty"] > 0 and held < c["qty"] * 0.99:
            raise Halt(f"{m}: 봇 기록 수량({c['qty']:.8f})보다 실제 잔고({held:.8f})가 적습니다. "
                       "직접 팔았거나 출금했다면 live/state.json 정리가 필요합니다.")
        info = analyze(m)
        px = price(m)

        # 1) 손절 / 이익 보호 (매시간)
        if c["qty"] > 0:
            c["peak"] = max(c.get("peak") or px, px)
            ts = getattr(config, "TRAILING_STOP", None)
            if config.STOP_LOSS and px <= c["entry"] * (1 - config.STOP_LOSS):
                trade(book, broker, m, "sell", c["qty"], f"손절 -{config.STOP_LOSS * 100:.0f}%", info, px)
                c["blocked"] = True
            elif ts and px <= c["peak"] * (1 - ts):
                trade(book, broker, m, "sell", c["qty"], f"이익 보호 (최고가 대비 -{ts * 100:.0f}%)", info, px)
                c["blocked"] = True
            book.save()

        # 2) 새 일봉 마감 시 비중 조정
        if info["candle"] != c["last_candle"]:
            new_day = True
            if info["weight"] == 0:
                c["blocked"] = False
            tgt = 0.0 if c["blocked"] else info["weight"]
            sleeve = c["budget"] + c["qty"] * px
            cur = c["qty"] * px / sleeve if sleeve > 0 else 0
            print(f"  {m} {info['candle']} 마감: 장세={info['regime']}, 목표비중 {tgt:.0%}, 현재 {cur:.0%}")
            if tgt == 0 and c["qty"] > 0:
                trade(book, broker, m, "sell", c["qty"], "추세 이탈 (전량 매도)", info, px)
            elif tgt > 0 and (c["qty"] == 0 or abs(tgt - cur) > config.REBALANCE_BAND):
                diff = tgt * sleeve - c["qty"] * px
                if diff > 0:
                    krw = min(diff, c["budget"], broker.krw()) / (1 + FEE) - 10
                    if krw >= MIN_ORDER:
                        why = "추세 진입" if c["qty"] == 0 else "비중 확대 (변동성 감소)"
                        trade(book, broker, m, "buy", krw, why, info, px)
                    elif mode == "live" and broker.krw() < MIN_ORDER:
                        send(f"[실거래] {m} 매수 못 함", "원화가 부족합니다. 업비트에 입금이 필요합니다.")
                elif -diff >= MIN_ORDER:
                    trade(book, broker, m, "sell", -diff / px, "비중 축소 (변동성 증가)", info, px)
            c["last_candle"] = info["candle"]         # 판단·주문이 끝난 뒤에 기록 (중간에 오류 나면 다음 실행 때 다시 판단)
        book.save()
        sleeve = c["budget"] + c["qty"] * px
        snaps.append({"시각": now(), "코인": m, "가격": px, "보유수량": c["qty"], "평가금액": round(sleeve),
                      "실제비중": round(c["qty"] * px / sleeve, 3) if sleeve else 0, "목표비중": info["weight"],
                      "장세": info["regime"], "200일선대비%": round(info["gap200"], 1), "변동성%": round(info["vol"], 0),
                      "ADX": round(info["adx"], 0), "EMA상향": info["ema_up"], "손절대기": c["blocked"]})
    book.snapshot(snaps)
    book.st["last_run"] = now()
    book.save()
    total = sum(s["평가금액"] for s in snaps)
    print(f"  총 평가금액 {total:,.0f}원 ({(total / book.st['capital'] - 1) * 100:+.2f}%)")
    try:
        import dashboard
        dash = dashboard.build(mode)
    except Exception as e:
        dash = None
        print(f"  대시보드 갱신 실패: {e}")
    if new_day and getattr(config, "DAILY_SUMMARY", True) and telegram_ready():
        telegram(daily_summary(book, snaps, total))
        send_file = getattr(config, "DAILY_DASHBOARD_FILE", None)
        if dash and (send_file if send_file is not None else sys.platform != "win32"):
            telegram_file(dash, "📊 오늘의 대시보드 (파일을 눌러 브라우저로 열기)")


def daily_summary(book, snaps, total):
    """매일 오전 9시 일봉 마감 후 텔레그램 요약"""
    ret = (total / book.st["capital"] - 1) * 100
    lines = [f"[{LABEL[book.mode]}] {datetime.now():%m/%d} 일일 요약",
             f"총 {total:,.0f}원 ({ret:+.2f}%)", ""]
    for s in snaps:
        c = book.st["coins"][s["코인"]]
        state = f"보유 {s['실제비중']:.0%}" if s["보유수량"] > 0 else ("손절 후 대기" if s["손절대기"] else "현금")
        pnl = f" · 평가손익 {(s['가격'] / c['entry'] - 1) * 100:+.1f}%" if c.get("entry") else ""
        lines.append(f"{s['코인'][4:]} {s['장세']} · {state} · 목표 {s['목표비중']:.0%}{pnl}")
        lines.append(f"   200일선 {s['200일선대비%']:+.1f}% · 변동성 {s['변동성%']:.0f}%")
    if book.mode == "live":                          # 업비트 API 키 만료 30일 전부터 경고
        try:
            for k in LiveBroker(None).up.api_keys() or []:
                exp = datetime.strptime(str(k.get("expire_at", ""))[:10], "%Y-%m-%d")
                left = (exp - datetime.now()).days
                if left <= 30:
                    lines += ["", f"⚠ 업비트 API 키 만료까지 {left}일 ({exp:%Y-%m-%d}). 새 키를 발급해 .env 를 바꿔 주세요."]
        except Exception:
            pass
    gaps = [g for g in book.st.get("gaps", []) if g["to"][:10] == datetime.now().strftime("%Y-%m-%d")]
    lines += ["", "⚠ 오늘 멈춘 기록: " + ", ".join(f"{g['hours']:.0f}시간" for g in gaps) if gaps else "● 정상 작동 중"]
    return "\n".join(lines)


def halt(reason):
    with open(STOP, "w", encoding="utf-8") as f:
        f.write(f"{now()} 자동 정지\n사유: {reason}\n\n원인을 확인한 뒤 이 파일을 지우면 다시 동작합니다.\n")
    print(f"[정지] {reason}")
    send("[실거래] 자동 정지", reason[:200])


def check():
    print("1) .env 키 읽기...", end=" ")
    b = LiveBroker(None)
    print("OK")
    import re
    for name, v in (("Access Key", b.up.access), ("Secret Key", b.up.secret)):   # 값은 출력하지 않고 형태만 점검
        bad = [(i + 1, ch) for i, ch in enumerate(v) if not ch.isascii() or not ch.isalnum()]
        alnum = sum(1 for ch in v if ch.isascii() and ch.isalnum())
        print(f"   {name}: 전체 {len(v)}자 (영문·숫자 {alnum}자)")
        if bad:
            shown = ", ".join(f"{i}번째 '{'공백' if ch == ' ' else ch}'" for i, ch in bad[:15])
            print(f"     ⚠ 키에 들어가면 안 되는 문자: {shown}{' …' if len(bad) > 15 else ''}")
    if b.up.access == b.up.secret:
        print("   ⚠ Access Key 와 Secret Key 가 똑같아요. 둘 중 하나를 잘못 붙여넣은 것 같아요.")
    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        print(f"   지금 이 컴퓨터의 IP: {ip}  (업비트 허용 IP와 같아야 해요)")
    except Exception:
        pass
    print("2) 업비트 잔고 조회 (키 권한·허용 IP 확인)...", end=" ")
    bal = b.balances()
    print("OK")
    print(f"   원화 {bal.get('KRW', 0):,.0f}원 / " + ", ".join(f"{k} {v}" for k, v in bal.items() if k != "KRW"))
    try:
        for k in b.up.api_keys() or []:
            print(f"   API 키 만료일: {str(k.get('expire_at', '?'))[:10]}")
    except Exception:
        pass
    print("3) 현재 전략 판단")
    for m in config.LIVE_COINS:
        i = analyze(m)
        print(f"   {m}: 장세 {i['regime']}, 목표비중 {i['weight']:.0%} (200일선 대비 {i['gap200']:+.1f}%, 변동성 {i['vol']:.0f}%)")
    if bal.get("KRW", 0) < config.LIVE_MAX_KRW * 0.5:
        print(f"\n   참고: 원화가 한도({config.LIVE_MAX_KRW:,}원)보다 적습니다. 매수 신호 전에 입금해 두세요.")
    print("\n점검 완료. 주문은 하지 않았습니다.")


def smoke():
    m = "KRW-BTC"
    b = LiveBroker(None)
    print("실제 주문 점검: 5,500원어치 BTC를 사고 바로 팝니다. (수수료 약 6원)")
    qty, cost = b.buy(m, 5500)
    print(f"  매수 완료: {qty:.8f} BTC, {cost:,.0f}원")
    proceeds = b.sell(m, qty)
    print(f"  매도 완료: {proceeds:,.0f}원 회수 (차이 {proceeds - cost:+,.0f}원)")
    send("[실거래] 주문 점검 완료", f"매수 {cost:,.0f}원 → 매도 {proceeds:,.0f}원")
    print("\n주문 경로 정상입니다.")


def _rotate_log(path, limit=2_000_000):
    """로그가 2MB를 넘으면 log.old.txt 로 넘기고 새로 시작"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > limit:
            os.replace(path, path.replace(".txt", ".old.txt"))
    except OSError:
        pass


def _lock(mode):
    """같은 봇이 동시에 두 번 돌지 않게 잠금 (10분 지난 잠금은 무시)"""
    path = os.path.join(HERE, mode, ".lock")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 600:
            return None
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return path
    except OSError:
        return path


def _notify_error(mode, text):
    """같은 일시 오류 알림은 6시간에 한 번만"""
    path = os.path.join(HERE, mode, ".last_error_notice")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
            return
        with open(path, "w") as f:
            f.write(now())
    except OSError:
        pass
    send(f"[{LABEL[mode]}] 일시 오류", text)


def main(mode):
    os.makedirs(os.path.join(HERE, mode), exist_ok=True)
    _rotate_log(os.path.join(HERE, mode, "log.txt"))
    if sys.stdout is None:                            # 예약 작업(창 없음)일 때 로그 파일로
        sys.stdout = sys.stderr = open(os.path.join(HERE, mode, "log.txt"), "a", encoding="utf-8")
    args = sys.argv[1:]
    lock = None
    if "--check" not in args and "--smoke" not in args:
        lock = _lock(mode)
        if lock is None:
            print(f"[{now()}] 이전 실행이 아직 진행 중이라 이번 실행은 건너뜁니다.")
            return
    try:
        _main(mode, args)
    finally:
        if lock and os.path.exists(lock):
            try:
                os.remove(lock)
            except OSError:
                pass


def _main(mode, args):
    try:
        if "--check" in args:
            check()
        elif "--smoke" in args:
            smoke()
        else:
            run(mode)
            if "--open" in args:
                import webbrowser
                webbrowser.open("file://" + os.path.join(HERE, "dashboard.html"))
    except Halt as e:
        if "--check" in args or mode == "paper":
            print(f"\n[문제] {e}")
        else:
            halt(str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        if "--check" in args:
            return
        if mode == "live" and IN_ORDER:
            halt(f"주문 중 오류: {e} — 업비트 앱에서 체결 여부를 확인하세요")
        else:
            print(f"[{now()}] 일시 오류, 다음 실행 때 재시도: {e}")
            _notify_error(mode, f"다음 실행 때 다시 시도합니다 (같은 알림은 6시간에 한 번): {str(e)[:150]}")
