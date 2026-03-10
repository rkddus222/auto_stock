import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, time as dtime, timezone, timedelta

# app/ 에서 python main.py 로 실행해도 app 패키지를 찾을 수 있도록 루트를 path에 추가
_MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_MAIN_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

try:
    import asyncio
    from fastapi import FastAPI, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ModuleNotFoundError as e:
    print("필요한 패키지가 없습니다. 프로젝트 루트(auto_stock)에서 아래를 실행하세요:", file=sys.stderr)
    print("  pip install -r requirements.txt", file=sys.stderr)
    print("실행은 루트에서: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000", file=sys.stderr)
    sys.exit(1)

from app.api import kis_order, kis_market, kis_condition
from app.core.config import settings
from app.core.logger import logger
from app.core.slack import send_slack_notification
from app.db import models, session
from app.db.models import OrderType, OrderStatus
from app.services import portfolio as portfolio_service
from app.services import reconciliation as reconciliation_service
from app.services import indicators as indicators_service
from app.services import stock_scoring as stock_scoring_service
from app.services import llm_advisor as llm_advisor_service
from app.strategies.registry import StrategyRegistry
from app.services.websocket_manager import ws_manager

# --- Global Variables & Settings ---
TRADE_STATUS_FILE: Path = settings.base_dir / "trade_status.json"
scheduler = AsyncIOScheduler()
target_symbols: list[str] = []
trade_status: dict = {}
trading_enabled = True  # API로 on/off 가능
_last_slot_scan_time: float = 0.0  # 빈 자리 채우기: 마지막 종목 검색 시각 (초)

# WebSocket: 매매 이벤트 브로드캐스트용 (스레드에서 넣고, async 태스크가 전송)
_pending_broadcasts: list[dict] = []
_broadcast_lock = threading.Lock()
# 매매 job 중복 실행 방지: job은 10초마다 호출되지만, 실제 로직은 한 번에 하나만 실행
_trading_job_lock = threading.Lock()
# 매수 실패 시 종목별 쿨다운 (symbol -> 쿨다운 만료 시각 epoch)
_buy_cooldown: dict[str, float] = {}
_BUY_COOLDOWN_SECONDS = 300  # 5분
# LLM 매수 거부 시 종목별 쿨다운 (symbol -> 쿨다운 만료 시각 epoch)
_llm_reject_cooldown: dict[str, float] = {}

DEFAULT_STRATEGY_NAME = "volatility_breakout"
DEFAULT_STRATEGY_PARAMS = {"ma_period": 20, "trailing_stop_pct": 4.0, "k": getattr(settings, "VOLATILITY_BREAKOUT_K", 0.5)}

# 지수 필터 캐시 (매 사이클마다 API 호출 방지, 60초 유효)
_market_filter_cache: dict = {"ok": True, "ts": 0.0, "reason": ""}
_MARKET_FILTER_TTL = 60  # 초


def _get_tick_size(price: float) -> int:
    """한국 주식 호가 단위를 반환합니다."""
    if price < 2000:
        return 1
    elif price < 5000:
        return 5
    elif price < 20000:
        return 10
    elif price < 50000:
        return 50
    elif price < 200000:
        return 100
    elif price < 500000:
        return 500
    else:
        return 1000


def _calc_buy_limit_price(current_price: float, tick_offset: int = 2) -> int:
    """현재가 + N호가 지정가를 계산합니다. tick_offset=0이면 0 반환(시장가)."""
    if tick_offset <= 0:
        return 0
    tick = _get_tick_size(current_price)
    return int(current_price + tick * tick_offset)


def _check_market_filter() -> tuple[bool, str]:
    """시장 지수 필터: 지수가 MA 아래면 (False, 사유) 반환. 캐시 적용."""
    if not getattr(settings, "USE_MARKET_FILTER", False):
        return True, ""
    now_ts = time.time()
    if now_ts - _market_filter_cache["ts"] < _MARKET_FILTER_TTL:
        return _market_filter_cache["ok"], _market_filter_cache["reason"]
    try:
        index_code = getattr(settings, "MARKET_INDEX_CODE", "1001")
        ma_period = getattr(settings, "MARKET_MA_PERIOD", 5)
        index_price = kis_market.get_index_price(index_code)
        daily = kis_market.get_index_daily(index_code, days=ma_period + 1)
        if daily and len(daily) >= ma_period:
            closes = []
            for d in daily[:ma_period]:
                val = d.get("bstp_nmix_prpr") or d.get("bstp_nmix_clpr") or d.get("stck_clpr")
                if val:
                    closes.append(float(val))
            if len(closes) >= ma_period:
                ma = sum(closes) / len(closes)
                index_name = "코스닥" if index_code == "1001" else "코스피"
                if index_price < ma:
                    reason = f"{index_name} {index_price:.1f} < MA{ma_period} {ma:.1f}"
                    _market_filter_cache.update(ok=False, ts=now_ts, reason=reason)
                    return False, reason
                else:
                    _market_filter_cache.update(ok=True, ts=now_ts, reason="")
                    return True, ""
    except Exception as e:
        logger.debug(f"시장 지수 필터 조회 실패 (통과 처리): {e}")
    _market_filter_cache.update(ok=True, ts=now_ts, reason="")
    return True, ""


def _is_trading_session() -> bool:
    """
    현재 시각이 실제 주문 가능한 장 운영 시간인지 여부를 반환합니다.
    - 평일(월~금) 09:00 ~ 15:20 사이에만 True
    - 그 외 시간(장중단, 야간 등)에는 False
    """
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    # 월=0, 일=6 → 토/일 제외
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 0) <= t <= dtime(15, 20)


def _is_entry_allowed_time() -> bool:
    """
    신규 매수 허용 시간 여부 (설정값 기반).
    - 09:{ENTRY_NO_BEFORE_MINUTE} 이후 ~ {ENTRY_NO_AFTER_HOUR}:{ENTRY_NO_AFTER_MINUTE} 미만일 때만 True
    """
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    no_before_min = getattr(settings, "ENTRY_NO_BEFORE_MINUTE", 30) or 0
    no_after_h = getattr(settings, "ENTRY_NO_AFTER_HOUR", 14)
    no_after_m = getattr(settings, "ENTRY_NO_AFTER_MINUTE", 30)
    if no_before_min > 0 and t < dtime(9, no_before_min):
        return False
    if no_after_h is not None and no_after_m is not None:
        if t >= dtime(no_after_h, no_after_m):
            return False
    return True


