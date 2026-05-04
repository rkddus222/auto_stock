"""
trade_status (dict + JSON 파일) 변경 보호용 공용 RLock.

매매 루프(main.py)와 스케줄러 잡(reconciliation.py 등)이 같은 dict/파일을
동시에 mutate/write 하는 것을 방지한다. 모듈 import 순환을 피하기 위해
별도 파일로 분리.
"""
import threading

trade_state_lock: threading.RLock = threading.RLock()
