# Fava Budget

`fava_budget` adds a dedicated monthly budgeting page to
[Fava](https://beancount.github.io/fava/). It uses Fava's native budget directives, reads actual expense postings
from the ledger, and keeps all budget data in Beancount rather than a separate database.

The screenshots below use synthetic data from `tests/fixtures/budget.bean`.

![Monthly budget overview](https://raw.githubusercontent.com/lildude/fava-budget/main/docs/images/budget-overview.png)

![Unbudgeted category suggestions](https://raw.githubusercontent.com/lildude/fava-budget/main/docs/images/unbudgeted-suggestions.png)

## Features

- Calendar-month navigation with a direct month picker.
- Summary totals for budget, budgeted spending, remaining budget, and unbudgeted spending.
- Per-category progress, status, and current-month pacing.
- A projected month-end total after the first three days of the current month.
- Most-specific category matching without double-counting parent and child budgets.
- Conversion to one report currency using explicit posting prices or costs first, then Fava's price map.
- Unbudgeted category suggestions based on recent complete months.
- Copyable native budget directives.
- A reconciliation of budgeted, unbudgeted, and deliberately excluded expenses.
- Clear warnings for malformed budgets, unsupported currencies, and missing conversion rates.
- Fava incognito-mode support that masks amounts and disables copying directives.

## Install

Install the extension directly from GitHub:

```shell
uv add git+https://github.com/lildude/fava-budget.git
```

The package supports Python 3.10 or newer and Fava 1.30.8 or newer.

## Enable the extension

Add the extension to the Beancount file that you open with Fava:

```beancount
2019-01-01 custom "fava-extension" "fava_budget" "{
  'currency': 'GBP',
  'exclude_accounts': ['Expenses:GitHub'],
  'suggestion_months': 3,
}"
```

Restart Fava after enabling the extension. A **Budget** link will appear in the Fava sidebar. Because `fava_budget`
is installed as a Python package, `option "insert_pythonpath" "True"` is not required.

## Add budgets

Budgets use Fava's native `budget` custom directive:

```beancount
2026-01-01 custom "budget" Expenses:Groceries "monthly" 500 GBP
2026-01-01 custom "budget" Expenses:EatingOut "monthly" 150 GBP
2026-01-01 custom "budget" Expenses:Travel "yearly" 2,400 GBP
```

The value order is:

```text
DATE custom "budget" ACCOUNT "PERIOD" AMOUNT CURRENCY
```

Supported periods are `daily`, `weekly`, `monthly`, `quarterly`, and `yearly`. Fava prorates each value into the
selected calendar month.

### Change a budget

Add another directive for the same account and currency on the date the new amount becomes effective:

```beancount
2026-04-01 custom "budget" Expenses:Groceries "monthly" 550 GBP
```

The dashboard handles mid-month changes and marks affected rows.

### Stop a budget

Set the amount to zero:

```beancount
2026-07-01 custom "budget" Expenses:Travel "monthly" 0 GBP
```

Zero budgets do not capture spending or appear as active categories.

### Use parent and child budgets

The most-specific active budget owns each posting:

```beancount
2026-01-01 custom "budget" Expenses:Bills "monthly" 400 GBP
2026-01-01 custom "budget" Expenses:Bills:Energy "monthly" 120 GBP
```

`Expenses:Bills:Energy` spending counts against the Energy budget. Other `Expenses:Bills:*` spending counts against
the parent Bills budget. This prevents the same posting from being counted twice.

## Configure the dashboard

All options are optional:

| Option | Default | Purpose |
| --- | --- | --- |
| `currency` | First operating currency | Currency used for totals, comparisons, and suggestions. |
| `expense_account` | Beancount expense root | Account hierarchy included in the dashboard. |
| `exclude_accounts` | `[]` | Prefixes omitted from category budgets but retained in reconciliation. |
| `labels` | `{}` | Mapping from full account names to shorter display labels. |
| `suggestion_months` | `3` | Number of complete prior months used for averages. Valid range: 1 to 24. |
| `suggestion_increment` | `5` | Increment used to round suggested budgets up. |

Example with every option:

```beancount
2019-01-01 custom "fava-extension" "fava_budget" "{
  'currency': 'GBP',
  'expense_account': 'Expenses',
  'exclude_accounts': [
    'Expenses:GitHub',
    'Expenses:Investments',
  ],
  'labels': {
    'Expenses:HealthFitness': 'Health and fitness',
    'Expenses:PublicTransport': 'Public transport',
  },
  'suggestion_months': 6,
  'suggestion_increment': 10,
}"
```

Account matching respects component boundaries. Excluding `Expenses:Car` does not exclude `Expenses:Carpet`.

## Use the dashboard

Start Fava with the ledger that contains the extension directive:

```shell
uv run fava path/to/ledger.bean
```

Open **Budget** in the sidebar.

1. Use the arrow buttons or month field to change the reporting month.
2. Review the summary cards and overall progress.
3. Open a category to inspect its transactions in Fava for that month.
4. Review **Categories to review** for spending without an active budget.
5. Use **Copy directive** to copy a suggested monthly budget, then paste it into an appropriate `.bean` file.

Suggestions use the larger of current spending and the recent monthly average, rounded up by `suggestion_increment`.
Months before an account's first posting are not included in its average.

## How values are calculated

### Current, past, and future months

- A past month includes the complete month.
- The current month includes transactions through today. Future-dated transactions are shown separately.
- A future month shows planned budgets but suppresses actual spending, pacing, and projections.
- Projections appear from day four onward and assume the current spending rate continues.

### Currency conversion

The dashboard calculates each posting in the configured report currency:

1. If the posting units already use the report currency, it uses those units.
2. If the posting has an explicit price or cost in the report currency, it uses that value.
3. Otherwise, it uses the latest applicable rate from Fava's price map.

Postings without a valid conversion rate are excluded from numeric totals and reported as warnings. A row-level note
identifies affected categories.

### Reconciliation and exclusions

The reconciliation is:

```text
converted expenses = budgeted spending + unbudgeted spending + excluded spending
```

Excluded accounts do not affect category progress or suggestions. They remain in reconciliation so the page explains
the difference between budgeted activity and total converted expenses.

The dashboard deliberately ignores Fava's global account, time, filter, and conversion controls. It displays a notice
when one of those controls is active.

## Develop and test with Docker

Build the development image:

```shell
docker build --target dev -t fava-budget-dev .
docker run --rm -it \
  -p 5000:5000 \
  -v "$PWD:/workspace" \
  fava-budget-dev
```

Inside the container:

```shell
ruff check .
ruff format --check .
python -m unittest discover -s tests -t . -v -b
bean-check tests/fixtures/budget.bean
python -m build
fava -H 0.0.0.0 -p 5000 tests/fixtures/budget.bean
```

The tests and screenshots use only the synthetic ledger in `tests/fixtures/budget.bean`.

To run the complete clean-install smoke test, including building a wheel, installing it without the source tree,
checking the fixture, and exercising the Fava extension route:

```shell
docker build --target test -t fava-budget-test .
```

Export the wheel and source distribution using the same Docker build:

```shell
docker build --target artifacts --output type=local,dest=dist .
```

To build a runtime image for your own ledger:

```shell
docker build -t fava-budget .
docker run --rm -p 5000:5000 \
  -v "/absolute/path/to/your/ledger-directory:/data:ro" \
  fava-budget /data/ledger.bean
```

## Troubleshooting

### Budget does not appear

- Confirm the directive account is under `expense_account`.
- Confirm the directive currency matches the configured report currency.
- Confirm the amount is greater than zero for at least part of the selected month.
- Check the warning panel for malformed directives or excluded accounts.

### Spending appears as unbudgeted

- Confirm the budget was active on the transaction date.
- Confirm the account is the budget account or one of its descendants.
- Check whether `exclude_accounts` contains the account or a parent.
- Check for a conversion warning.

### Spending differs from the Income Statement

The Budget page uses its own selected calendar month and ignores Fava's global filters. Compare the reconciliation
total, then check excluded and unconvertible postings.

## License

This project is licensed under the [MIT License](LICENSE). Copyright 2026 Colin Seymour.
