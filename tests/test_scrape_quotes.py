#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-тесты парсера: сеть не нужна, используется локальная HTML-фикстура."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrape_quotes as sq  # noqa: E402


def fixture() -> str:
    return Path(__file__).with_name("fixture_page.html").read_text(encoding="utf-8")


class ExtractRowsTest(unittest.TestCase):
    def test_extracts_complete_blocks(self) -> None:
        rows = sq.extract_rows(fixture())
        self.assertEqual(len(rows), 2)  # битый блок без автора пропущен
        self.assertEqual(rows[0].author, "Автор Первый")
        self.assertIn("Первая тестовая цитата", rows[0].quote)

    def test_joins_tags(self) -> None:
        rows = sq.extract_rows(fixture())
        self.assertEqual(rows[0].tags, "python; тест")
        self.assertEqual(rows[1].tags, "парсинг")

    def test_empty_page_gives_no_rows(self) -> None:
        self.assertEqual(sq.extract_rows("<html><body></body></html>"), [])


class NextPageTest(unittest.TestCase):
    def test_finds_absolute_next_url(self) -> None:
        url = sq.next_page_url(fixture(), "https://quotes.toscrape.com/")
        self.assertEqual(url, "https://quotes.toscrape.com/page/2/")

    def test_no_next_link(self) -> None:
        self.assertIsNone(sq.next_page_url("<html><body></body></html>", "https://example.com/"))


class WriteCsvTest(unittest.TestCase):
    def test_csv_has_header_and_rows_utf8(self) -> None:
        rows = sq.extract_rows(fixture())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            sq.write_csv(rows, str(path))
            with path.open(encoding="utf-8-sig", newline="") as fh:
                data = list(csv.DictReader(fh))
        self.assertEqual(len(data), 2)
        self.assertEqual(list(data[0].keys()), sq.FIELDS)
        self.assertEqual(data[1]["author"], "Автор Второй")


if __name__ == "__main__":
    unittest.main(verbosity=2)
