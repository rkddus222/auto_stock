import json
from pathlib import Path

from app.api import kis_order
from app.core.config import settings
from app.core.logger import logger
from app.core.slack import send_slack_notification

TRADE_STATUS_FILE: Path = settings.base_dir / "trade_status.json"

_EMPTY_POSITION = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}


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
    for symbol, st in list(trade_status.items()):
        if not st.get("bought"):
            continue
        kis_qty = kis_map.get(symbol, 0)
        if kis_qty <= 0:
            trade_status[symbol] = {**_EMPTY_POSITION}
            cleared.append(symbol)
            logger.info(f"[동기화] {symbol} KIS 잔고 없음 → 보유 해제")
    if cleared:
        TRADE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(trade_status, f, indent=4, ensure_ascii=False)
    return cleared


def run_reconciliation(trade_status: dict):
    """trade_status vs KIS get_balance() 비교, 불일치 시 로컬을 KIS 기준으로 정리 후 알림"""
    kis_map = _get_kis_balance_map()
    if kis_map is None:
        return

    # KIS 잔고 0인데 로컬에 보유로 되어 있으면 보유 해제 후 저장
    cleared = sync_positions_from_kis(trade_status, kis_map)
    if cleared:
        logger.info(f"리콘실리에이션: KIS 잔고 없음으로 보유 해제된 종목: {cleared}")

    # 로컬 상태를 {symbol: quantity} 맵으로 변환 (동기화 반영 후)
    local_map: dict[str, int] = {}
    for symbol, st in trade_status.items():
        if st.get("bought") and st.get("quantity", 0) > 0:
            local_map[symbol] = st["quantity"]

    # 수량 불일치 (둘 다 보유 중인데 수량이 다른 경우) 알림
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
        msg_lines = ["[리콘실리에이션] 포지션 불일치 감지:"]
        for m in mismatches:
            msg_lines.append(f"  {m['symbol']}: KIS={m['kis_quantity']}주 vs 로컬={m['local_quantity']}주")
        msg = "\n".join(msg_lines)
        logger.warning(msg)
        send_slack_notification(msg)
    else:
        logger.info("리콘실리에이션 완료: 포지션 일치")
