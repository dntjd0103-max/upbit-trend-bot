"""
대시보드 웹페이지 만들기: python dashboard.py [paper|live]

휴대폰에서 보기 좋게 만든 한 장짜리 페이지입니다 (텔레그램 /dashboard 로 받아 열기).
탭: 요약(수익률·상태·200일선 게이지) / 차트 / 일지(자동 복기) / 점검(수정 제안·전략 설명)
색 규칙은 업비트와 같습니다: 빨강 = 상승·매수, 파랑 = 하락·매도.
"""
import html
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import config
from backtest import simulate_weights
from data import DATA_DIR, load_candles
from indicators import add_all
from review import _cfg, round_trips, suggestions
from strategies import final_weight, market_regime

HERE = os.path.dirname(os.path.abspath(__file__))
LABEL = {"paper": "모의투자", "live": "실거래"}
UP, DOWN, MUTED = "var(--up)", "var(--down)", "var(--muted)"
TYPE_TONE = {"추세 수익": "up", "짧은 수익": "up", "보유 중": "hold", "손절 (정상)": "flat",
             "손절 후 반등": "warn", "수익 반납": "warn", "휩쏘 손실": "down", "추세 손실": "down"}


# ── 데이터 ────────────────────────────────────────────────
def candles_for(coin):
    try:
        raw = load_candles(coin, "day", 3300)
    except Exception:
        raw = pd.read_csv(os.path.join(DATA_DIR, f"{coin}_day_3300.csv"), index_col="time", parse_dates=True)
    return add_all(raw)


def esc(s):
    return html.escape(str(s))


def pct(v, nd=1, signed=True):
    if v is None or v != v:
        return "-"
    return f"{v:+.{nd}f}%" if signed else f"{v:.{nd}f}%"


def tone(v):
    if v is None or v != v or abs(v) < 0.005:
        return ""
    return "up" if v > 0 else "down"


def won(v):
    return f"{v:,.0f}원"


# ── SVG 차트 ──────────────────────────────────────────────
def _num(t):
    return pd.Timestamp(t).value / 1e9


def svg_lines(series, h=190, yfmt=lambda v: f"{v:,.0f}", spans=(), markers=(), ref=None, area=False):
    """series: [(pd.Series, color, width, dash)], spans: [(start, end)], markers: [(time, price, kind)]"""
    series = [(s.dropna(), c, w, d) for s, c, w, d in series if s is not None and len(s.dropna()) >= 2]
    if not series:
        return "<p class='empty'>그릴 데이터가 아직 없어요.</p>"
    W, L, R, T, B = 380, 4, 4, 8, 20
    t0 = min(_num(s.index[0]) for s, *_ in series)
    t1 = max(_num(s.index[-1]) for s, *_ in series)
    vals = pd.concat([s for s, *_ in series])
    lo, hi = float(vals.min()), float(vals.max())
    if markers:
        mv = [m[1] for m in markers]
        lo, hi = min(lo, min(mv)), max(hi, max(mv))
    if ref is not None:
        lo, hi = min(lo, ref), max(hi, ref)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    X = lambda t: L + (_num(t) - t0) / max(t1 - t0, 1) * (W - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (h - T - B)
    out = [f"<svg viewBox='0 0 {W} {h}' class='chart' role='img'>"]
    for a, b in spans:
        x0, x1 = max(X(a), L), min(X(b), W - R)
        if x1 <= L:
            continue
        out.append(f"<rect x='{x0:.1f}' y='{T}' width='{max(x1 - x0, 1.5):.1f}' height='{h - T - B}' class='span'/>")
    for g in np.linspace(lo + pad, hi - pad, 3):
        y = Y(g)
        out.append(f"<line x1='{L}' x2='{W - R}' y1='{y:.1f}' y2='{y:.1f}' class='grid'/>"
                   f"<text x='{L + 3}' y='{y - 3:.1f}' class='ylab'>{esc(yfmt(g))}</text>")
    if ref is not None:
        out.append(f"<line x1='{L}' x2='{W - R}' y1='{Y(ref):.1f}' y2='{Y(ref):.1f}' class='ref'/>")
    for i, (s, color, width, dash) in enumerate(series):
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in s.items())
        if area and i == 0:
            out.append(f"<polygon points='{X(s.index[0]):.1f},{h - B} {pts} {X(s.index[-1]):.1f},{h - B}' "
                       f"style='fill:{color};opacity:.10'/>")
        out.append(f"<polyline points='{pts}' fill='none' style='stroke:{color}' stroke-width='{width * 0.75}' "
                   f"{'stroke-dasharray=' + chr(39) + dash + chr(39) if dash else ''} stroke-linejoin='round'/>")
    for t, v, kind in markers:
        x, y = X(t), Y(v)
        if kind == "buy":
            out.append(f"<path d='M{x:.1f},{y - 7:.1f} l5,8 h-10z' class='mk buy'/>")
        elif kind == "stop":
            out.append(f"<path d='M{x - 5:.1f},{y - 5:.1f} l10,10 M{x + 5:.1f},{y - 5:.1f} l-10,10' class='mk stop'/>")
        else:
            out.append(f"<path d='M{x:.1f},{y + 7:.1f} l5,-8 h-10z' class='mk sell'/>")
    d0, d1 = pd.Timestamp(t0, unit="s"), pd.Timestamp(t1, unit="s")
    fmt = "%y.%m" if (d1 - d0).days > 120 else "%m/%d"
    out.append(f"<text x='{L}' y='{h - 6}' class='xlab'>{d0:{fmt}}</text>"
               f"<text x='{W - R}' y='{h - 6}' class='xlab' text-anchor='end'>{d1:{fmt}}</text>")
    out.append("</svg>")
    return "".join(out)


