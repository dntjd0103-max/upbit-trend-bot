"""PC의 최신 파일로 서버용 upbit_server.zip 을 만듭니다 (upload_to_server.bat 이 자동 실행)."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["trader.py", "live_trade.py", "paper_trade.py", "strategies.py", "indicators.py", "backtest.py",
         "review.py", "dashboard.py", "data.py", "notify.py", "telegram_bot.py", "config.py",
         "requirements_server.txt", "install.sh", "SERVER.md"]

missing = [f for f in FILES if not os.path.exists(os.path.join(HERE, f))]
if missing:
    raise SystemExit(f"빠진 파일이 있어요: {', '.join(missing)}")
with zipfile.ZipFile(os.path.join(HERE, "upbit_server.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    for f in FILES:
        data = open(os.path.join(HERE, f), "rb").read()
        if f.endswith(".sh"):
            data = data.replace(b"\r\n", b"\n")      # 윈도우 줄바꿈이 섞이면 서버에서 실행이 안 됨
        z.writestr(f"upbit_ai_trader/{f}", data)
print(f"upbit_server.zip 생성 완료 ({len(FILES)}개 파일, config.py 포함)")
