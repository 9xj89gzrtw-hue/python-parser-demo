#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мониторинг цен: сравнивает текущую выборку с прошлым снимком и сообщает об изменениях.

Демо: цены книг с https://books.toscrape.com — публичной песочницы для скрапинга.
Под свой сайт меняются только селекторы в parse_books().

Запуск:
    python3 watch_books.py --out snapshot.json              # создать/обновить снимок
    python3 watch_books.py --out snapshot.json --notify     # + отчёт в Telegram
    python3 watch_books.py --out snapshot.json --pages 3    # обойти 3 страницы каталога

Telegram: токен бота и chat_id берутся из переменных окружения
TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID (или из ключей --telegram-token/--telegram-chat).

Расписание (пример для cron, раз в день в 9:00):
    0 9 * * * cd /path/to/project && python3 watch_books.py --out snapshot.json --notify
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_quotes as sq  # noqa: E402  (переиспользуем fetch/robots/pagination)

LOG = logging.getLogger("watch")
DEFAULT_URL = "https://books.toscrape.com/"


@dataclass
class Change:
    title: str
    kind: str  # new | gone | cheaper | pricier
    old: float | None
    new: float | None

    @property
    def delta_pct(self) -> float | None:
        if self.old and self.new:
            return (self.new - self.old) / self.old * 100.0
        return None


def parse_books(html: str) -> Dict[str, float]:
    """HTML страницы → {название: цена}. Селекторы под конкретный сайт."""
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, float] = {}
    for card in soup.select("article.product_pod"):
        title_el = card.select_one("h3 a")
        price_el = card.select_one("p.price_color")
        if not title_el or not price_el:
            continue
        title = (title_el.get("title") or title_el.get_text(strip=True)).strip()
        digits = "".join(ch for ch in price_el.get_text() if ch.isdigit() or ch in ".,")
        digits = digits.replace(",", ".")
        try:
            price = float(digits)
        except ValueError:
            continue
        out[title] = price
    return out


def crawl_prices(url: str, pages: int, delay: float = 1.0) -> Dict[str, float]:
    """Обходит страницы каталога, возвращает {название: цена}."""
    session = requests.Session()
    prices: Dict[str, float] = {}
    visited: set[str] = set()
    for page_no in range(1, pages + 1):
        if not url:
            break
        if not sq.robots_allows(url, session):
            raise RuntimeError(f"robots.txt запрещает обход {url}")
        LOG.info("страница %d: %s", page_no, url)
        html = sq.fetch(url, session)
        page_prices = parse_books(html)
        LOG.info("  позиций: %d", len(page_prices))
        prices.update(page_prices)
        visited.add(url)
        candidate = sq.next_page_url(html, url)
        url = candidate if candidate and candidate not in visited else None
        if url:
            time.sleep(delay)
    return prices


def diff_prices(old: Dict[str, float], new: Dict[str, float], threshold_pct: float = 0.0) -> List[Change]:
    """Сравнивает два снимка. threshold_pct — игнорировать изменения меньше X%."""
    changes: List[Change] = []
    for title, new_price in new.items():
        if title not in old:
            changes.append(Change(title, "new", None, new_price))
            continue
        old_price = old[title]
        if old_price == new_price:
            continue
        pct = abs((new_price - old_price) / old_price * 100.0) if old_price else 100.0
        if pct < threshold_pct:
            continue
        kind = "cheaper" if new_price < old_price else "pricier"
        changes.append(Change(title, kind, old_price, new_price))
    for title, old_price in old.items():
        if title not in new:
            changes.append(Change(title, "gone", old_price, None))
    return changes


def format_report(changes: List[Change]) -> str:
    if not changes:
        return "Изменений цен не найдено."
    lines = [f"Изменения цен: {len(changes)}"]
    for ch in changes:
        if ch.kind == "new":
            lines.append(f"+ новый товар: {ch.title} — {ch.new:.2f}")
        elif ch.kind == "gone":
            lines.append(f"- пропал из каталога: {ch.title}")
        elif ch.kind == "cheaper":
            lines.append(f"↓ подешевело: {ch.title} — {ch.old:.2f} → {ch.new:.2f} ({ch.delta_pct:+.1f}%)")
        else:
            lines.append(f"↑ подорожало: {ch.title} — {ch.old:.2f} → {ch.new:.2f} ({ch.delta_pct:+.1f}%)")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Отправляет сообщение в Telegram. Возвращает True при успехе."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
        ok = resp.status_code == 200 and resp.json().get("ok", False)
        if not ok:
            LOG.warning("Telegram ответил: %s %s", resp.status_code, resp.text[:200])
        return bool(ok)
    except requests.RequestException as exc:
        LOG.warning("не удалось отправить в Telegram: %s", exc)
        return False


def load_snapshot(path: Path) -> Dict[str, float]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("снимок повреждён (%s), считаю его пустым", exc)
        return {}


def save_snapshot(path: Path, prices: Dict[str, float]) -> None:
    path.write_text(json.dumps(prices, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Мониторинг цен с отчётом в Telegram (демо).")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--out", default="snapshot.json", help="файл снимка (создаётся/обновляется)")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--threshold", type=float, default=0.0, help="игнорировать изменения меньше X%%")
    parser.add_argument("--notify", action="store_true", help="отправить отчёт в Telegram")
    parser.add_argument("--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--telegram-chat", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    path = Path(args.out)
    old = load_snapshot(path)
    try:
        new = crawl_prices(args.url, args.pages, args.delay)
    except Exception as exc:  # noqa: BLE001
        LOG.error("обход не удался: %s", exc)
        return 2
    if not new:
        LOG.error("позиций не найдено — проверьте селекторы parse_books()")
        return 2

    changes = diff_prices(old, new, args.threshold)
    report = format_report(changes)
    print(report)
    save_snapshot(path, new)
    LOG.info("снимок обновлён: %s (%d позиций)", path, len(new))

    if args.notify:
        if not args.telegram_token or not args.telegram_chat:
            LOG.error("для --notify нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
            return 2
        if send_telegram(args.telegram_token, args.telegram_chat, report):
            LOG.info("отчёт отправлен в Telegram")
        else:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