def svg_drawdown(eq, h=80):
    if eq is None or len(eq.dropna()) < 2:
        return ""
    dd = (eq / eq.cummax() - 1) * 100
    return svg_lines([(dd, DOWN, 1.5, None)], h=h, yfmt=lambda v: f"{v:.0f}%", ref=0, area=True)


def gauge(gap):
    """200일선 대비 위치 막대: 가운데 선이 200일선, -30% ~ +30%"""
    pos = min(max((gap + 30) / 60, 0), 1) * 100
    side = "up" if gap > 0 else "down"
    return (f"<div class='gauge' aria-label='200일선 대비 {gap:+.1f}%'><div class='g-track'>"
            f"<div class='g-zero'></div><div class='g-dot {side}' style='left:{pos:.1f}%'></div></div>"
            f"<div class='g-scale'><span>-30%</span><span>200일선</span><span>+30%</span></div></div>")


def month_grid(eq):
    eq = eq.dropna()
    if len(eq) < 2:
        return ""
    m = eq.resample("ME").last()
    r = m.pct_change()
    r.iloc[0] = m.iloc[0] / eq.iloc[0] - 1
    y = eq.resample("YE").last()
    yr = y.pct_change()
    yr.iloc[0] = y.iloc[0] / eq.iloc[0] - 1
    rows = []
    for year in sorted(set(r.index.year)):
        cells = []
        for mo in range(1, 13):
            v = r[(r.index.year == year) & (r.index.month == mo)]
            if len(v):
                x = v.iloc[0] * 100
                a = min(abs(x) / 15, 1)
                bg = f"rgba(214,58,58,{.12 + .6 * a:.2f})" if x > 0.05 else (f"rgba(47,111,219,{.12 + .6 * a:.2f})" if x < -0.05 else "transparent")
                cells.append(f"<td style='background:{bg}'>{x:+.0f}</td>")
            else:
                cells.append("<td></td>")
        yv = yr[yr.index.year == year].iloc[0] * 100
        rows.append(f"<tr><th>{str(year)[2:]}</th>{''.join(cells)}<td class='yr {tone(yv)}'>{yv:+.0f}</td></tr>")
    head = "".join(f"<th>{i}</th>" for i in range(1, 13))
    return f"<div class='scroll'><table class='months'><tr><th></th>{head}<th>연</th></tr>{''.join(rows)}</table></div>"


# ── 화면 조각 ─────────────────────────────────────────────
def explain(r):
    gap = (r["close"] / r["sma200"] - 1) * 100
    up = r["ema12"] > r["ema26"]
    if gap <= 0:
        return f"200일선까지 {abs(gap) / (1 + gap / 100):.1f}% 더 올라야 매수 조건에 들어가요. 그 전엔 현금으로 기다려요."
    if not up:
        return "200일선 위지만 단기 흐름이 아래라 쉬는 중이에요. 단기 흐름이 위로 돌아서면 매수해요."
    size = min(1, config.VOL_TARGET / r["rvol20"])
    return f"매수 조건을 모두 충족했어요. 최근 변동성 {r['rvol20'] * 100:.0f}%라 목표 비중은 {size:.0%}예요."