def get_strategy_for_symbol(symbol: str):
    """종목별 StrategyConfig를 조회해 전략 인스턴스를 반환합니다. 없으면 기본 변동성 돌파."""
    db = session.SessionLocal()
    try:
        row = (
            db.query(models.StrategyConfig)
            .filter(models.StrategyConfig.symbol == symbol, models.StrategyConfig.is_active == True)
            .order_by(models.StrategyConfig.updated_at.desc())
            .first()
        )
        if row and row.strategy_name:
            params = json.loads(row.parameters) if isinstance(row.parameters, str) else (row.parameters or {})
            return StrategyRegistry.get_strategy(row.strategy_name, params)
    except Exception as e:
        logger.debug(f"종목 {symbol} 전략 설정 조회 실패, 기본 전략 사용: {e}")
    finally:
        db.close()
    return StrategyRegistry.get_strategy(DEFAULT_STRATEGY_NAME, DEFAULT_STRATEGY_PARAMS)

# --- State Persistence Functions ---
def save_trade_status():
    """거래 상태를 JSON 파일에 저장합니다."""
    TRADE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(trade_status, f, indent=4, ensure_ascii=False)


def load_trade_status():
    """JSON 파일에서 거래 상태를 불러옵니다. 조건검색 사용 시 HTS 조건검색 결과로 대상 종목을 갱신합니다."""
    global trade_status, target_symbols
    try:
        with open(TRADE_STATUS_FILE, "r", encoding="utf-8") as f:
            trade_status = json.load(f)
        logger.info("거래 상태를 파일에서 성공적으로 불러왔습니다.")
    except (FileNotFoundError, json.JSONDecodeError):
        trade_status = {}
        logger.warning("거래 상태 파일을 찾을 수 없어 새로 생성합니다.")

    # P7: 미보유 종목이 20개 초과 시 일괄 제거 (누적 방지)
    non_holding = [s for s, st in trade_status.items() if not st.get("bought")]
    if len(non_holding) > 20:
        for s in non_holding:
            del trade_status[s]
        logger.info(f"trade_status 정리: 미보유 {len(non_holding)}건 제거")
        save_trade_status()

    holding = [s for s, st in trade_status.items() if st.get("bought")]
    if settings.USE_VOLUME_RANK:
        # 거래량 상위 API 사용 (HTS 불필요)
        dynamic_list = kis_condition.get_top_volume_stocks()
        if dynamic_list:
            target_symbols = holding + [s for s in dynamic_list if s not in holding][: settings.CONDITION_SEARCH_MAX]
            logger.info(f"거래량 순위 적용: 대상 종목 {target_symbols} (보유 {len(holding)} + 신규 {len(target_symbols) - len(holding)})")
        else:
            target_symbols = settings.target_symbols_list or ["005930", "000660"]
            logger.warning("거래량 순위 결과가 비어 있어 TARGET_SYMBOLS로 대체합니다.")
    elif settings.USE_CONDITION_SEARCH:
        condition_list = kis_condition.get_target_stocks_by_condition()
        if condition_list:
            target_symbols = holding + [s for s in condition_list if s not in holding][: settings.CONDITION_SEARCH_MAX]
            logger.info(f"조건검색 적용: 대상 종목 {target_symbols} (보유 {len(holding)} + 신규 {len(target_symbols) - len(holding)})")
        else:
            target_symbols = settings.target_symbols_list or ["005930", "000660"]
            logger.warning("조건검색 결과가 비어 있어 TARGET_SYMBOLS로 대체합니다.")
    else:
        target_symbols = settings.target_symbols_list
        if not target_symbols:
            logger.warning("TARGET_SYMBOLS가 비어 있습니다. 기본 종목을 사용합니다.")
            target_symbols = ["005930", "000660"]

    for symbol in target_symbols:
        trade_status.setdefault(
            symbol,
            {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0},
        )

def _get_today_pl_and_assets():
    """당일 실현손익과 총자산을 (today_pl, total_assets)로 반환. 조회 실패 시 (0, None)."""
    KST = timezone(timedelta(hours=9))
    today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    db = session.SessionLocal()
    try:
        rows = (
            db.query(models.TradeLog)
            .filter(
                models.TradeLog.timestamp >= today_start,
                models.TradeLog.order_type == OrderType.SELL,
                models.TradeLog.status == OrderStatus.EXECUTED,
            )
            .all()
        )
        today_pl = sum(r.realized_pl or 0.0 for r in rows)
    except Exception:
        today_pl = 0.0
    finally:
        db.close()
    try:
        cash = kis_order.get_cash_balance()
        total_holding = 0.0
        for sym, st in trade_status.items():
            if st.get("bought"):
                try:
                    total_holding += kis_market.get_current_price(sym) * st["quantity"]
                except Exception:
                    total_holding += st.get("purchase_price", 0) * st.get("quantity", 0)
        total_assets = cash + total_holding
        return today_pl, total_assets
    except Exception:
        return today_pl, None


def _get_recent_trade_stats(window: int = 10) -> tuple[int, int, int]:
    """당일 매도 거래만으로 (연속 손실 횟수, 승수, 패수) 반환. 날이 바뀌면 자동 초기화(Lv0).
    연속 손실: 가장 최근 거래부터 연속으로 손실인 횟수.
    승/패: 당일 SELL 체결 건 중 윈도우(기본 10건) 내 승패 카운트.
    """
    KST = timezone(timedelta(hours=9))
    today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    db = session.SessionLocal()
    try:
        rows = (
            db.query(models.TradeLog)
            .filter(
                models.TradeLog.timestamp >= today_start,
                models.TradeLog.order_type == OrderType.SELL,
                models.TradeLog.status == OrderStatus.EXECUTED,
            )
            .order_by(models.TradeLog.timestamp.desc())
            .limit(window)
            .all()
        )
        consecutive = 0
        wins = 0
        losses = 0
        for i, r in enumerate(rows):
            if (r.realized_pl or 0) < 0:
                losses += 1
                if i == consecutive:  # 아직 연속 손실 중
                    consecutive += 1
            else:
                wins += 1
        return consecutive, wins, losses
    except Exception:
        return 0, 0, 0
    finally:
        db.close()


def _get_today_trade_count() -> int:
    """당일 체결 건수(BUY+SELL 성공)를 반환합니다."""
    KST = timezone(timedelta(hours=9))
    today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    db = session.SessionLocal()
    try:
        return (
            db.query(models.TradeLog)
            .filter(
                models.TradeLog.timestamp >= today_start,
                models.TradeLog.status == OrderStatus.EXECUTED,
            )
            .count()
        )
    except Exception:
        return 0
    finally:
        db.close()


