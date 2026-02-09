
import requests
from app.core.config import settings
from app.core.logger import logger

def send_slack_notification(message: str):
    """Slack으로 메시지를 전송합니다."""
    try:
        payload = {"text": message}
        response = requests.post(settings.SLACK_WEBHOOK_URL, json=payload)
        if response.status_code != 200:
            logger.warning(f"Slack 알림 전송 실패: {response.text}")
    except Exception as e:
        logger.error(f"Slack 알림 전송 중 에러 발생: {e}")

