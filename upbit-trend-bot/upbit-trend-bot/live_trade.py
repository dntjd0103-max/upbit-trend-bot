"""실거래 실행: python live_trade.py [--check | --smoke]  (자세한 내용은 trader.py)  ⚠ 실제 돈이 움직입니다."""
from trader import main

if __name__ == "__main__":
    main("live")