def _log_trade(symbol: str, order_type: str, price: float, quantity: int, status: OrderStatus, kis_response: dict | None, realized_pl: float | None = None):
    """주문 결과를 DB에 기록합니다. SELL 시 realized_pl(매도금액-원금)을 넘기면 실현손익으로 저장합니다."""
    try:
        db = session.SessionLocal()
        try:
            log = models.TradeLog(
                symbol=symbol,
                order_type=OrderType.BUY if order_type == "BUY" else OrderType.SELL,
                price=price,
                quantity=quantity,
                status=status,
                kis_response=json.dumps(kis_response, ensure_ascii=False) if kis_response else None,
                realized_pl=realized_pl if realized_pl is not None else 0.0,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"거래 로그 저장 실패: {e}")


# --- Trading Jobs ---
def run_trading_strategy():
    """자동 매매 전략 실행 (매수 및 트레일링 스톱). 빈 자리 있으면 주기적으로 종목 재검색."""
    global target_symbols, _last_slot_scan_time
    if not trading_enabled:
        return

    # 장시간이 아니면 주문/조회 로직 자체를 스킵 (불필요한 주문 실패 방지)
    if not _is_trading_session():
        logger.debug("장 운영 시간이 아니므로 자동매매(run_trading_strategy)를 스킵합니다.")
        return

    # 이전 실행이 아직 끝나지 않았으면 이번 턴은 건너뜀 (스케줄러 'max instances' 스킵 방지)
    if not _trading_job_lock.acquire(blocking=False):
        logger.debug("이전 매매 작업 실행 중이라 이번 턴을 건너뜁니다.")
        return
    try:
        _run_trading_strategy_impl()
    finally:
        _trading_job_lock.release()


