"""
텔레그램 원격 명령 (서버에서 1분마다 실행): python telegram_bot.py

연결된 본인 채팅에서 보낸 명령만 처리합니다. 다른 사람이 봇에게 보낸 메시지는 무시합니다.
  /status     지금 상태 요약
  /stop       실거래 즉시 정지 (STOP.txt 생성)
  /resume     실거래 재개 (STOP.txt 삭제, 다음 정시 7분부터 동작)
  /dashboard  대시보드 파일 받기
  /log        최근 실행 기록 20줄
  /export     매매일지(trades.csv)·자산 기록 파일 받기
  /help       명령 목록
"""
import json
import os
from datetime import datetime

import pandas as pd
import requests

from notify import _env, telegram, telegram_file

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "STOP.txt")
LIVE = os.path.join(HERE, "live")
OFFSET = os.path.join(LIVE, "tg_offset.txt")

HELP = ("사용할 수 있는 명령\n"
        "/status  지금 상태\n/stop  실거래 즉시 정지\n/resume  실거래 재개\n/dashboard  대시보드 파일\n"
        "/log  최근 실행 기록\n/export  매매일지 파일 받기\n/help  도움말")


def status_text():
    lines = []
    stopped = os.path.exists(STOP)
    lines.append("⏸ 실거래 정지 중 (/resume 으로 재개)" if stopped else "▶ 실거래 동작 중")
    st_path = os.path.join(LIVE, "state.json")
    if not os.path.exists(st_path):
        return "\n".join(lines + ["아직 실행 기록이 없어요."])
    st = json.load(open(st_path, encoding="utf-8"))
    last = st.get("last_run")
    if last:
        h = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        lines.append(f"마지막 실행 {last[5:16]} ({h * 60:.0f}분 전)" if h < 2 else f"⚠ 마지막 실행 {last[5:16]} ({h:.0f}시간 전)")
    snap = os.path.join(LIVE, "snapshots.csv")
    if os.path.exists(snap):
        s = pd.read_csv(snap)
        last_rows = s.groupby("코인").tail(1)
        total = last_rows["평가금액"].sum()
        lines.append(f"총 {total:,.0f}원 ({(total / st['capital'] - 1) * 100:+.2f}%, 한도 {st['capital']:,}원)")
        for _, r in last_rows.iterrows():
            c = st["coins"].get(r["코인"], {})
            state = f"보유 {r['실제비중']:.0%}" if c.get("qty", 0) > 0 else ("손절 후 대기" if c.get("blocked") else "현금")
            lines.append(f"{r['코인'][4:]} {r['장세']} · {state} · 목표 {r['목표비중']:.0%} · 200일선 {r['200일선대비%']:+.1f}%")
    return "\n".join(lines)


def handle(cmd):
    cmd = cmd.strip().split()[0].split("@")[0].lower()
    if cmd in ("/stop", "/정지"):
        with open(STOP, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} 텔레그램 /stop 으로 정지\n")
        telegram("⏸ 실거래를 정지했어요. 지금부터 주문이 나가지 않아요.\n보유 중인 코인은 그대로 있어요. 다시 시작하려면 /resume")
    elif cmd in ("/resume", "/재개"):
        reason = open(STOP, encoding="utf-8").read().strip() if os.path.exists(STOP) else ""
        if os.path.exists(STOP):
            os.remove(STOP)
        msg = "▶ 실거래를 재개했어요. 다음 정시 7분부터 동작해요."
        if reason and "자동 정지" in reason:
            msg += f"\n\n참고: 이전 정지는 자동 정지였어요.\n{reason[:300]}\n원인이 해결됐는지 꼭 확인하세요."
        telegram(msg)
    elif cmd in ("/status", "/상태"):
        telegram(status_text())
    elif cmd in ("/dashboard", "/대시보드"):
        try:
            import dashboard
            path = dashboard.build("live")             # 서버에서는 항상 실거래 기준
            telegram_file(path, "📊 대시보드 (파일을 눌러 브라우저로 열기)")
        except Exception as e:
            telegram(f"대시보드를 만들지 못했어요: {e}")
    elif cmd in ("/log", "/로그"):
        path = os.path.join(LIVE, "log.txt")
        if os.path.exists(path):
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()[-20:]
            telegram("최근 실행 기록 20줄\n" + "\n".join(l[:120] for l in lines))
        else:
            telegram("아직 실행 기록이 없어요.")
    elif cmd in ("/export", "/기록"):
        sent = False
        for name, cap in (("trades.csv", "매매일지 (세금 신고·복기용으로 보관하세요)"), ("snapshots.csv", "시간별 자산 기록")):
            path = os.path.join(LIVE, name)
            if os.path.exists(path):
                telegram_file(path, cap)
                sent = True
        if not sent:
            telegram("아직 내보낼 기록이 없어요.")
    elif cmd in ("/help", "/start", "/도움말"):
        telegram(HELP)
    else:
        telegram("모르는 명령이에요.\n" + HELP)


def main():
    token, chat = _env()
    if not token or not chat:
        return
    os.makedirs(LIVE, exist_ok=True)
    offset = int(open(OFFSET).read().strip()) if os.path.exists(OFFSET) else 0
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"offset": offset, "timeout": 0}, timeout=15).json()
    except Exception:
        return                                        # 잠깐 연결이 안 되면 다음 분에 다시 시도
    for u in r.get("result", []):
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat):      # 본인 채팅이 아니면 무시
            continue
        text = msg.get("text", "")
        if text.startswith("/"):
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 명령: {text}")
            try:
                handle(text)
            except Exception as e:
                telegram(f"명령 처리 중 오류: {e}")
    with open(OFFSET, "w") as f:
        f.write(str(offset))


if __name__ == "__main__":
    main()
