#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Парсер каталога сайта → CSV или Excel.

Демонстрационный проект: собирает цитаты с https://quotes.toscrape.com —
публичной песочницы, созданной специально для отработки скрапинга.
Под другой сайт меняются две функции: extract_rows() и next_page_url().

Запуск:
    python3 scrape_quotes.py --pages 3 --out quotes.csv
    python3 scrape_quotes.py --pages 5 --out quotes.xlsx --delay 1.0

Принципы:
  * уважаем robots.txt (--ignore-robots отключает проверку);
  * пауза между запросами (--delay, по умолчанию 1 секунда);
  * повторы с экспоненциальной паузой на сетевые ошибки;
  * коды возврата: 0 — успех, 2 — ошибка (удобно для cron/планировщика).
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("parser")

DEFAULT_URL = "https://quotes.toscrape.com/"
HEADERS = {"User-Agent": "parser-demo/1.0 (+educational scraping demo)"}
FIELDS = ["quote", "author", "tags"]


@dataclass
class Row:
    """Одна запись результата. Поля = колонки CSV/XLSX."""

    quote: str
    author: str
    tags: str


def robots_allows(url: str, session: requests.Session, user_agent: str = "*") -> bool:
    """Проверяет robots.txt перед обходом. Нет файла / ошибка сети — обход разрешён."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = session.get(robots_url, headers=HEADERS, timeout=10)
        if resp.status_code >= 400:
            return True
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
    except requests.RequestException:
        return True
    return parser.can_fetch(user_agent, url)


def fetch(url: str, session: requests.Session, retries: int = 3, timeout: int = 20) -> str:
    """GET с повторами. Бросает RuntimeError, если все попытки исчерпаны."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:  # сеть/5xx/таймаут — пробуем ещё
            last_exc = exc
            pause = min(2 ** attempt, 8)
            LOG.warning("попытка %d/%d не удалась (%s), пауза %ds", attempt, retries, exc.__class__.__name__, pause)
            time.sleep(pause)
    raise RuntimeError(f"не удалось скачать {url}: {last_exc}")


def extract_rows(html: str) -> list[Row]:
    """Разбирает одну страницу. Селекторы — под конкретный сайт."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[Row] = []
    for block in soup.select("div.quote"):
        text_el = block.select_one("span.text")
        author_el = block.select_one("small.author")
        if not text_el or not author_el:
            continue  # неполный блок пропускаем, а не падаем
        tags = [tag.get_text(strip=True) for tag in block.select("a.tag")]
        rows.append(
            Row(
                quote=text_el.get_text(strip=True),
                author=author_el.get_text(strip=True),
                tags="; ".join(tags),
            )
        )
    return rows


def next_page_url(html: str, current_url: str) -> str | None:
    """Ищет ссылку «следующая страница» и приводит её к абсолютному виду."""
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("li.next > a")
    if not link or not link.get("href"):
        return None
    return urljoin(current_url, link["href"])


def crawl(start_url: str, pages: int, delay: float, check_robots: bool = True) -> list[Row]:
    """Обходит до `pages` страниц пагинации, возвращает все найденные записи."""
    session = requests.Session()
    url: str | None = start_url
    rows: list[Row] = []
    visited: set[str] = set()
    for page_no in range(1, pages + 1):
        if not url:
            break
        if check_robots and not robots_allows(url, session):
            raise RuntimeError(f"robots.txt запрещает обход {url}")
        LOG.info("страница %d: %s", page_no, url)
        html = fetch(url, session)
        page_rows = extract_rows(html)
        LOG.info("  записей: %d", len(page_rows))
        rows.extend(page_rows)
        visited.add(url)
        candidate = next_page_url(html, url)
        url = candidate if candidate and candidate not in visited else None
        if url:
            time.sleep(delay)
    return rows


def write_csv(rows: list[Row], path: str) -> None:
    """CSV в UTF-8 с BOM — открывается в Excel без пляски с кодировкой."""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_xlsx(rows: list[Row], path: str) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # подсказка, а не стектрейс
        raise RuntimeError("для xlsx установите openpyxl: pip3 install --user openpyxl") from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(FIELDS)
    for row in rows:
        sheet.append([getattr(row, field) for field in FIELDS])
    workbook.save(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Парсер каталога в CSV/XLSX (демо).")
    parser.add_argument("--url", default=DEFAULT_URL, help="первая страница выборки")
    parser.add_argument("--pages", type=int, default=1, help="максимум страниц (по умолчанию 1)")
    parser.add_argument("--out", default="data.csv", help="файл результата")
    parser.add_argument("--format", choices=["csv", "xlsx"], default=None, help="по умолчанию — по расширению --out")
    parser.add_argument("--delay", type=float, default=1.0, help="пауза между страницами, сек")
    parser.add_argument("--ignore-robots", action="store_true", help="не проверять robots.txt")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    fmt = args.format or ("xlsx" if args.out.lower().endswith(".xlsx") else "csv")

    try:
        rows = crawl(args.url, args.pages, args.delay, check_robots=not args.ignore_robots)
    except Exception as exc:
        LOG.error("обход не удался: %s", exc)
        return 2

    if not rows:
        LOG.error("записей не найдено — проверьте селекторы extract_rows() под ваш сайт")
        return 2

    try:
        write_xlsx(rows, args.out) if fmt == "xlsx" else write_csv(rows, args.out)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 2

    LOG.info("готово: %d строк(и) → %s", len(rows), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