def trip_cards(trips, limit=None):
    if trips is None or trips.empty:
        return "<p class='empty'>아직 마감된 거래가 없어요. 매수 조건을 기다리는 중이에요.</p>"
    t = trips.iloc[::-1]
    if limit:
        t = t.head(limit)
    out = []
    for _, r in t.iterrows():
        k = TYPE_TONE.get(r["복기유형"], "flat")
        period = f"{r['진입일'][2:]} → {r['청산일'][2:] if r['청산일'] != '-' else '보유 중'}"
        out.append(f"""<article class='trip'>
<div class='trip-top'><b>{esc(r['코인'][4:])}</b><span class='badge {k}'>{esc(r['복기유형'])}</span><span class='trip-ret {tone(r['수익률%'])}'>{pct(r['수익률%'])}</span></div>
<div class='trip-meta'>{period}, {r['보유일']}일 보유, 진입 때 {esc(r['진입장세'])}</div>
<div class='trip-range'><span>최고 {pct(r['최고%'])}</span><span>최저 {pct(r['최저%'])}</span></div>
<p>{esc(r['복기'])}</p></article>""")
    return "".join(out)


def trip_summary(trips):
    c = trips[trips["closed"]] if len(trips) else trips
    if c.empty:
        return ""
    win = (c["수익률%"] > 0).mean() * 100
    aw = c.loc[c["수익률%"] > 0, "수익률%"].mean()
    al = c.loc[c["수익률%"] <= 0, "수익률%"].mean()
    return (f"<div class='kpis'><div><span>마감 거래</span><b>{len(c)}건</b></div>"
            f"<div><span>승률</span><b>{win:.0f}%</b></div>"
            f"<div><span>평균 수익</span><b class='up'>{pct(aw)}</b></div>"
            f"<div><span>평균 손실</span><b class='down'>{pct(al)}</b></div></div>")


