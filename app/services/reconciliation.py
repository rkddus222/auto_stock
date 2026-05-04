import json
from pathlib import Path

from app.api import kis_order, kis_market
from app.core.config import settings
from app.core.logger import logger
from app.core.slack import send_slack_notification
from app.core.trade_state_lock import trade_state_lock
from app.services import indicators as indicators_service

TRADE_STATUS_FILE: Path = settings.base_dir / "trade_status.json"


def _atomic_write_trade_status(trade_status: dict) -> None:
    """trade_status.json 쓰기 — 공용 lock 보유 + 파일 mkdir."""
    with trade_state_lock:
        TRADE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(trade_status, f, indent=4, ensure_ascii=False)

_EMPTY_POSITION = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}


def get_balance_position(symbol: str) -> dict | None:
    """KIS 잔고 조회 결과에서 특정 종목 행을 반환합니다."""
    kis_holdings = kis_order.get_balance()
    if not kis_holdings:
        return None
    for item in kis_holdings:
        if item.get("pdno") == symbol:
            return item
    return None


def extract_average_price(position: dict | None) -> float | None:
    """잔고 항목에서 평균 매입단가를 추출합니다."""
    if not position:
        return None
    for key in ("pchs_avg_pric", "pchs_avg_pric1", "pchs_unpr", "avg_prvs", "buy_amt_avg"):
        val = position.get(key)
        if val is None or str(val).strip() == "":
            continue
        try:
            price = float(val)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def _get_kis_balance_map() -> dict[str, int] | None:
    """KIS 잔고를 {symbol: quantity} 맵으로 반환. 실패 시 None."""
    try:
        kis_holdings = kis_order.get_balance()
    except Exception as e:
        logger.error(f"잔고 조회 실패: {e}")
        return None
    kis_map: dict[str, int] = {}
    if kis_holdings:
        for item in kis_holdings:
            sym = item.get("pdno", "")
            qty = int(item.get("hldg_qty", 0))
            if sym and qty > 0:
                kis_map[sym] = qty
    return kis_map


def sync_positions_from_kis(trade_status: dict, kis_map: dict[str, int] | None = None) -> list[str]:
    """
    KIS 실제 잔고 기준으로 trade_status를 동기화합니다.
    KIS에 잔고가 0인데 로컬에 보유로 되어 있으면 보유 해제하고 파일 저장.
    :param kis_map: None이면 내부에서 get_balance() 호출
    :return: 보유 해제된 종목 코드 목록
    """
    if kis_map is None:
        kis_map = _get_kis_balance_map() or {}
    cleared: list[str] = []
    with trade_state_lock:
        for symbol, st in list(trade_status.items()):
            if not st.get("bought"):
                continue
            kis_qty = kis_map.get(symbol, 0)
            if kis_qty <= 0:
                trade_status[symbol] = {**_EMPTY_POSITION}
                cleared.append(symbol)
                logger.info(f"[동기화] {symbol} KIS 잔고 없음 → 보유 해제")
        if cleared:
            _atomic_write_trade_status(trade_status)
    return cleared