def _run_trading_strategy_impl():
    """run_trading_strategy 실제 로직 (락 획득 후 호출)."""
    global target_symbols, _last_slot_scan_time

    # --- 빈 자리 채우기: 동적 종목(거래량/조건검색) 사용 시, 슬롯 제한 및 주기 재검색 ---
    use_dynamic = settings.USE_VOLUME_RANK or settings.USE_CONDITION_SEARCH
    if use_dynamic:
        current_holdings = [s for s, st in trade_status.items() if st.get("bought")]
        holding_count = len(current_holdings)
        max_slots = settings.MAX_SLOTS or 3
        scan_interval = settings.SCAN_INTERVAL or 60

        if holding_count >= max_slots:
            # 꽉 찼으면 보유 종목만 감시
            target_symbols = current_holdings
        elif (time.time() - _last_slot_scan_time) >= scan_interval:
            # 자리 비었고, 마지막 검색 후 간격 경과 → 새 후보 검색
            logger.info(f"빈 슬롯 발견 ({holding_count}/{max_slots}). 새 종목 탐색 중...")
            try:
                if settings.USE_VOLUME_RANK:
                    new_candidates = kis_condition.get_top_volume_stocks()
                else:
                    new_candidates = kis_condition.get_target_stocks_by_condition()
                real_targets = [c for c in new_candidates if c not in current_holdings]
                slots_needed = max_slots - holding_count
                if getattr(settings, "USE_STOCK_SCORING", False) and real_targets:
                    ranked = stock_scoring_service.rank_candidates(real_targets, slots_needed)
                    target_symbols = current_holdings + ranked
                else:
                    target_symbols = current_holdings + real_targets[:slots_needed]
                for symbol in target_symbols:
                    trade_status.setdefault(
                        symbol,
                        {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0},
                    )
                _last_slot_scan_time = time.time()
                logger.info(f"타겟 리스트 갱신: {target_symbols} (보유 {holding_count} + 신규 {len(target_symbols) - holding_count})")
            except Exception as e:
                logger.error(f"빈 자리 채우기 검색 실패: {e}")

    logger.debug("자동 매매 로직 실행: 매수 및 트레일링 스톱 모니터링")
    try:
        cash_balance = kis_order.get_cash_balance()
        ratio = max(0.01, min(1.0, settings.BUDGET_RATIO))
        current_holdings = sum(1 for st in trade_status.values() if st.get("bought"))
        open_slots = max(1, (settings.MAX_SLOTS or 3) - current_holdings)
        budget_per_stock = (cash_balance * ratio) / open_slots
    except Exception as e:
        logger.error(f"예수금 조회 실패: {e}")
        return

    # 일별 리스크: 손실 한도 / 연패 / 일일 매매 횟수
    no_new_buy = False
    today_pl, total_assets = _get_today_pl_and_assets()
    loss_limit_pct = getattr(settings, "DAILY_LOSS_LIMIT_PCT", -2.0)
    if total_assets and total_assets > 0 and loss_limit_pct < 0:
        if (today_pl / total_assets * 100) <= loss_limit_pct:
            no_new_buy = True
            logger.info(f"일일 손실 한도 도달 (당일 실현손익 {today_pl:.0f}, 총자산 대비 {today_pl/total_assets*100:.2f}%) → 신규 매수 중단")
    if not no_new_buy:
        today_trades = _get_today_trade_count()
        max_trades = getattr(settings, "MAX_DAILY_TRADES", 6) or 0
        if max_trades > 0 and today_trades >= max_trades:
            no_new_buy = True
            logger.debug(f"일일 매매 횟수 한도 ({today_trades} >= {max_trades}) → 신규 매수 중단")
    if not no_new_buy:
        consec_losses, recent_wins, recent_losses = _get_recent_trade_stats(10)
        max_streak = getattr(settings, "MAX_CONSECUTIVE_LOSSES", 4) or 0
        max_slots_orig = settings.MAX_SLOTS or 3
        budget_cut_ratio = getattr(settings, "BUDGET_CUT_ON_STREAK", 0.7)
        total_recent = recent_wins + recent_losses
        win_rate = recent_wins / max(1, total_recent)

        if max_streak > 0 and consec_losses >= max_streak:
            # Level 2 (SEVERE): 활성 연패 중 → 슬롯 절반 + 예산 축소
            reduced_slots = max(1, max_slots_orig // 2)
            if current_holdings >= reduced_slots:
                no_new_buy = True
                logger.info(f"[리스크 Lv2] 연패 {consec_losses}회 → 슬롯 {max_slots_orig}→{reduced_slots}, 보유 {current_holdings}개 → 매수 중단")
            else:
                open_slots = max(1, reduced_slots - current_holdings)
                budget_per_stock = (cash_balance * ratio * budget_cut_ratio) / open_slots
                logger.info(f"[리스크 Lv2] 연패 {consec_losses}회 → 슬롯 {max_slots_orig}→{reduced_slots}, 예산 ×{budget_cut_ratio}")
        elif max_streak > 0 and total_recent >= 3 and win_rate < 0.4:
            # Level 1 (CAUTION): 연패 끊었지만 최근 10건 승률 40% 미만 → 슬롯 3/4
            reduced_slots = max(1, max_slots_orig * 3 // 4)
            if current_holdings >= reduced_slots:
                no_new_buy = True
                logger.info(f"[리스크 Lv1] 최근 승률 {win_rate:.0%} ({recent_wins}W/{total_recent}) → 슬롯 {max_slots_orig}→{reduced_slots}, 매수 중단")
            else:
                open_slots = max(1, reduced_slots - current_holdings)
                budget_per_stock = (cash_balance * ratio) / open_slots
                logger.info(f"[리스크 Lv1] 최근 승률 {win_rate:.0%} ({recent_wins}W/{total_recent}) → 슬롯 {max_slots_orig}→{reduced_slots}")
        else:
            logger.debug(f"[리스크 Lv0] 정상 (최근 {recent_wins}W {recent_losses}L, 연패 {consec_losses})")

    for symbol in target_symbols:
        try:
            strategy = get_strategy_for_symbol(symbol)
            trailing_pct = strategy.get_parameters().get("trailing_stop_pct", 3.0)
            current_price = kis_market.get_current_price(symbol)

            # 1. 매수 상태: 트레일링 스톱 및 전략 SELL 신호 확인
            if trade_status.get(symbol, {}).get("bought"):
                stop_price = trade_status[symbol]["stop_price"]
                quantity = trade_status[symbol]["quantity"]

                # 3단계 익절: +5% 1/3 매도+본절 이동, +10% 추가 1/3 매도, 나머지 트레일링
                purchase_price = trade_status[symbol].get("purchase_price", 0) or 0
                initial_quantity = trade_status[symbol].get("initial_quantity", quantity)
                # 최고가 추적 (트레일링/ATR 손절 기준점 — 현재가가 아닌 최고가 기준)
                high_price = trade_status[symbol].get("high_price", purchase_price or current_price)
                if current_price > high_price:
                    high_price = current_price
                    trade_status[symbol]["high_price"] = high_price
                stage1_done = trade_status[symbol].get("stage1_sell_done", False)
                stage2_done = trade_status[symbol].get("stage2_sell_done", False)
                if purchase_price <= 0:
                    pass
                elif initial_quantity < 3:
                    # 보유 수량이 3주 미만이면 단계 익절 불가 → 트레일링 스톱에 맡김
                    if not stage1_done:
                        logger.debug(f"[{symbol}] 보유 {initial_quantity}주 → 단계 익절 스킵 (트레일링 스톱 유지)")
                elif not stage1_done and current_price >= purchase_price * 1.05:
                    # +5%: 1/3 매도 + 스톱 본절로 이동
                    sell_qty = initial_quantity // 3
                    if sell_qty > 0 and quantity >= sell_qty:
                        try:
                            res = kis_order.place_order(symbol=symbol, quantity=sell_qty, price=0, order_type="SELL")
                            pl = (current_price - purchase_price) * sell_qty
                            _log_trade(symbol, "SELL", current_price, sell_qty, OrderStatus.EXECUTED, res, realized_pl=pl)
                            send_slack_notification(f"[익절 1/3 +5%] {symbol} ({sell_qty}주) | 현재가: {current_price:.0f}, 스톱 본절 이동")
                            trade_status[symbol]["quantity"] = quantity - sell_qty
                            queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": sell_qty})
                        except Exception as e:
                            _log_trade(symbol, "SELL", current_price, sell_qty, OrderStatus.FAILED, None)
                            logger.error(f"[{symbol}] 익절 1/3 매도 실패: {e}")
                            time.sleep(0.2)
                            continue
                    trade_status[symbol]["stop_price"] = purchase_price
                    trade_status[symbol]["stage1_sell_done"] = True
                    logger.info(f"[{symbol}] +5% 익절 1/3 처리, 손절가 본절로 이동 -> {purchase_price:.0f}")
                    save_trade_status()
                    time.sleep(0.2)
                    continue
                elif stage1_done and not stage2_done and current_price >= purchase_price * 1.10:
                    # +10%: 추가 1/3 매도
                    sell_qty = initial_quantity // 3
                    remaining = trade_status[symbol]["quantity"]
                    sell_qty = min(sell_qty, remaining)
                    if sell_qty > 0:
                        try:
                            res = kis_order.place_order(symbol=symbol, quantity=sell_qty, price=0, order_type="SELL")
                            pl = (current_price - purchase_price) * sell_qty
                            _log_trade(symbol, "SELL", current_price, sell_qty, OrderStatus.EXECUTED, res, realized_pl=pl)
                            send_slack_notification(f"[익절 2/3 +10%] {symbol} ({sell_qty}주) | 현재가: {current_price:.0f}")
                            trade_status[symbol]["quantity"] = remaining - sell_qty
                            queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": sell_qty})
                        except Exception as e:
                            _log_trade(symbol, "SELL", current_price, sell_qty, OrderStatus.FAILED, None)
                            logger.error(f"[{symbol}] 익절 2/3 매도 실패: {e}")
                            time.sleep(0.2)
                            continue
                    trade_status[symbol]["stage2_sell_done"] = True
                    save_trade_status()
                    time.sleep(0.2)
                    continue

                # 전략 SELL 신호 확인 (RSI 과매수)
                signal, _ = strategy.check_signal(symbol, current_price=current_price)
                if signal == "SELL":
                    if initial_quantity < 3:
                        # 소량 보유 → 전량 매도 (단계 익절 불가한 포지션)
                        logger.info(f"[{symbol}] RSI 매도 ({quantity}주 전량)")
                        try:
                            res = kis_order.place_order(symbol=symbol, quantity=quantity, price=0, order_type="SELL")
                            pl = (current_price - purchase_price) * quantity
                            _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.EXECUTED, res, realized_pl=pl)
                            send_slack_notification(f"[RSI 매도] {symbol} ({quantity}주) | 현재가: {current_price:.0f}")
                            trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
                            save_trade_status()
                            queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": quantity})
                        except Exception as e:
                            _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.FAILED, None)
                            logger.error(f"[{symbol}] 매도 주문 실패: {e}")
                        time.sleep(0.2)
                        continue
                    else:
                        # 다량 보유 → 트레일링 타이트닝 (추세추종 유지, 전량매도 대신 손절폭 절반 축소)
                        tight_stop = high_price * (1 - trailing_pct / 200)
                        if tight_stop > trade_status[symbol]["stop_price"]:
                            trade_status[symbol]["stop_price"] = tight_stop
                            logger.info(f"[{symbol}] RSI 과매수 → 트레일링 타이트닝 (손절가 {tight_stop:.0f}, 최고가 {high_price:.0f})")
                            save_trade_status()
                        time.sleep(0.2)
                        continue

                # 손절 조건 확인 (ATR 기반 또는 트레일링 스톱)
                use_atr = getattr(settings, "USE_ATR_STOP", False) or strategy.get_parameters().get("use_atr_stop", False)
                atr_mult = getattr(settings, "ATR_MULTIPLIER", 1.5)
                if use_atr and trade_status[symbol].get("atr") is not None:
                    atr = trade_status[symbol]["atr"]
                    atr_floor = high_price - atr * atr_mult
                    if atr_floor > stop_price:
                        stop_price = atr_floor
                if current_price <= stop_price:
                    stop_type = "ATR 손절" if (not stage1_done and use_atr) else "트레일링 스톱"
                    logger.info(f"[{symbol}] {stop_type} 매도! 현재가: {current_price}, 손절가: {stop_price}")
                    try:
                        res = kis_order.place_order(symbol=symbol, quantity=quantity, price=0, order_type="SELL")
                        pl = (current_price - purchase_price) * quantity
                        _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.EXECUTED, res, realized_pl=pl)
                        send_slack_notification(f"[{stop_type}] {symbol} ({quantity}주) | 현재가: {current_price:.0f}")
                        trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
                        save_trade_status()
                        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": quantity})
                    except Exception as e:
                        _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.FAILED, None)
                        logger.error(f"[{symbol}] 매도 주문 실패: {e}")
                    continue

                # 트레일링 스톱 상향: stage1 전에는 ATR 손절만 유지, stage1 후에 트레일링 활성
                if not stage1_done and initial_quantity >= 3:
                    # stage1 전: ATR 손절가만 갱신 (최고가 기준, 상향만)
                    if use_atr and trade_status[symbol].get("atr") is not None:
                        atr = trade_status[symbol]["atr"]
                        atr_stop = high_price - atr * atr_mult
                        if atr_stop > stop_price:
                            trade_status[symbol]["stop_price"] = atr_stop
                            logger.debug(f"[{symbol}] stage1 전 ATR 손절가 상향 -> {atr_stop:.0f} (최고가 {high_price:.0f})")
                            save_trade_status()
                else:
                    # stage1 후: 트레일링 스톱 활성 (최고가 기준, ATR floor 반영)
                    new_stop_price = high_price * (1 - trailing_pct / 100)
                    if use_atr and trade_status[symbol].get("atr") is not None:
                        atr = trade_status[symbol]["atr"]
                        atr_floor = high_price - atr * atr_mult
                        new_stop_price = max(new_stop_price, atr_floor)
                    if new_stop_price > stop_price:
                        trade_status[symbol]["stop_price"] = new_stop_price
                        logger.debug(f"[{symbol}] 트레일링 스톱 상향 -> {new_stop_price:.0f} (최고가 {high_price:.0f})")
                        save_trade_status()

            # 2. 미매수 상태: 매수 신호 확인 (진입 허용 시간 + 일별 리스크 통과 시)
            else:
                if no_new_buy:
                    logger.debug(f"[{symbol}] 매수 스킵: 일별 리스크(손실한도/일일횟수) 도달")
                    time.sleep(0.2)
                    continue
                if not _is_entry_allowed_time():
                    logger.debug(f"[{symbol}] 매수 스킵: 진입 허용 시간 아님 (09:{settings.ENTRY_NO_BEFORE_MINUTE:02d}~{settings.ENTRY_NO_AFTER_HOUR}:{settings.ENTRY_NO_AFTER_MINUTE:02d})")
                    time.sleep(0.2)
                    continue
                # 시장 지수 필터: 지수가 MA 아래면 매수 금지
                market_ok, market_reason = _check_market_filter()
                if not market_ok:
                    logger.debug(f"[{symbol}] 매수 스킵: 시장 하락 ({market_reason})")
                    time.sleep(0.2)
                    continue
                # 매수 쿨다운 체크 (이전 실패 후 5분 대기)
                cooldown_until = _buy_cooldown.get(symbol, 0)
                now_ts = time.time()
                if now_ts < cooldown_until:
                    logger.debug(f"[{symbol}] 매수 쿨다운 중 (남은 {cooldown_until - now_ts:.0f}초)")
                    time.sleep(0.2)
                    continue
                # 만료된 쿨다운 정리
                expired = [s for s, t in _buy_cooldown.items() if now_ts >= t]
                for s in expired:
                    del _buy_cooldown[s]
                signal, price_at_signal = strategy.check_signal(symbol, current_price=current_price)
                if signal == "BUY" and price_at_signal is not None:
                    # 상친거(시가 대비 N% 이상 상승) 매수 방지 — LLM 활성 시 바이패스 (LLM이 판단)
                    if not settings.USE_LLM_ADVISOR:
                        max_up_pct = getattr(settings, "ENTRY_MAX_UP_FROM_OPEN_PCT", 10.0) or 0
                        if max_up_pct > 0:
                            try:
                                daily = kis_market.get_daily_ohlcv(symbol, days=1)
                                if daily and len(daily) > 0:
                                    today_open = float(daily[0].get("stck_oprc") or 0)
                                    if today_open > 0 and price_at_signal >= today_open * (1 + max_up_pct / 100):
                                        logger.debug(f"[{symbol}] 매수 스킵: 시가 대비 {max_up_pct}% 이상 상승 (시가={today_open:.0f}, 현재가={price_at_signal:.0f})")
                                        time.sleep(0.2)
                                        continue
                            except Exception as e:
                                logger.debug(f"[{symbol}] 시가 조회 실패, 상승률 필터 스킵: {e}")

                    quantity_to_buy = int(budget_per_stock // price_at_signal)
                    if quantity_to_buy < 1:
                        logger.debug(f"[{symbol}] 매수 스킵: 예산 부족 (종목당 예산으로 1주 미만)")
                        time.sleep(0.2)
                        continue

                    # --- LLM 매수 어드바이저 검증 ---
                    if settings.USE_LLM_ADVISOR:
                        # LLM 거부 쿨다운 체크: 이전에 거부당한 종목은 일정 시간 재시도하지 않음
                        llm_cd_until = _llm_reject_cooldown.get(symbol, 0)
                        if now_ts < llm_cd_until:
                            remaining = int(llm_cd_until - now_ts)
                            logger.debug(f"[{symbol}] LLM 거부 쿨다운 중 (남은 {remaining}초)")
                            time.sleep(0.2)
                            continue
                        # 만료된 LLM 거부 쿨다운 정리
                        expired_llm = [s for s, t in _llm_reject_cooldown.items() if now_ts >= t]
                        for s in expired_llm:
                            del _llm_reject_cooldown[s]

                        try:
                            ohlcv_for_llm = kis_market.get_daily_ohlcv(symbol, days=5) or []
                            llm_indicators = getattr(strategy, "last_indicators", {})
                            llm_reason = getattr(strategy, "last_decision_reason", "")
                            llm_approved, llm_msg = llm_advisor_service.should_buy(
                                symbol=symbol,
                                current_price=price_at_signal,
                                indicators=llm_indicators,
                                ohlcv_recent=ohlcv_for_llm,
                                strategy_reason=llm_reason,
                            )
                            if not llm_approved:
                                cooldown_sec = getattr(settings, "LLM_REJECT_COOLDOWN", 1800)
                                _llm_reject_cooldown[symbol] = time.time() + cooldown_sec
                                logger.info(f"[{symbol}] LLM 매수 거부: {llm_msg} (쿨다운 {cooldown_sec}초)")
                                send_slack_notification(f"[LLM 매수 거부] {symbol} | {llm_msg} (재시도 {cooldown_sec//60}분 후)")
                                time.sleep(0.2)
                                continue
                            logger.info(f"[{symbol}] LLM 매수 승인: {llm_msg}")
                        except Exception as e:
                            logger.warning(f"[{symbol}] LLM 어드바이저 오류, 매수 진행 (fail-open): {e}")

                    use_atr_stop = getattr(settings, "USE_ATR_STOP", False)
                    atr_mult = getattr(settings, "ATR_MULTIPLIER", 1.5)
                    atr_val = indicators_service.get_atr(symbol) if use_atr_stop else None
                    if use_atr_stop and atr_val is not None and atr_val > 0:
                        initial_stop_price = price_at_signal - atr_val * atr_mult
                    else:
                        initial_stop_price = price_at_signal * (1 - trailing_pct / 100)

                    try:
                        tick_offset = getattr(settings, "BUY_PRICE_TICK_OFFSET", 2) or 0
                        buy_price = _calc_buy_limit_price(price_at_signal, tick_offset)
                        res = kis_order.place_order(symbol=symbol, quantity=quantity_to_buy, price=buy_price, order_type="BUY")
                        _log_trade(symbol, "BUY", price_at_signal, quantity_to_buy, OrderStatus.EXECUTED, res)
                        st_entry = {
                            "bought": True,
                            "purchase_price": price_at_signal,
                            "quantity": quantity_to_buy,
                            "initial_quantity": quantity_to_buy,
                            "high_price": price_at_signal,
                            "stop_price": initial_stop_price,
                            "stage1_sell_done": False,
                            "stage2_sell_done": False,
                        }
                        if use_atr_stop and atr_val is not None:
                            st_entry["atr"] = atr_val
                        trade_status[symbol] = st_entry
                        logger.info(f"[{symbol}] 매수 성공! 손절가: {initial_stop_price:.0f}")
                        send_slack_notification(f"[매수 성공] {symbol}({quantity_to_buy}주) | 매수가: {price_at_signal:.0f}")
                        save_trade_status()
                        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "BUY", "price": price_at_signal, "quantity": quantity_to_buy})
                        _buy_cooldown.pop(symbol, None)  # 매수 성공 시 쿨다운 제거
                    except Exception as e:
                        _log_trade(symbol, "BUY", price_at_signal, quantity_to_buy, OrderStatus.FAILED, None)
                        _buy_cooldown[symbol] = time.time() + _BUY_COOLDOWN_SECONDS
                        logger.error(f"[{symbol}] 매수 주문 실패 (쿨다운 {_BUY_COOLDOWN_SECONDS}초 설정): {e}")

            time.sleep(0.2)

        except Exception as e:
            logger.error(f"[{symbol}] 매매 로직 실행 중 에러: {e}")


