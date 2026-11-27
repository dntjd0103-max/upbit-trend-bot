#!/bin/bash
# 업비트 자동매매 서버 설치 스크립트 (Ubuntu 22.04/24.04)
# 사용법: bash install.sh
set -e
APP="$(cd "$(dirname "$0")" && pwd)"
FIRST=1                                           # 이미 설치된 서버(파이썬 환경이 있음)면 업데이트로 취급
{ [ -d "$APP/.venv" ] || [ -f "$APP/.installed" ]; } && FIRST=0
echo "== 1/5 시간대를 한국 시간으로 설정"
sudo timedatectl set-timezone Asia/Seoul
echo "== 2/5 프로그램 설치 (파이썬) — 2~3분 걸려요"
sudo apt-get update -y -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip > /dev/null
if ! swapon --show | grep -q .; then           # 메모리가 작은 서버에서 멈추지 않도록 1GB 여유 공간(스왑) 추가
  ( [ -f /swapfile ] || { sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile > /dev/null; } ) \
    && sudo swapon /swapfile && { grep -q "/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null; } \
    || echo "   (스왑 설정은 건너뛰었어요. 동작에는 문제 없어요)"
fi
echo "== 3/5 파이썬 패키지 설치"
cd "$APP"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements_server.txt
mkdir -p live
if [ ! -f .env ]; then
  echo "!! .env 파일이 없어요. PC의 .env 를 이 폴더에 올린 뒤 다시 실행하세요."; exit 1
fi
chmod 600 .env
if [ "$FIRST" = 1 ]; then                        # 처음 설치할 때만 안전하게 정지 상태로 시작
  [ -f STOP.txt ] || echo "서버 설치 직후 안전 정지 상태입니다. 텔레그램에서 /resume 을 보내면 시작해요." > STOP.txt
fi
date > .installed
echo "== 4/5 자동 실행 등록 (매시 7분 매매, 1분마다 텔레그램 명령 확인)"
ENV="PYTHONIOENCODING=utf-8 LANG=C.UTF-8"
( crontab -l 2>/dev/null | grep -v "upbit_ai_trader" || true ;
  echo "7 * * * * cd $APP && $ENV .venv/bin/python live_trade.py >> live/log.txt 2>&1 # upbit_ai_trader" ;
  echo "* * * * * cd $APP && $ENV .venv/bin/python telegram_bot.py >> live/bot.log 2>&1 # upbit_ai_trader" ) | crontab -
sudo systemctl restart cron
echo "== 5/5 업비트 연결 점검 (주문 없음)"
PYTHONIOENCODING=utf-8 .venv/bin/python live_trade.py --check || true
echo
if [ -f STOP.txt ]; then
  echo "설치 완료. 지금은 정지 상태예요. 텔레그램에서 /status 로 확인하고, 준비되면 /resume 을 보내세요."
else
  echo "업데이트 완료. 봇은 원래대로 동작 중이에요 (다음 정시 7분부터 새 코드로 실행)."
fi
