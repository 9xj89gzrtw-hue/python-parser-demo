#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-тесты мониторинга цен: сеть не нужна (локальная фикстура + моки)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watch_books as wb  # noqa: E402


def fixture() -> str:
    return Path(__file__).with_name("fixture_books.html").read_text(encoding="utf-8")


class ParseBooksTest(unittest.TestCase):
    def test_parses_titles_and_prices(self) -> None:
        prices = wb.parse_books(fixture())
        self.assertEqual(len(prices), 3)
        self.assertAlmostEqual(prices["A Light in the Attic"], 51.77)
        self.assertAlmostEqual(prices["Soumission"], 50.10)

    def test_empty_page(self) -> None:
        self.assertEqual(wb.parse_books("<html><body></body></html>"), {})


class DiffTest(unittest.TestCase):
    def test_detects_cheaper_and_pricier(self) -> None:
        old = {"A": 100.0, "B": 50.0}
        new = {"A": 90.0, "B": 55.0}
        changes = {c.title: c for c in wb.diff_prices(old, new)}
        self.assertEqual(changes["A"].kind, "cheaper")
        self.assertAlmostEqual(changes["A"].delta_pct, -10.0)
        self.assertEqual(changes["B"].kind, "pricier")
        self.assertAlmostEqual(changes["B"].delta_pct, 10.0)

    def test_detects_new_and_gone(self) -> None:
        changes = {c.title: c for c in wb.diff_prices({"A": 10.0}, {"B": 20.0})}
        self.assertEqual(changes["B"].kind, "new")
        self.assertEqual(changes["A"].kind, "gone")

    def test_no_changes(self) -> None:
        self.assertEqual(wb.diff_prices({"A": 10.0}, {"A": 10.0}), [])

    def test_threshold_filters_small_moves(self) -> None:
        changes = wb.diff_prices({"A": 100.0}, {"A": 100.5}, threshold_pct=1.0)
        self.assertEqual(changes, [])
        changes = wb.diff_prices({"A": 100.0}, {"A": 102.0}, threshold_pct=1.0)
        self.assertEqual(len(changes), 1)


class ReportTest(unittest.TestCase):
    def test_report_contains_arrow_and_percent(self) -> None:
        report = wb.format_report(wb.diff_prices({"A": 100.0}, {"A": 90.0}))
        self.assertIn("подешевело", report)
        self.assertIn("-10.0%", report)

    def test_empty_report(self) -> None:
        self.assertIn("не найдено", wb.format_report([]))


class SnapshotTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.json"
            wb.save_snapshot(path, {"A": 1.5})
            self.assertEqual(wb.load_snapshot(path), {"A": 1.5})
            self.assertEqual(wb.load_snapshot(Path(tmp) / "missing.json"), {})

    def test_broken_snapshot_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.json"
            path.write_text("{не json", encoding="utf-8")
            self.assertEqual(wb.load_snapshot(path), {})


class TelegramTest(unittest.TestCase):
    def test_send_ok(self) -> None:
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"ok": True}
        with mock.patch.object(wb.requests, "post", return_value=fake) as post:
            self.assertTrue(wb.send_telegram("token", "42", "привет"))
        self.assertIn("api.telegram.org/bottoken/sendMessage", post.call_args[0][0])

    def test_send_failure(self) -> None:
        fake = mock.Mock(status_code=400, text="bad request")
        fake.json.return_value = {"ok": False}
        with mock.patch.object(wb.requests, "post", return_value=fake):
            self.assertFalse(wb.send_telegram("token", "42", "привет"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