def sell_symbol(symbol: str, quantity: int | None = None) -> dict:
    """
    특정 종목을 개별 매도합니다. quantity가 없거나 0이면 전량 매도.
    :return: {"success": bool, "message": str, "sold_quantity": int or 0}
    """
    st = trade_status.get(symbol)
    if not st or not st.get("bought"):
        return {"success": False, "message": f"{symbol} 보유 종목이 아닙니다.", "sold_quantity": 0}
    held = st["quantity"]
    if held <= 0:
        return {"success": False, "message": f"{symbol} 보유 수량이 없습니다.", "sold_quantity": 0}
    sell_qty = (quantity if quantity and quantity > 0 else held)
    sell_qty = min(sell_qty, held)
    purchase_price = st.get("purchase_price", 0) or 0
    try:
        current_price = kis_market.get_current_price(symbol)
        res = kis_order.place_order(symbol=symbol, quantity=sell_qty, price=0, order_type="SELL")
        pl = (current_price - purchase_price) * sell_qty if purchase_price else 0.0
        _log_trade(symbol, "SELL", current_price, sell_qty, OrderStatus.EXECUTED, res, realized_pl=pl)
        send_slack_notification(f"[개별 매도] {symbol}, 수량: {sell_qty}, 현재가: {current_price:.0f}")
        remaining = held - sell_qty
        if remaining <= 0:
            trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
        else:
            trade_status[symbol]["quantity"] = remaining
        save_trade_status()
        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": sell_qty})
        return {"success": True, "message": f"{symbol} {sell_qty}주 매도 체결", "sold_quantity": sell_qty}
    except Exception as e:
        _log_trade(symbol, "SELL", 0.0, sell_qty, OrderStatus.FAILED, None)
        logger.error(f"[{symbol}] 개별 매도 실패: {e}")
        return {"success": False, "message": str(e), "sold_quantity": 0}


