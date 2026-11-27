"""
알림 보내기.

1) 윈도우 알림: 설정 없이 바로 동작 (화면 오른쪽 아래 팝업)
2) 텔레그램: 휴대폰으로 받고 싶을 때.
   .env 에 TELEGRAM_TOKEN 을 넣고 telegram_setup.bat 을 실행하면 나머지는 자동으로 설정됩니다.
"""
import base64
import subprocess
import sys

import requests

import config

_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode('{title}')) > $null
$x.Item(1).AppendChild($t.CreateTextNode('{body}')) > $null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


def windows_toast(title: str, body: str) -> bool:
    if sys.platform != "win32":
        return False
    clean = lambda s: s.replace("'", "''")
    script = _PS.format(title=clean(title), body=clean(body))
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                       timeout=20, capture_output=True, creationflags=0x08000000)  # 창 숨김
        return True
    except Exception as e:
        print(f"  윈도우 알림 실패: {e}")
        return False


def _env():
    """텔레그램 정보는 config.py 또는 .env 에서 읽음 (.env 권장)"""
    import os
    env = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    token = env.get("TELEGRAM_TOKEN") or getattr(config, "TELEGRAM_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID") or getattr(config, "TELEGRAM_CHAT_ID", "")
    return token, chat


def telegram_ready() -> bool:
    t, c = _env()
    return bool(t and c)


def telegram(text: str) -> bool:
    token, chat = _env()
    if not token or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=10)
        return r.ok
    except Exception as e:
        print(f"  텔레그램 알림 실패: {e}")
        return False


def telegram_file(path: str, caption: str = "") -> bool:
    """파일(예: 대시보드 html)을 텔레그램으로 보내기"""
    token, chat = _env()
    if not token or not chat:
        return False
    try:
        with open(path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                              data={"chat_id": chat, "caption": caption[:1000]}, files={"document": f}, timeout=30)
        return r.ok
    except Exception as e:
        print(f"  텔레그램 파일 전송 실패: {e}")
        return False


def send(title: str, body: str):
    windows_toast(title, body)
    telegram(f"{title}\n{body}")


def setup_telegram():
    """
    텔레그램 연결 도우미 (telegram_setup.bat 이 실행)
    1) .env 에 TELEGRAM_TOKEN 이 있는지 확인
    2) 봇에게 보낸 메시지에서 chat id 를 찾아 .env 에 자동으로 추가
    3) 테스트 메시지 전송
    """
    import os
    token, chat = _env()
    if not token:
        print("1) .env 에 TELEGRAM_TOKEN=봇토큰 줄을 먼저 추가하세요. (BotFather 가 알려준 토큰)")
        return
    print("1) 봇 토큰 확인...", end=" ")
    me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
    if not me.get("ok"):
        print(f"실패: {me.get('description', me)} → 토큰을 다시 확인하세요.")
        return
    print(f"OK (@{me['result']['username']})")
    if not chat:
        print("2) 봇에게 보낸 메시지에서 chat id 찾기...", end=" ")
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10).json()
        ids = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
        if not ids:
            print(f"못 찾았어요.\n   텔레그램에서 @{me['result']['username']} 를 열고 아무 메시지(예: 안녕)를 보낸 뒤 다시 실행하세요.")
            return
        chat = str(ids[-1])
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\nTELEGRAM_CHAT_ID={chat}\n")
        print(f"OK ({chat}) → .env 에 저장했어요")
    else:
        print(f"2) chat id 이미 설정됨 ({chat})")
    ok = telegram("✅ 업비트 자동매매 알림 연결 완료\n매수·매도·자동 정지 알림과 매일 오전 9시 요약이 여기로 와요.")
    print("3) 테스트 메시지 전송:", "OK — 휴대폰을 확인하세요." if ok else "실패")


if __name__ == "__main__":
    import sys
    if "--setup" in sys.argv:
        setup_telegram()
    else:
        ok_w = windows_toast("모의투자 알림 테스트", "이 알림이 보이면 윈도우 알림이 정상입니다.")
        ok_t = telegram("모의투자 알림 테스트\n이 메시지가 오면 텔레그램 알림이 정상입니다.")
        print(f"윈도우 알림: {'보냄' if ok_w else '사용 안 함'} / 텔레그램: {'보냄' if ok_t else '미설정 또는 실패'}")
