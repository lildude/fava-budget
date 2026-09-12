"""A focused monthly budgeting dashboard for Fava."""

from __future__ import annotations

from datetime import date

from fava.ext import FavaExtensionBase

from .report import BudgetReport, build_budget_report


class Budget(FavaExtensionBase):
    """Render an easy-to-scan monthly budget report."""

    report_title = "Budget"
    has_js_module = True

    def build_report(
        self,
        month: str | None = None,
        *,
        today: date | None = None,
    ) -> BudgetReport:
        """Build the report for a calendar month."""
        return build_budget_report(
            self.ledger,
            self.config,
            month,
            today=today or date.today(),
        )
