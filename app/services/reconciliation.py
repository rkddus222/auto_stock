import json
from pathlib import Path

from app.api import kis_order
from app.core.config import settings
from app.core.logger import logger
from app.core.slack import send_slack_notification

TRADE_STATUS_FILE: Path = settings.base_dir / "trade_status.json"


def run_reconciliation(trade_status: dict):
    """trade_status.json vs KIS get_balance() 비교, 불일치 시 알림"""
    try:
        kis_holdings = kis_order.get_balance()
    except Exception as e:
        logger.error(f"리콘실리에이션 - 잔고 조회 실패: {e}")
        return

    # KIS 잔고를 {symbol: quantity} 맵으로 변환
    kis_map: dict[str, int] = {}
    if kis_holdings:
        for item in kis_holdings:
            sym = item.get("pdno", "")
            qty = int(item.get("hldg_qty", 0))
            if sym and qty > 0:
                kis_map[sym] = qty

    # 로컬 상태를 {symbol: quantity} 맵으로 변환
    local_map: dict[str, int] = {}
    for symbol, st in trade_status.items():
        if st.get("bought") and st.get("quantity", 0) > 0:
            local_map[symbol] = st["quantity"]

    # 비교
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
