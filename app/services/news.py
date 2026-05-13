"""네이버 금융 뉴스 수집 — LLM 어드바이저에 뉴스 컨텍스트를 제공한다.

네이버 증권 모바일 API를 사용하여 종목별 최신 뉴스를 JSON으로 가져온다.
Selenium 없이 requests만으로 동작하며, 종목별 캐시(TTL)로 반복 호출 부담을 줄인다.
"""

import time
import requests
import urllib3
from app.core.logger import logger

# SSL 인증 fallback 시 경고 억제
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 종목별 뉴스 캐시: {symbol: {"ts": float, "news": list[dict]}}
_news_cache: dict[str, dict] = {}
_NEWS_CACHE_TTL: float = 300.0  # 5분

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
_REQUEST_TIMEOUT = 5  # 초
_NAVER_MOBILE_NEWS_API = "https://m.stock.naver.com/api/news/stock/{symbol}?pageSize={size}&page=1"


def fetch_stock_news(symbol: str, max_count: int = 5) -> list[dict]:
    """
    네이버 증권 모바일 API에서 종목 관련 최신 뉴스를 가져온다.

    Args:
        symbol: 종목 코드 (예: "005930")
        max_count: 최대 수집 건수

    Returns:
        [{"title": "...", "summary": "...", "date": "...", "source": "..."}, ...]
    """
    # 캐시 확인
    cached = _news_cache.get(symbol)
    if cached and (time.time() - cached["ts"]) < _NEWS_CACHE_TTL:
        return cached["news"][:max_count]

    articles: list[dict] = []
    try:
        articles = _fetch_from_mobile_api(symbol, max_count)
    except Exception as e:
        logger.debug(f"[뉴스] 네이버 모바일 API 뉴스 수집 실패 ({symbol}): {e}")

    # 캐시 저장 (빈 결과도 캐시하여 반복 실패 방지)
    _news_cache[symbol] = {"ts": time.time(), "news": articles}
    return articles[:max_count]


def _format_datetime(dt_str: str) -> str:
    """'202603170942' → '2026.03.17 09:42' 형태로 변환."""
    if not dt_str or len(dt_str) < 12:
        return dt_str or ""
    return f"{dt_str[:4]}.{dt_str[4:6]}.{dt_str[6:8]} {dt_str[8:10]}:{dt_str[10:12]}"


def _fetch_from_mobile_api(symbol: str, max_count: int) -> list[dict]:
    """네이버 증권 모바일 API로 종목 뉴스를 가져온다."""
    url = _NAVER_MOBILE_NEWS_API.format(symbol=symbol, size=max_count)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
    except requests.exceptions.SSLError:
        # SSL 인증 문제 시 verify=False fallback (사내망/WSL 등)
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT, verify=False)
    resp.raise_for_status()
    data = resp.json()

    articles = []
    # API 응답: list of {total, items: [{title, body, datetime, officeName, ...}]}
    if isinstance(data, list):
        for cluster in data:
            for item in cluster.get("items", []):
                if len(articles) >= max_count:
                    break
                title = (item.get("titleFull") or item.get("title") or "").strip()
                body = (item.get("body") or "").strip()
                # body가 길면 앞부분만 요약으로 사용
                summary = body[:100] + "..." if len(body) > 100 else body
                date_str = _format_datetime(item.get("datetime", ""))
                source = item.get("officeName", "")
                if title:
                    articles.append({
                        "title": title,
                        "summary": summary,
                        "date": date_str,
                        "source": source,
                    })
            if len(articles) >= max_count:
                break

    return articles


def format_news_for_prompt(news_list: list[dict]) -> str:
    """뉴스 리스트를 LLM 프롬프트용 텍스트로 포맷한다."""
    if not news_list:
        return "최근 뉴스 없음"
    lines = []
    for i, article in enumerate(news_list, 1):
        date_str = f" ({article['date']})" if article.get("date") else ""
        source_str = f" [{article['source']}]" if article.get("source") else ""
        line = f"  {i}. {article['title']}{source_str}{date_str}"
        if article.get("summary"):
            line += f"\n     → {article['summary']}"
        lines.append(line)
    return "\n".join(lines)