# ── 본체 ─────────────────────────────────────────────────
def build(mode="paper"):
    d = os.path.join(HERE, mode)
    rd = lambda n: pd.read_csv(os.path.join(d, n)) if os.path.exists(os.path.join(d, n)) else pd.DataFrame()
    st = json.load(open(os.path.join(d, "state.json"), encoding="utf-8")) if os.path.exists(os.path.join(d, "state.json")) else None
    if st and st.get("version") != 2:
        st = None
    tr, sn = rd("trades.csv"), rd("snapshots.csv")
    coins = config.LIVE_COINS if mode == "live" else config.PAPER_COINS
    capital = st["capital"] if st else (config.LIVE_MAX_KRW if mode == "live" else config.PAPER_CASH)
    candles = {c: candles_for(c) for c in coins}
    cfg = _cfg()

    # 계좌
    eq = exp = None
    total, ret, dd, day_chg = capital, 0.0, None, None
    if len(sn):
        sn["시각"] = pd.to_datetime(sn["시각"])
        eq = sn.groupby("시각")["평가금액"].sum()
        total, ret = eq.iloc[-1], (eq.iloc[-1] / capital - 1) * 100
        dd = (eq / eq.cummax() - 1).min() * 100
        prev = eq[eq.index <= eq.index[-1] - pd.Timedelta(hours=23)]
        if len(prev):
            day_chg = (eq.iloc[-1] / prev.iloc[-1] - 1) * 100
        try:
            start = eq.index[0].normalize() + pd.Timedelta(hours=9)
            parts = [simulate_weights(candles[c], final_weight(candles[c], cfg), cfg, start=start,
                                      capital=capital / len(coins))[0] for c in coins]
            exp = sum(p.reindex(parts[0].index).ffill() for p in parts)
        except Exception:
            exp = None

    # 코인별 현재 판단
    coin_html = []
    for c in coins:
        df = candles[c]
        r = df.iloc[-2]                                    # 마감된 마지막 일봉
        w = float(final_weight(df.iloc[:-1], cfg).iloc[-1])
        cs = st["coins"].get(c, {}) if st else {}
        last = sn[sn["코인"] == c].iloc[-1] if len(sn) and (sn["코인"] == c).any() else None
        held = cs.get("qty", 0) > 0
        state = "보유 중" if held else ("손절 후 대기" if cs.get("blocked") else "현금 대기")
        gap = (r["close"] / r["sma200"] - 1) * 100
        pnl = ""
        if held and cs.get("entry") and last is not None:
            p = (last["가격"] / cs["entry"] - 1) * 100
            pnl = f"<span class='{tone(p)}'>평가손익 {pct(p)}</span>"
        ema = "위" if r["ema12"] > r["ema26"] else "아래"
        coin_html.append(f"""<section class='coin'>
<div class='coin-head'><h3>{c[4:]}</h3><span class='pill {'hold' if held else ''}'>{state}</span></div>
<div class='coin-price'>{r['close']:,.0f}<small>원</small> {pnl}</div>
{gauge(gap)}
<p class='why'>{esc(explain(r))}</p>
<dl class='facts'><div><dt>장세</dt><dd>{esc(market_regime(r))}</dd></div>
<div><dt>단기 흐름</dt><dd>{ema}</dd></div>
<div><dt>목표 비중</dt><dd>{w:.0%}</dd></div>
<div><dt>변동성</dt><dd>{r['rvol20'] * 100:.0f}%</dd></div></dl></section>""")

    # 업비트 실제 잔고 (실거래 모드에서만, 서버/PC의 .env 키로 조회)
    acct_html = ""
    if mode == "live":
        try:
            from trader import LiveBroker, price
            bal = LiveBroker(None).balances()
            rows, tot = [], bal.get("KRW", 0.0)
            rows.append(f"<div><dt>원화</dt><dd>{won(bal.get('KRW', 0.0))}</dd></div>")
            for c in coins:
                q = bal.get(c[4:], 0.0)
                v = q * price(c) if q > 0 else 0.0
                tot += v
                rows.append(f"<div><dt>{c[4:]}</dt><dd>{won(v)}<small> ({q:.6f}개)</small></dd></div>")
            acct_html = (f"<section class='coin acct'><div class='coin-head'><h3>업비트 실제 잔고</h3>"
                         f"<span class='pill'>{won(tot)}</span></div><dl class='facts'>{''.join(rows)}</dl>"
                         f"<p class='note' style='margin:10px 0 0'>봇은 이 중 최대 {won(capital)}까지만 사용해요. "
                         f"위 총자산·수익률은 봇이 운용하는 몫만 계산한 값이에요.</p></section>")
        except Exception as e:
            acct_html = f"<p class='note'>업비트 잔고를 불러오지 못했어요: {esc(str(e)[:120])}</p>"

    # 일지·백테스트
    real_trips = round_trips(tr, candles) if len(tr) else pd.DataFrame()
    bt_trips, bt_eqs, bt_logs = [], {}, {}
    for c in coins:
        df = candles[c]
        e, log = simulate_weights(df, final_weight(df, cfg), cfg, start=df.index[-1] - pd.Timedelta(days=730),
                                  capital=capital / len(coins))
        bt_eqs[c] = e
        if len(log):
            log["코인"] = c
            bt_logs[c] = log
            bt_trips.append(round_trips(log, {c: df}))
    bt_trips = pd.concat(bt_trips, ignore_index=True).sort_values("진입일") if bt_trips else pd.DataFrame()
    port = sum(bt_eqs.values())
    bt_ret, bt_dd = (port.iloc[-1] / port.iloc[0] - 1) * 100, (port / port.cummax() - 1).min() * 100

    # 제안
    closed_real = real_trips[real_trips["closed"]] if len(real_trips) else real_trips
    if len(closed_real) >= 3:
        sug, note = suggestions(real_trips, candles, f"{LABEL[mode]} 실전", dd)
        src = f"{LABEL[mode]} 실제 거래 기준"
    else:
        one = bt_trips[pd.to_datetime(bt_trips["진입일"]) >= port.index[-1] - pd.Timedelta(days=365)] if len(bt_trips) else bt_trips
        sug, note = suggestions(one, candles, "최근 1년 백테스트", dd)
        src = "실제 거래가 3건 쌓이기 전이라 최근 1년 백테스트 기준"
    sug_html = ""
    for s in sug:
        wi, verdict = s.get("whatif"), ""
        if wi:
            a, b = wi["현재"], wi["수정안"]
            if b["연수익%"] >= a["연수익%"] and b["MDD%"] >= a["MDD%"]:
                verdict = "<b class='good'>최근 3년 기준 개선돼요.</b>"
            elif b["연수익%"] < a["연수익%"] and b["MDD%"] < a["MDD%"]:
                verdict = "<b class='bad'>최근 3년 기준 오히려 나빠져요. 적용하지 않는 게 좋아요.</b>"
            else:
                verdict = "<b>수익과 위험이 엇갈려요.</b>"
            verdict = (f"<div class='whatif'><span>현재 연 {pct(a['연수익%'])}, 최대낙폭 {pct(a['MDD%'])}</span>"
                       f"<span>수정 후 연 {pct(b['연수익%'])}, 최대낙폭 {pct(b['MDD%'])}</span>{verdict}</div>")
        sug_html += (f"<section class='sug'><h3>{esc(s['제목'])}</h3><p>{esc(s['근거'])}</p><p>{esc(s['제안'])}</p>"
                     f"<p class='how'>적용: <code>{esc(s['적용'])}</code></p>{verdict}</section>")
    if note:
        sug_html += f"<p class='okmsg'>{esc(note)}</p>"

    # 상태 배너
    last_run = st.get("last_run") if st else None
    stopped = mode == "live" and os.path.exists(os.path.join(HERE, "STOP.txt"))
    gaps = (st.get("gaps") or [])[-5:] if st else []
    if stopped:
        health_cls, health_txt = "stop", "일시 정지 중 · 텔레그램 /resume 으로 재개"
    elif last_run:
        mins = (datetime.now() - datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        health_cls, health_txt = (("ok", f"정상 작동 중 · {mins:.0f}분 전 확인") if mins < 120 else
                                  ("bad", f"{mins / 60:.0f}시간째 실행 안 됨 · 마지막 {last_run[5:16]}"))
    else:
        health_cls, health_txt = "warn", "작동 중 · 첫 실행을 기다리는 중 (매시 7분)"

    # 기대 vs 실제
    exp_html = ""
    if exp is not None and len(exp):
        er = (exp.iloc[-1] / capital - 1) * 100
        gap = ret - er
        msg = ("백테스트와 거의 같게 움직이고 있어요." if abs(gap) < 1.5 else
               "실제가 백테스트보다 좋아요." if gap > 0 else
               "실제가 백테스트보다 뒤처져요. 멈춤 기록과 일지를 확인하세요.")
        exp_html = (f"<div class='compare'><div><span>실제</span><b class='{tone(ret)}'>{pct(ret, 2)}</b></div>"
                    f"<div><span>백테스트라면</span><b class='{tone(er)}'>{pct(er, 2)}</b></div>"
                    f"<p>{msg}</p></div>")

    # 차트
    acct_chart = ""
    if eq is not None and len(eq) >= 2:
        acct_chart = (f"<h3 class='sub'>내 계좌 (시작 = 100)</h3>"
                      + svg_lines([(eq / capital * 100, UP if ret >= 0 else DOWN, 2.2, None)]
                                  + ([(exp / capital * 100, MUTED, 1.4, "4 4")] if exp is not None else []),
                                  yfmt=lambda v: f"{v:.0f}", ref=100, area=True)
                      + "<div class='legend'><span class='l-main'>내 계좌</span><span class='l-dash'>백테스트 기대치</span></div>"
                      + "<h3 class='sub'>고점 대비 낙폭</h3>" + svg_drawdown(eq))
    since = candles[coins[0]].index[-1] - pd.Timedelta(days=365)
    price_charts = []
    for c in coins:
        df = candles[c][candles[c].index >= since]
        logs = tr[tr["코인"] == c].copy() if len(tr) else pd.DataFrame()
        note_src = "실제 매매"
        if logs.empty and c in bt_logs:
            logs, note_src = bt_logs[c].copy(), "백테스트 매매"
        spans, marks = [], []
        if len(logs):
            logs["시각"] = pd.to_datetime(logs["시각"])
            logs = logs[logs["시각"] >= since]
            for _, g in logs.groupby("포지션"):
                closed = (g["구분"] == "매도").any() and str(g.iloc[-1]["사유"]).startswith(("추세 이탈", "손절", "이익 보호"))
                spans.append((g["시각"].min(), g["시각"].max() if closed else df.index[-1]))
            for _, x in logs.iterrows():
                kind = "buy" if x["구분"] == "매수" else ("stop" if str(x["사유"]).startswith("손절") else "sell")
                marks.append((x["시각"], float(x["체결가"]), kind))
        price_charts.append(
            f"<h3 class='sub'>{c[4:]} 최근 1년 · {note_src}</h3>"
            + svg_lines([(df["close"], "var(--ink)", 1.6, None), (df["sma200"], "var(--line200)", 1.6, "6 4")],
                        h=200, yfmt=lambda v: f"{v / 1e4:,.0f}만", spans=spans, markers=marks)
            + "<div class='legend'><span class='l-ink'>가격</span><span class='l-200'>200일선</span>"
              "<span class='l-buy'>매수</span><span class='l-sell'>매도</span><span class='l-span'>보유 구간</span></div>")
    bt_chart = (svg_lines([(port / port.iloc[0] * 100, UP if bt_ret >= 0 else DOWN, 2.2, None)]
                          + [(candles[c]["close"].reindex(port.index) / candles[c]["close"].reindex(port.index).dropna().iloc[0] * 100,
                              MUTED, 1.1, "3 4") for c in coins[:1]],
                          yfmt=lambda v: f"{v:.0f}", ref=100, area=True)
                + f"<div class='legend'><span class='l-main'>전략</span><span class='l-dash'>{coins[0][4:]} 그냥 보유</span></div>"
                + "<h3 class='sub'>고점 대비 낙폭</h3>" + svg_drawdown(port))
    real_months = month_grid(eq.resample("D").last().ffill()) if eq is not None and (eq.index[-1] - eq.index[0]).days >= 20 else ""

    gap_html = ""
    if gaps:
        gap_html = "<details><summary>최근 멈춤 기록 " + str(len(gaps)) + "건</summary><ul class='gaps'>" + "".join(
            f"<li>{g['from'][5:16]} → {g['to'][5:16]} ({g['hours']:.0f}시간)</li>" for g in reversed(gaps)) + "</ul></details>"
    raw = ""
    if len(tr):
        rows = "".join(f"<li><span class='{'up' if x['구분'] == '매수' else 'down'}'>{esc(x['구분'])}</span> {esc(str(x['코인'])[4:])} "
                       f"{float(x['금액']):,.0f}원 <small>{esc(str(x['시각'])[2:16])} · {esc(x['사유'])}</small></li>"
                       for _, x in tr.iloc[::-1].head(30).iterrows())
        raw = f"<details><summary>주문 기록 최근 30건</summary><ul class='orders'>{rows}</ul></details>"

    started = f"{st['started'][:10]}부터" if st else "아직 시작 전"
    sl = f"매수가 대비 -{config.STOP_LOSS * 100:.0f}%" if config.STOP_LOSS else "없음"
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>{LABEL[mode]} · {won(total)} ({pct(ret, 2)})</title>
<style>{CSS}</style></head><body>
<header class="top">
  <div class="health {health_cls}">{health_txt}</div>
  <p class="mode">{LABEL[mode]}{' · 실제 주문' if mode == 'live' else ' · 가상 주문'} · {started}</p>
  <div class="total">{total:,.0f}<small>원</small></div>
  <div class="ret {tone(ret)}">{pct(ret, 2)}<span>시작 {capital:,.0f}원 대비</span></div>
  <div class="mini">
    <div><span>오늘</span><b class="{tone(day_chg)}">{pct(day_chg, 2)}</b></div>
    <div><span>최대 낙폭</span><b>{pct(dd) if dd is not None else '-'}</b></div>
    <div><span>마감 거래</span><b>{len(closed_real) if len(closed_real) else 0}건</b></div>
  </div>
</header>
<input type="radio" name="tab" id="t-sum" class="tabr" checked><input type="radio" name="tab" id="t-chart" class="tabr">
<input type="radio" name="tab" id="t-log" class="tabr"><input type="radio" name="tab" id="t-check" class="tabr">
<nav class="tabs"><label for="t-sum">요약</label><label for="t-chart">차트</label><label for="t-log">일지</label><label for="t-check">점검</label></nav>
<main>
<div class="panel" id="sum">
  {acct_html}
  {''.join(coin_html)}
  {exp_html}
  {gap_html}
</div>
<div class="panel" id="chart">
  {acct_chart or "<p class='empty'>실행 기록이 쌓이면 내 계좌 그래프가 생겨요.</p>"}
  {('<h3 class=sub>내 월별 수익률 (%)</h3>' + real_months) if real_months else ''}
  {''.join(price_charts)}
</div>
<div class="panel" id="log">
  <h2>내 거래 복기</h2>
  {trip_summary(real_trips) if len(real_trips) else ''}
  {trip_cards(real_trips)}
  {raw}
  <h2>참고: 같은 전략의 최근 2년</h2>
  <p class="note">전략 {pct(bt_ret)}, 최대 낙폭 {pct(bt_dd)}. 실제 결과가 이 범위를 크게 벗어나면 점검이 필요해요.</p>
  {bt_chart}
  <h3 class="sub">월별 수익률 (%)</h3>{month_grid(port)}
  {trip_summary(bt_trips)}
  <details><summary>백테스트 거래 {len(bt_trips)}건 복기 보기</summary>{trip_cards(bt_trips)}</details>
</div>
<div class="panel" id="check">
  <h2>전략 점검</h2>
  <p class="note">{esc(src)}. 제안은 자동으로 적용되지 않아요.</p>
  {sug_html}
  <h2>이 봇의 규칙</h2>
  <dl class="rules">
    <div><dt>살 때</dt><dd>가격이 200일선 위이고, 단기 흐름(EMA12)이 중기 흐름(EMA26)보다 위일 때</dd></div>
    <div><dt>얼마나</dt><dd>목표 변동성 {config.VOL_TARGET:.0%} ÷ 최근 20일 변동성 (최대 100%). 거칠면 적게, 잔잔하면 많이</dd></div>
    <div><dt>팔 때</dt><dd>두 조건 중 하나라도 깨지면 전량 매도</dd></div>
    <div><dt>손절</dt><dd>{sl}. 손절 후엔 신호가 한 번 꺼졌다 켜질 때까지 다시 사지 않아요</dd></div>
    <div><dt>언제</dt><dd>매일 오전 9시 7분에 판단, 손절은 매시 7분에 확인</dd></div>
  </dl>
  <p class="note">2020~2026년 BTC·ETH 반반 검증: 연 60%, 최대 낙폭 -24%. 과거 성과는 미래 수익을 보장하지 않아요.</p>
</div>
</main>
<footer>갱신 {datetime.now():%m/%d %H:%M}</footer>
</body></html>"""
    out = os.path.join(HERE, f"dashboard_{mode}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    main_mode = "live" if (os.path.exists(os.path.join(HERE, "live")) or os.path.exists(os.path.join(HERE, ".env"))) else "paper"
    if mode == main_mode:
        with open(os.path.join(HERE, "dashboard.html"), "w", encoding="utf-8") as f:
            f.write(page)
    return out


CSS = """
:root{--bg:#f4f6f9;--card:#fff;--ink:#1b2430;--muted:#8a94a6;--soft:#5b6576;--line:#e3e7ee;
--up:#d63a3a;--down:#2f6fdb;--hold:#b26a00;--line200:#c48a1d;--span:rgba(214,58,58,.07);--good:#1f8a4c}
@media (prefers-color-scheme:dark){:root{--bg:#11151b;--card:#1a2029;--ink:#e8ecf2;--muted:#7d8899;--soft:#a5afbf;
--line:#2a323e;--up:#ff6b6b;--down:#5b9bff;--hold:#e0a33a;--line200:#e0a33a;--span:rgba(255,107,107,.09);--good:#4cc38a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Pretendard,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
font-variant-numeric:tabular-nums;-webkit-text-size-adjust:100%}
.top,.tabs,main,footer{max-width:560px;margin:0 auto}
.top{padding:max(16px,env(safe-area-inset-top)) 20px 18px}
.health{display:inline-block;font-size:13px;padding:4px 10px;border-radius:999px;background:var(--line);color:var(--soft)}
.health.ok{background:rgba(31,138,76,.12);color:var(--good)} .health.ok::before{content:"● "}
.health.warn{background:var(--line);color:var(--soft)}
.health.bad{background:rgba(214,58,58,.14);color:var(--up);font-weight:700}
.health.stop{background:var(--line);color:var(--ink);font-weight:600}
.mode{margin:14px 0 2px;color:var(--soft);font-size:14px}
.total{font-size:40px;font-weight:800;letter-spacing:-.02em;line-height:1.15}
.total small,.coin-price small{font-size:.5em;font-weight:600;margin-left:2px}
.ret{font-size:24px;font-weight:700;margin-top:2px} .ret span{display:block;font-size:13px;font-weight:400;color:var(--muted)}
.mini{display:grid;grid-template-columns:repeat(3,1fr);margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.mini span,.kpis span,.compare span{display:block;font-size:12px;color:var(--muted)} .mini b{font-size:17px}
.up{color:var(--up)} .down{color:var(--down)}
.tabs{position:sticky;top:0;z-index:5;display:flex;background:var(--bg);border-bottom:1px solid var(--line);padding:0 12px}
.tabr{position:absolute;opacity:0;pointer-events:none}
.tabs label{flex:1;text-align:center;color:var(--muted);font-size:15px;font-weight:600;padding:12px 0;border-bottom:3px solid transparent;cursor:pointer;-webkit-tap-highlight-color:transparent}
#t-sum:checked~.tabs label[for=t-sum],#t-chart:checked~.tabs label[for=t-chart],#t-log:checked~.tabs label[for=t-log],
#t-check:checked~.tabs label[for=t-check]{color:var(--ink);border-bottom-color:var(--ink)}
.tabr:focus-visible~.tabs{outline:2px solid var(--down)}
.panel{display:none}
#t-sum:checked~main #sum,#t-chart:checked~main #chart,#t-log:checked~main #log,#t-check:checked~main #check{display:block}
main{padding:14px 14px 24px}
h2{font-size:19px;margin:26px 6px 8px} h3.sub{font-size:14px;color:var(--soft);font-weight:600;margin:22px 6px 6px}
.coin,.sug,.compare,.trip{background:var(--card);border-radius:14px;padding:16px 18px;margin-bottom:12px}
.coin-head{display:flex;align-items:center;justify-content:space-between} .coin h3{margin:0;font-size:20px}
.pill{font-size:13px;padding:3px 10px;border-radius:999px;background:var(--line);color:var(--soft)}
.pill.hold{background:rgba(214,58,58,.12);color:var(--up)}
.coin-price{font-size:22px;font-weight:700;margin:4px 0 10px} .coin-price span{font-size:14px;margin-left:6px}
.gauge{margin:6px 0 4px} .g-track{position:relative;height:10px;border-radius:5px;
background:linear-gradient(90deg,rgba(47,111,219,.22),rgba(47,111,219,.06) 50%,rgba(214,58,58,.06) 50%,rgba(214,58,58,.22))}
.g-zero{position:absolute;left:50%;top:-4px;bottom:-4px;width:2px;background:var(--line200)}
.g-dot{position:absolute;top:50%;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;border:3px solid var(--card)}
.g-dot.up{background:var(--up)} .g-dot.down{background:var(--down)}
.g-scale{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:4px}
.why{margin:10px 0 12px;font-size:15px}
.facts,.rules{margin:0;display:grid;grid-template-columns:1fr 1fr;gap:8px 16px}
.facts dt,.rules dt{font-size:12px;color:var(--muted)} .facts dd{margin:0;font-weight:600} .facts dd small{font-weight:400;color:var(--muted)}
.rules{grid-template-columns:1fr;background:var(--card);border-radius:14px;padding:16px 18px}
.rules dd{margin:0 0 4px}
.compare{display:grid;grid-template-columns:1fr 1fr} .compare b{font-size:20px} .compare p{grid-column:1/-1;margin:8px 0 0;font-size:14px;color:var(--soft)}
.chart{width:100%;height:auto;display:block;background:var(--card);border-radius:14px}
.grid{stroke:var(--line);stroke-width:1} .ref{stroke:var(--muted);stroke-width:1;stroke-dasharray:2 3}
.ylab,.xlab{fill:var(--muted);font-size:10.5px} .span{fill:var(--span)}
.mk.buy{fill:var(--up)} .mk.sell{fill:var(--down)} .mk.stop{stroke:var(--ink);stroke-width:2.2;fill:none}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--soft);margin:6px 6px 0}
.legend span::before{content:"";display:inline-block;width:14px;height:3px;margin-right:5px;vertical-align:middle;background:var(--ink)}
.l-main::before{background:var(--up)!important} .l-dash::before{background:repeating-linear-gradient(90deg,var(--muted) 0 4px,transparent 4px 7px)!important}
.l-200::before{background:var(--line200)!important} .l-buy::before{background:var(--up)!important;height:8px!important;width:8px!important;border-radius:2px}
.l-sell::before{background:var(--down)!important;height:8px!important;width:8px!important;border-radius:2px}
.l-span::before{background:rgba(214,58,58,.25)!important;height:10px!important}
.scroll{overflow-x:auto;background:var(--card);border-radius:14px;padding:8px 4px}
.months{border-collapse:collapse;font-size:11px;width:100%;table-layout:fixed} .months th,.months td{padding:5px 0;text-align:center}
.months th:first-child{width:20px} .months th:last-child{width:30px}
.months th{color:var(--muted);font-weight:500} .months .yr{font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);background:var(--card);border-radius:14px;padding:12px 14px;margin-bottom:12px}
.kpis b{font-size:16px}
.trip-top{display:flex;align-items:center;gap:8px} .trip-top b{font-size:17px}
.trip-ret{margin-left:auto;font-size:19px;font-weight:800}
.badge{font-size:12px;padding:2px 8px;border-radius:999px;font-weight:600}
.badge.up{background:rgba(214,58,58,.12);color:var(--up)} .badge.down{background:rgba(47,111,219,.13);color:var(--down)}
.badge.warn{background:rgba(178,106,0,.13);color:var(--hold)} .badge.flat,.badge.hold{background:var(--line);color:var(--soft)}
.trip-meta{font-size:13px;color:var(--muted);margin-top:2px}
.trip-range{display:flex;gap:14px;font-size:13px;color:var(--soft);margin-top:6px}
.trip p{margin:8px 0 0;font-size:14px}
.sug{border-left:4px solid var(--hold)} .sug h3{margin:0 0 6px;font-size:16px} .sug p{margin:4px 0;font-size:14px}
.how code{background:var(--line);padding:1px 6px;border-radius:4px;font-size:13px}
.whatif{margin-top:8px;font-size:13px;display:grid;gap:2px} .good{color:var(--good)} .bad{color:var(--up)}
.okmsg{background:var(--card);border-radius:14px;padding:14px 18px;font-size:14px;border-left:4px solid var(--good)}
.note{font-size:13px;color:var(--soft);margin:0 6px 10px} .rules+.note{margin-top:12px} .empty{color:var(--muted);font-size:14px;padding:6px}
details{margin:10px 2px} summary{cursor:pointer;color:var(--down);font-size:14px;padding:6px 4px}
.orders,.gaps{list-style:none;padding:0;margin:6px 0;font-size:14px} .orders li,.gaps li{padding:7px 4px;border-bottom:1px solid var(--line)}
.orders small{display:block;color:var(--muted)}
footer{color:var(--muted);font-size:12px;text-align:center;padding:10px 0 max(20px,env(safe-area-inset-bottom))}
@media (min-width:700px){.total{font-size:46px}}
"""


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in LABEL else ("live" if os.path.exists(os.path.join(HERE, ".env")) else "paper")
    p = build(m)
    print(f"대시보드 저장: {p}")
    if "--no-open" not in sys.argv:
        import webbrowser
        webbrowser.open("file://" + os.path.join(HERE, "dashboard.html"))
