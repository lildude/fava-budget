from __future__ import annotations

import unittest
from importlib.resources import files


class PackageDataTest(unittest.TestCase):
    def test_template_and_javascript_are_available_as_package_data(self) -> None:
        package = files("fava_budget")
        template = package.joinpath("templates").joinpath("Budget.html").read_text(encoding="utf-8")
        javascript = package.joinpath("Budget.js").read_text(encoding="utf-8")

        self.assertIn('class="budget-page"', template)
        self.assertIn('class="budget-responsive-table budget-categories"', template)
        self.assertIn("@media (width <= 650px)", template)
        self.assertNotIn("\n  header {", template)
        self.assertNotIn("\n  article {", template)
        self.assertIn("bindBudgetCopyButtons", javascript)


if __name__ == "__main__":
    unittest.main()