def run_reconciliation(trade_status: dict):
    """trade_status vs KIS get_balance() 비교, 불일치 시 로컬을 KIS 기준으로 정리 후 알림"""
    kis_map = _get_kis_balance_map()
    if kis_map is None:
        return

    # KIS 잔고 0인데 로컬에 보유로 되어 있으면 보유 해제 후 저장
    # (sync_positions_from_kis 내부에서 lock 보유)
    cleared = sync_positions_from_kis(trade_status, kis_map)
    if cleared:
        logger.info(f"리콘실리에이션: KIS 잔고 없음으로 보유 해제된 종목: {cleared}")

    # 이하 mutate/write는 매매 루프와 충돌하지 않도록 단일 lock 안에서 일괄 처리
    with trade_state_lock:
        # 로컬 상태를 {symbol: quantity} 맵으로 변환 (동기화 반영 후)
        local_map: dict[str, int] = {}
        for symbol, st in trade_status.items():
            if st.get("bought") and st.get("quantity", 0) > 0:
                local_map[symbol] = st["quantity"]

        # KIS에만 있는 고아 포지션 자동 추가
        adopted = []
        for sym, kis_qty in kis_map.items():
            if kis_qty > 0 and sym not in local_map:
                try:
                    position = get_balance_position(sym)
                    current_price = kis_market.get_current_price(sym)
                    purchase_price = extract_average_price(position) or current_price
                    atr_mult = getattr(settings, "ATR_MULTIPLIER", 1.5)
                    atr_val = indicators_service.get_atr(sym)
                    if atr_val and atr_val > 0:
                        stop_price = purchase_price - atr_val * atr_mult
                    else:
                        stop_price = purchase_price * 0.95  # ATR 실패 시 -5% 기본값
                    entry = {
                        "bought": True,
                        "purchase_price": purchase_price,
                        "quantity": kis_qty,
                        "initial_quantity": kis_qty,
                        "high_price": max(current_price, purchase_price),
                        "stop_price": stop_price,
                        "stage1_sell_done": False,
                        "stage2_sell_done": False,
                        "adopted_from_kis": True,
                    }
                    if atr_val and atr_val > 0:
                        entry["atr"] = atr_val
                    trade_status[sym] = entry
                    adopted.append(sym)
                    logger.info(
                        f"[고아 포지션 추가] {sym} ({kis_qty}주) | 평균단가: {purchase_price:.0f}, "
                        f"현재가: {current_price:.0f}, 손절가: {stop_price:.0f}"
                    )
                except Exception as e:
                    logger.error(f"[고아 포지션] {sym} 추가 실패: {e}")
        if adopted:
            _atomic_write_trade_status(trade_status)
            send_slack_notification(f"[리콘실리에이션] 고아 포지션 {len(adopted)}건 자동 추가: {', '.join(adopted)}")
            # local_map 갱신 (아래 불일치 검사에 반영)
            for sym in adopted:
                local_map[sym] = trade_status[sym]["quantity"]

        # 수량 불일치 (둘 다 보유 중인데 수량이 다른 경우) → KIS 기준으로 강제 정합
        # KIS 잔고를 단일 소스로 취급해야 매도 시 "주문가능수량 초과" 오류가 재발하지 않음
        all_symbols = set(kis_map.keys()) | set(local_map.keys())
        mismatches = []
        for sym in all_symbols:
            kis_qty = kis_map.get(sym, 0)
            local_qty = local_map.get(sym, 0)
            if kis_qty != local_qty:
                mismatches.append({
                    "symbol": sym,
                    "kis_quantity": kis_qty,
                    "local_quantity": local_qty,
                })

        if mismatches:
            adjusted = []
            for m in mismatches:
                sym = m["symbol"]
                kis_qty = m["kis_quantity"]
                st = trade_status.get(sym)
                # bought=True이고 KIS도 보유 중인 케이스만 quantity 강제 동기화
                # (KIS=0인 케이스는 위쪽 sync_positions_from_kis에서 이미 처리, 고아 케이스는 자동 추가에서 처리)
                if st and st.get("bought") and kis_qty > 0:
                    st["quantity"] = kis_qty
                    # initial_quantity도 위로 늘어난 경우엔 함께 상향(분할 익절 비율 보존)
                    if kis_qty > st.get("initial_quantity", 0):
                        st["initial_quantity"] = kis_qty
                    adjusted.append(sym)
            if adjusted:
                _atomic_write_trade_status(trade_status)

            msg_lines = ["[리콘실리에이션] 포지션 불일치 감지 → KIS 기준 정합:"]
            for m in mismatches:
                tag = "→ 동기화" if m["symbol"] in adjusted else "(보류)"
                msg_lines.append(f"  {m['symbol']}: KIS={m['kis_quantity']}주 vs 로컬={m['local_quantity']}주 {tag}")
            msg = "\n".join(msg_lines)
            logger.warning(msg)
            send_slack_notification(msg)
        else:
            logger.info("리콘실리에이션 완료: 포지션 일치")
