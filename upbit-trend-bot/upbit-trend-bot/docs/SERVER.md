# 서버 운영 메모 (AWS Lightsail, Ubuntu)

- 위치: ~/upbit_ai_trader
- 자동 실행(cron): 매시 7분 live_trade.py, 1분마다 telegram_bot.py
- 기록: live/state.json(봇 상태), live/trades.csv(매매일지), live/snapshots.csv(시간별 자산), live/log.txt(실행 로그)
- 정지: 텔레그램 /stop (= STOP.txt 생성), 재개: /resume

## 업데이트
PC에서 upload_to_server.bat 실행 → PC 폴더의 파일로 zip 을 만들어 올리고 install.sh 를 다시 실행.
처음 설치일 때만 정지 상태로 시작하고, 이후 업데이트는 동작 상태를 그대로 유지해요.

## 직접 접속할 일이 생기면 (Lightsail 콘솔 → 인스턴스 → SSH로 연결)
- 로그 보기: tail -30 ~/upbit_ai_trader/live/log.txt
- 자동 실행 확인: crontab -l
- 수동 점검: cd ~/upbit_ai_trader && .venv/bin/python live_trade.py --check
