"""Latest news for a ticker (Yahoo Finance + Google News RSS) and sentiment scoring."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus


@dataclass
class Article:
    title: str
    source: str
    url: str
    published: datetime | None
    sentiment: float = 0.0   # VADER compound, -1..+1


def _from_yahoo(ticker: str) -> list[Article]:
    import yfinance as yf
    out = []
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        return out
    for it in items:
        c = it.get("content", it)  # yfinance changed its news format; handle both
        title = c.get("title")
        if not title:
            continue
        url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link", "")
        src = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher", "Yahoo")
        pub = None
        if c.get("pubDate"):
            try:
                pub = datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00"))
            except ValueError:
                pass
        elif c.get("providerPublishTime"):
            pub = datetime.fromtimestamp(c["providerPublishTime"], tz=timezone.utc)
        out.append(Article(title, src or "Yahoo", url or "", pub))
    return out


def _from_google(ticker: str, company: str | None) -> list[Article]:
    import feedparser
    q = quote_plus(f'"{company}" OR {ticker} stock' if company else f"{ticker} stock")
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
    out = []
    for e in feed.entries[:30]:
        pub = None
        try:
            pub = parsedate_to_datetime(e.published)
        except Exception:
            pass
        src = getattr(getattr(e, "source", None), "title", None) or "Google News"
        out.append(Article(e.title, src, e.link, pub))
    return out


_vader = None


def _score(text: str) -> float:
    global _vader
    if _vader is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader = SentimentIntensityAnalyzer()
    return _vader.polarity_scores(text)["compound"]


def fetch(ticker: str, company: str | None = None, max_articles: int = 12, max_age_days: int = 7) -> list[Article]:
    arts = _from_yahoo(ticker) + _from_google(ticker, company)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    seen, out = set(), []
    for a in arts:
        k = a.title.lower()[:80]
        if k in seen or (a.published and a.published < cutoff):
            continue
        seen.add(k)
        a.sentiment = _score(a.title)
        out.append(a)
    out.sort(key=lambda a: a.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out[:max_articles]


def aggregate_sentiment(articles: list[Article], half_life_days: float = 2.0) -> float:
    """Recency-weighted average sentiment; newer headlines count more."""
    if not articles:
        return 0.0
    now = datetime.now(timezone.utc)
    num = den = 0.0
    for a in articles:
        age = (now - a.published).total_seconds() / 86400 if a.published else max(half_life_days, 1)
        w = math.exp(-math.log(2) * max(age, 0) / half_life_days)
        num += w * a.sentiment
        den += w
    return num / den if den else 0.0
