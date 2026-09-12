from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from fava.application import create_app
from fava.core import FavaLedger

from fava_budget import Budget
from fava_budget.report import parse_config

FIXTURE = Path(__file__).parent / "fixtures" / "budget.bean"


class BudgetReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = FavaLedger(str(FIXTURE), poll_watcher=True)
        extension = cls.ledger.extensions.get_extension("Budget")
        if not isinstance(extension, Budget):
            raise AssertionError("Budget extension did not load")
        cls.extension = extension

    def test_ledger_and_extension_load_without_errors(self) -> None:
        self.assertEqual(self.ledger.load_errors, [])
        self.assertEqual(self.ledger.extensions.errors, [])

    def test_current_month_report_reconciles_spending(self) -> None:
        report = self.extension.build_report("2026-02", today=date(2026, 2, 10))
        categories = {category.account: category for category in report.categories}

        self.assertEqual(report.total_budget, Decimal("450.10"))
        self.assertEqual(report.budgeted_spend, Decimal("150.15"))
        self.assertEqual(report.unbudgeted_spend, Decimal("220.00"))
        self.assertEqual(report.excluded_spend, Decimal("25.00"))
        self.assertEqual(report.total_expenses, Decimal("395.15"))
        self.assertEqual(report.projected_spend, Decimal("420.42"))
        self.assertEqual(report.future_spend, Decimal("50.00"))
        self.assertEqual(report.future_posting_count, 1)
        self.assertEqual(report.unconverted_posting_count, 1)

        self.assertEqual(categories["Expenses:Food"].label, "Food and dining")
        self.assertEqual(categories["Expenses:Food"].spent, Decimal("90.00"))
        self.assertTrue(categories["Expenses:Food"].has_child_budget)
        self.assertEqual(categories["Expenses:Food:Coffee"].spent, Decimal("20.00"))
        self.assertEqual(categories["Expenses:Car"].spent, Decimal("40.00"))
        self.assertEqual(categories["Expenses:Tiny"].spent, Decimal("0.15"))
        self.assertEqual(categories["Expenses:Tiny"].usage_percent, 150)
        self.assertEqual(categories["Expenses:Tiny"].progress_percent, 100)

    def test_prefixes_do_not_match_similarly_named_accounts(self) -> None:
        report = self.extension.build_report("2026-02", today=date(2026, 2, 10))
        categories = {category.account: category for category in report.categories}
        unbudgeted = {category.account: category for category in report.unbudgeted}

        self.assertEqual(categories["Expenses:Car"].spent, Decimal("40.00"))
        self.assertEqual(unbudgeted["Expenses:Carpet"].spent, Decimal("70.00"))
        self.assertEqual(unbudgeted["Expenses:ExcludedExtra"].spent, Decimal("10.00"))

    def test_unbudgeted_suggestion_uses_complete_prior_months(self) -> None:
        report = self.extension.build_report("2026-02", today=date(2026, 2, 10))
        transport = next(category for category in report.unbudgeted if category.account == "Expenses:Transport")

        self.assertEqual(transport.recent_average, Decimal("60.00"))
        self.assertEqual(transport.sample_months, 3)
        self.assertEqual(transport.suggested, Decimal("60.00"))
        self.assertEqual(
            transport.directive,
            '2026-02-01 custom "budget" Expenses:Transport "monthly" 60.00 GBP',
        )

    def test_future_month_suppresses_actuals_and_projection(self) -> None:
        report = self.extension.build_report("2026-03", today=date(2026, 2, 10))

        self.assertEqual(report.state, "future")
        self.assertEqual(report.days_elapsed, 0)
        self.assertEqual(report.budgeted_spend, Decimal("0.00"))
        self.assertIsNone(report.projected_spend)
        self.assertEqual(report.total_budget, Decimal("450.10"))

    def test_invalid_month_is_reported(self) -> None:
        report = self.extension.build_report("2026-13", today=date(2026, 2, 10))

        self.assertEqual(report.month_key, "2026-02")
        self.assertIn("Month must use YYYY-MM.", report.errors)

    def test_boundary_month_is_reported(self) -> None:
        report = self.extension.build_report("9999-12", today=date(2026, 2, 10))

        self.assertEqual(report.month_key, "2026-02")
        self.assertIn("Month must use YYYY-MM within the supported date range.", report.errors)

    def test_invalid_config_is_reported_with_defaults(self) -> None:
        config, errors = parse_config(
            {"currency": "", "exclude_account": ["Expenses:Excluded"], "suggestion_months": 0},
            self.ledger,
        )

        self.assertEqual(config.currency, "GBP")
        self.assertEqual(config.suggestion_months, 3)
        self.assertTrue(any("Unknown configuration option" in error for error in errors))
        self.assertTrue(any("currency option" in error for error in errors))


class BudgetRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app([FIXTURE], load=True, poll_watcher=True)
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    def test_budget_report_and_javascript_routes(self) -> None:
        response = self.client.get("/budget-test/extension/Budget/?month=2026-02")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Budget overview", response.data)
        self.assertIn(b"Food and dining", response.data)
        self.assertIn(b"Unbudgeted spending", response.data)

        javascript = self.client.get("/budget-test/extension_js_module/Budget.js")
        self.assertEqual(javascript.status_code, 200)
        self.assertIn(b"bindBudgetCopyButtons", javascript.data)

    def test_incognito_mode_masks_amounts_and_copy_actions(self) -> None:
        app = create_app([FIXTURE], load=True, incognito=True, poll_watcher=True)
        app.config.update(TESTING=True)

        response = app.test_client().get("/budget-test/extension/Budget/?month=2026-02")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<span class="budget-money">XXX.XX</span>', response.data)
        self.assertNotIn(b"data-budget-directive=", response.data)


if __name__ == "__main__":
    unittest.main()