def buy_symbol(symbol: str, quantity: int | None = None, amount: int | None = None) -> dict:
    """
    수동 매수: 시장가로 매수 후 trade_status에 등록하여 자동 매도 관리를 받는다.
    :param symbol: 종목코드 (예: "005930")
    :param quantity: 매수 수량 (직접 지정)
    :param amount: 매수 금액 (원 단위, quantity 미지정 시 사용)
    :return: {"success": bool, "message": str, ...}
    """
    # 이미 보유 중이면 에러
    st = trade_status.get(symbol)
    if st and st.get("bought"):
        return {"success": False, "message": f"{symbol} 이미 보유 중인 종목입니다."}

    # 현재가 조회
    try:
        current_price = kis_market.get_current_price(symbol)
    except Exception as e:
        return {"success": False, "message": f"현재가 조회 실패: {e}"}

    if not current_price or current_price <= 0:
        return {"success": False, "message": f"{symbol} 현재가를 조회할 수 없습니다."}

    # 수량 결정
    if quantity and quantity > 0:
        qty = quantity
    elif amount and amount > 0:
        qty = amount // int(current_price)
        if qty < 1:
            return {"success": False, "message": f"금액({amount:,}원)으로 1주도 매수할 수 없습니다. (현재가: {current_price:,.0f}원)"}
    else:
        return {"success": False, "message": "quantity 또는 amount 중 하나를 지정해야 합니다."}

    # ATR 기반 손절가 계산
    use_atr_stop = getattr(settings, "USE_ATR_STOP", False)
    atr_mult = getattr(settings, "ATR_MULTIPLIER", 1.5)
    trailing_pct = DEFAULT_STRATEGY_PARAMS.get("trailing_stop_pct", 4.0)
    atr_val = indicators_service.get_atr(symbol) if use_atr_stop else None
    if use_atr_stop and atr_val is not None and atr_val > 0:
        initial_stop_price = current_price - atr_val * atr_mult
    else:
        initial_stop_price = current_price * (1 - trailing_pct / 100)

    # 시장가 매수 주문
    try:
        res = kis_order.place_order(symbol=symbol, quantity=qty, price=0, order_type="BUY")
        _log_trade(symbol, "BUY", current_price, qty, OrderStatus.EXECUTED, res)
        st_entry = {
            "bought": True,
            "purchase_price": current_price,
            "quantity": qty,
            "initial_quantity": qty,
            "high_price": current_price,
            "stop_price": initial_stop_price,
            "stage1_sell_done": False,
            "stage2_sell_done": False,
        }
        if use_atr_stop and atr_val is not None:
            st_entry["atr"] = atr_val
        trade_status[symbol] = st_entry
        save_trade_status()
        logger.info(f"[{symbol}] 수동 매수 성공! {qty}주 @ {current_price:.0f}, 손절가: {initial_stop_price:.0f}")
        send_slack_notification(f"[수동 매수] {symbol}({qty}주) | 매수가: {current_price:.0f}")
        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "BUY", "price": current_price, "quantity": qty})
        return {
            "success": True,
            "message": f"{symbol} {qty}주 매수 체결 (시장가)",
            "symbol": symbol,
            "quantity": qty,
            "price": current_price,
            "stop_price": initial_stop_price,
        }
    except Exception as e:
        _log_trade(symbol, "BUY", current_price, qty, OrderStatus.FAILED, None)
        logger.error(f"[{symbol}] 수동 매수 실패: {e}")
        return {"success": False, "message": f"매수 주문 실패: {e}"}


def sell_all_at_close():
    """장 마감 전량 매도 (스케줄 또는 API 호출)"""
    global trading_enabled
    trading_enabled = False  # 매도 중 재매수 방지
    logger.info("장 마감 전량 매도 로직을 시작합니다. (자동매매 비활성화)")
    # trade_status 전체 순회: target_symbols가 아닌 실제 보유 종목 기준
    for symbol, st in list(trade_status.items()):
        if st.get("bought"):
            quantity = st["quantity"]
            purchase_price = st.get("purchase_price", 0) or 0
            try:
                current_price = kis_market.get_current_price(symbol)
                res = kis_order.place_order(symbol=symbol, quantity=quantity, price=0, order_type="SELL")
                pl = (current_price - purchase_price) * quantity if purchase_price else 0.0
                _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.EXECUTED, res, realized_pl=pl)
                send_slack_notification(f"[장 마감 매도] {symbol}, 수량: {quantity}, 현재가: {current_price:.0f}")
                trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
                queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": quantity})
            except Exception as e:
                _log_trade(symbol, "SELL", 0.0, quantity, OrderStatus.FAILED, None)
                logger.error(f"[{symbol}] 장 마감 매도 실패: {e}")
            time.sleep(1)
    save_trade_status()


def job_portfolio_snapshot():
    """5분마다 포트폴리오 스냅샷 저장"""
    try:
        portfolio_service.create_portfolio_snapshot(trade_status)
    except Exception as e:
        logger.error(f"포트폴리오 스냅샷 실패: {e}")


def job_reconciliation():
    """30분마다 포지션 정합성 검사"""
    try:
        reconciliation_service.run_reconciliation(trade_status)
    except Exception as e:
        logger.error(f"리콘실리에이션 실패: {e}")


def enable_trading_morning():
    """매일 장 시작 전 자동매매를 재활성화합니다 (전일 sell_all_at_close에서 비활성화된 것을 복원)."""
    global trading_enabled
    trading_enabled = True
    logger.info("장 시작: 자동매매 활성화")


def refresh_target_symbols_from_condition():
    """거래량 순위 또는 조건검색 사용 시 장중(09:10)에 대상 종목을 한 번 더 갱신합니다."""
    if not settings.USE_VOLUME_RANK and not settings.USE_CONDITION_SEARCH:
        return
    global target_symbols
    try:
        holding = [s for s, st in trade_status.items() if st.get("bought")]
        if settings.USE_VOLUME_RANK:
            dynamic_list = kis_condition.get_top_volume_stocks()
        else:
            dynamic_list = kis_condition.get_target_stocks_by_condition()
        if dynamic_list:
            target_symbols = holding + [s for s in dynamic_list if s not in holding][: settings.CONDITION_SEARCH_MAX]
            for symbol in target_symbols:
                trade_status.setdefault(
                    symbol,
                    {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0},
                )
            source = "거래량 순위" if settings.USE_VOLUME_RANK else "조건검색"
            logger.info(f"{source} 장중 갱신: 대상 종목 {len(target_symbols)} (보유 {len(holding)} + 신규 {len(target_symbols) - len(holding)})")
    except Exception as e:
        logger.error(f"대상 종목 장중 갱신 실패: {e}")


def queue_broadcast(message: dict) -> None:
    """스레드(스케줄러)에서 호출: WebSocket 브로드캐스트를 대기열에 넣습니다."""
    with _broadcast_lock:
        _pending_broadcasts.append(message)


async def price_update_broadcaster():
    """5초마다 상태 브로드캐스트 및 대기 중인 trade_event 전송."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(5)
        try:
            status = await loop.run_in_executor(None, get_status)
            await ws_manager.broadcast({"type": "status_update", "payload": status})
        except Exception as e:
            logger.debug(f"status broadcast 오류: {e}")
        with _broadcast_lock:
            pending = _pending_broadcasts[:]
            _pending_broadcasts.clear()
        for msg in pending:
            try:
                await ws_manager.broadcast(msg)
            except Exception as e:
                logger.debug(f"trade_event broadcast 오류: {e}")


# --- FastAPI Lifespan & App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=session.engine)
    from app.db.migrate import run_migrations
    run_migrations()
    load_trade_status()
    scheduler.add_job(enable_trading_morning, 'cron', day_of_week='mon-fri', hour=8, minute=59, id="enable_trading_job")
    scheduler.add_job(
        run_trading_strategy,
        "cron",
        day_of_week="mon-fri",
        hour="9-15",
        minute="*",
        second="*/10",
        id="trading_job",
        max_instances=2,
    )
    scheduler.add_job(refresh_target_symbols_from_condition, 'cron', day_of_week='mon-fri', hour=9, minute=10, id="condition_refresh_job")
    scheduler.add_job(sell_all_at_close, 'cron', day_of_week='mon-fri', hour=15, minute=19, id="sell_all_job")
    scheduler.add_job(job_portfolio_snapshot, 'cron', minute='*/5', id="portfolio_snapshot_job")
    scheduler.add_job(job_reconciliation, 'cron', minute='0,30', id="reconciliation_job")
    scheduler.start()
    asyncio.create_task(price_update_broadcaster())
    logger.info("고도화된 자동매매 시스템이 시작되었습니다.")
    send_slack_notification("고도화된 자동매매 시스템이 시작되었습니다.")
    yield
    scheduler.shutdown()
    logger.info("자동매매 시스템이 종료되었습니다.")

app = FastAPI(lifespan=lifespan)

# CORS: 프론트엔드(localhost:5173 등)에서 API 호출 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # 접속 시 초기 상태 전송
        initial = get_status()
        await websocket.send_json({"type": "status_update", "payload": initial})
        # ping/pong 하트비트 및 수신 대기
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text("pong")
    except Exception:
        pass
    finally:
        ws_manager.disconnect(websocket)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Advanced Auto Trading System is running."}


@app.get("/api/status")
def get_status():
    """총자산, 예수금, 보유종목, 봇 on/off 상태 반환"""
    assets_error = None
    try:
        cash = kis_order.get_cash_balance()
    except Exception as e:
        logger.error(f"예수금 조회 실패: {e}")
        cash = 0
        assets_error = str(e)
    total_holding = 0.0
    for symbol, st in trade_status.items():
        if st.get("bought"):
            try:
                price = kis_market.get_current_price(symbol)
                total_holding += price * st["quantity"]
            except Exception:
                total_holding += st["purchase_price"] * st["quantity"]
    # 당일 실현손익: 오늘 체결된 매도의 (매도금액 - 원금) 합계
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    today_pl = 0.0
    db = session.SessionLocal()
    try:
        today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        rows = (
            db.query(models.TradeLog)
            .filter(
                models.TradeLog.timestamp >= today_start,
                models.TradeLog.order_type == OrderType.SELL,
                models.TradeLog.status == OrderStatus.EXECUTED,
            )
            .all()
        )
        today_pl = sum(r.realized_pl or 0.0 for r in rows)
    except Exception as e:
        logger.debug(f"당일 손익 집계 스킵: {e}")
    finally:
        db.close()
    # 총 자산 = 현금(예수금) + 보유 주식 평가액 (매수 시 현금 ↓ 보유 ↑, 합계는 동일 유지)
    total_assets = cash + total_holding
    return_rate = (today_pl / total_assets * 100) if total_assets else 0.0
    positions_detail = portfolio_service.calculate_unrealized_pl(trade_status)
    return {
        "totalAssets": round(total_assets, 0),
        "cashBalance": round(cash, 0),
        "holdingsValue": round(total_holding, 0),
        "todayRealizedPL": today_pl,
        "returnRate": round(return_rate, 2),
        "positions": trade_status,
        "positionsDetail": positions_detail,
        "tradingEnabled": trading_enabled,
        "targetSymbols": target_symbols,
        "assetsError": assets_error,
    }


@app.get("/api/decisions")
def get_decisions(symbol: str = "", limit: int = 50):
    """의사결정 로그를 조회합니다."""
    db = session.SessionLocal()
    try:
        query = db.query(models.DecisionLog).order_by(models.DecisionLog.timestamp.desc()).limit(limit)
        if symbol:
            query = query.filter(models.DecisionLog.symbol == symbol)
        rows = query.all()
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "symbol": r.symbol,
                "strategy_name": r.strategy_name,
                "signal": r.signal,
                "decision_reason": r.decision_reason,
                "indicator_values": json.loads(r.indicator_values) if r.indicator_values else {},
                "current_price": r.current_price,
                "action_taken": r.action_taken,
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/api/trades")
def get_trades(limit: int = 50):
    """최근 매매 로그 (DB)"""
    db = session.SessionLocal()
    try:
        logs = (
            db.query(models.TradeLog)
            .order_by(models.TradeLog.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": l.id,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                "symbol": l.symbol,
                "orderType": l.order_type.value if l.order_type else None,
                "price": l.price,
                "quantity": l.quantity,
                "status": l.status.value if l.status else None,
            }
            for l in logs
        ]
    finally:
        db.close()


@app.post("/api/bot/start")
def bot_start():
    """자동매매 봇 활성화"""
    global trading_enabled
    trading_enabled = True
    return {"success": True, "tradingEnabled": True}


@app.post("/api/bot/stop")
def bot_stop():
    """자동매매 봇 일시 정지"""
    global trading_enabled
    trading_enabled = False
    return {"success": True, "tradingEnabled": False}


@app.post("/api/panic-sell")
def api_panic_sell():
    """전량 매도 (Panic Sell)"""
    sell_all_at_close()
    return {"success": True, "message": "전량 매도 주문을 실행했습니다."}


class SellRequest(BaseModel):
    symbol: str
    quantity: int | None = None  # 없거나 0이면 전량 매도


class BuyRequest(BaseModel):
    symbol: str                    # 종목코드 (예: "005930")
    quantity: int | None = None    # 수량 직접 입력 (None이면 amount 사용)
    amount: int | None = None      # 금액 입력 (원 단위, quantity 미지정 시 사용)


@app.post("/api/sell")
def api_sell(body: SellRequest):
    """개별 종목 매도 (전량 또는 지정 수량)"""
    result = sell_symbol(body.symbol, body.quantity)
    return result


@app.post("/api/buy")
def api_buy(body: BuyRequest):
    """수동 매수 (시장가). 매수 후 자동 매도 관리(익절/손절/트레일링스탑) 적용."""
    result = buy_symbol(body.symbol, body.quantity, body.amount)
    return result


@app.post("/api/sync-positions")
def api_sync_positions():
    """KIS 실제 잔고 기준으로 보유 목록 동기화 (잔고 없는 종목은 보유에서 제거)"""
    cleared = reconciliation_service.sync_positions_from_kis(trade_status)
    return {
        "success": True,
        "cleared": cleared,
        "message": f"동기화 완료. 보유 해제된 종목: {len(cleared)}건" + (f" ({', '.join(cleared)})" if cleared else ""),
    }


# 라우터 등록 (전략/포트폴리오 API)
from app.routers import strategies as strategies_router
from app.routers import portfolio as portfolio_router
app.include_router(strategies_router.router, prefix="/api")
app.include_router(portfolio_router.router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
