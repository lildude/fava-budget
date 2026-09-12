"""Budget report calculations."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from functools import cache
from typing import TYPE_CHECKING

from beancount.core import convert
from fava.core.budgets import parse_budgets

if TYPE_CHECKING:
    from fava.beans.abc import Posting
    from fava.core import FavaLedger


ZERO = Decimal()
ONE_DAY = timedelta(days=1)
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CONFIG_KEYS = {
    "currency",
    "exclude_accounts",
    "expense_account",
    "labels",
    "suggestion_increment",
    "suggestion_months",
}


@dataclass(frozen=True)
class BudgetConfig:
    """Validated extension configuration."""

    currency: str
    expense_account: str
    exclude_accounts: tuple[str, ...]
    labels: Mapping[str, str]
    suggestion_months: int
    suggestion_increment: Decimal


@dataclass(frozen=True)
class BudgetCategory:
    """A budgeted account shown in the report."""

    account: str
    label: str
    budget: Decimal
    spent: Decimal
    remaining: Decimal
    pace_budget: Decimal
    status: str
    status_label: str
    usage_percent: int
    progress_percent: int
    pace_percent: int
    has_child_budget: bool
    partial_month: bool
    unconverted_count: int


@dataclass(frozen=True)
class UnbudgetedCategory:
    """An account without an active budget."""

    account: str
    label: str
    spent: Decimal
    recent_average: Decimal
    sample_months: int
    suggested: Decimal
    directive: str | None
    covered_later: bool
    unconverted_count: int
    history_incomplete: bool


@dataclass(frozen=True)
class BudgetReport:
    """All values needed by the Budget template."""

    month_start: date
    month_end: date
    month_key: str
    month_label: str
    previous_month: str
    next_month: str
    current_month: str
    state: str
    as_of_label: str
    days_elapsed: int
    days_total: int
    remaining_days: int
    currency: str
    expense_account: str
    suggestion_months: int
    categories: tuple[BudgetCategory, ...]
    unbudgeted: tuple[UnbudgetedCategory, ...]
    total_budget: Decimal
    budgeted_spend: Decimal
    remaining: Decimal
    unbudgeted_spend: Decimal
    excluded_spend: Decimal
    total_expenses: Decimal
    pace_budget: Decimal
    projected_spend: Decimal | None
    remaining_per_day: Decimal | None
    usage_percent: int
    progress_percent: int
    pace_percent: int
    overall_status: str
    overall_status_label: str
    future_spend: Decimal
    future_posting_count: int
    future_unconverted_count: int
    unconverted_posting_count: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    budget_directive_example: str


@dataclass(frozen=True)
class _ActiveBudget:
    account: str
    budget: Decimal
    pace_budget: Decimal
    partial_month: bool


def parse_config(raw_config: object, ledger: FavaLedger) -> tuple[BudgetConfig, tuple[str, ...]]:
    """Validate extension configuration while retaining usable defaults."""
    operating_currencies = ledger.options["operating_currency"]
    default_currency = operating_currencies[0] if operating_currencies else "USD"
    default_expense_account = ledger.options["name_expenses"]
    errors: list[str] = []

    if raw_config is None:
        values: Mapping[str, object] = {}
    elif isinstance(raw_config, Mapping):
        values = raw_config
    else:
        values = {}
        errors.append("Extension configuration must be a dictionary.")

    unknown_keys = sorted(repr(key) for key in values if key not in CONFIG_KEYS)
    if unknown_keys:
        errors.append(f"Unknown configuration option(s): {', '.join(unknown_keys)}.")

    currency_value = values.get("currency", default_currency)
    if isinstance(currency_value, str) and currency_value.strip():
        currency = currency_value.strip().upper()
    else:
        currency = default_currency
        errors.append("The currency option must be a non-empty string.")

    expense_value = values.get("expense_account", default_expense_account)
    if isinstance(expense_value, str) and expense_value.strip(": "):
        expense_account = expense_value.strip().rstrip(":")
    else:
        expense_account = default_expense_account
        errors.append("The expense_account option must be a non-empty account name.")

    exclude_value = values.get("exclude_accounts", ())
    if isinstance(exclude_value, (list, tuple)) and all(isinstance(item, str) for item in exclude_value):
        exclude_accounts = tuple(item.strip().rstrip(":") for item in exclude_value if item.strip(": "))
    else:
        exclude_accounts = ()
        errors.append("The exclude_accounts option must be a list of account names.")

    labels_value = values.get("labels", {})
    if isinstance(labels_value, Mapping) and all(
        isinstance(account, str) and isinstance(label, str) for account, label in labels_value.items()
    ):
        labels = dict(labels_value)
    else:
        labels = {}
        errors.append("The labels option must map account names to labels.")

    months_value = values.get("suggestion_months", 3)
    if type(months_value) is int and 1 <= months_value <= 24:
        suggestion_months = months_value
    else:
        suggestion_months = 3
        errors.append("The suggestion_months option must be an integer from 1 to 24.")

    increment_value = values.get("suggestion_increment", 5)
    try:
        suggestion_increment = Decimal(str(increment_value))
    except (InvalidOperation, ValueError):
        suggestion_increment = Decimal(5)
        errors.append("The suggestion_increment option must be a positive number.")
    else:
        if not suggestion_increment.is_finite() or suggestion_increment <= ZERO:
            suggestion_increment = Decimal(5)
            errors.append("The suggestion_increment option must be a positive number.")

    return (
        BudgetConfig(
            currency=currency,
            expense_account=expense_account,
            exclude_accounts=exclude_accounts,
            labels=labels,
            suggestion_months=suggestion_months,
            suggestion_increment=suggestion_increment,
        ),
        tuple(errors),
    )


def build_budget_report(
    ledger: FavaLedger,
    raw_config: object,
    month: str | None,
    *,
    today: date,
) -> BudgetReport:
    """Calculate one calendar month's budget report."""
    config, config_errors = parse_config(raw_config, ledger)
    month_start, month_error = _parse_month(month, today, config.suggestion_months)
    month_end = _shift_month(month_start, 1)
    errors = list(config_errors)
    if month_error:
        errors.append(month_error)

    if today < month_start:
        state = "future"
        actual_end = month_start
        as_of_label = "Not started"
    elif today >= month_end:
        state = "past"
        actual_end = month_end
        as_of_label = "Complete month"
    else:
        state = "current"
        actual_end = min(today + ONE_DAY, month_end)
        as_of_label = f"Through {today.day} {today.strftime('%B')}"

    days_total = (month_end - month_start).days
    days_elapsed = (actual_end - month_start).days
    remaining_days = max(days_total - days_elapsed, 0)
    precision = ledger.format_decimal.precisions.get(config.currency, 2)
    quantum = Decimal(1).scaleb(-precision)
    tolerance = quantum / 2

    def quantize(value: Decimal) -> Decimal:
        return value.quantize(quantum)

    parsed_budgets, budget_errors = parse_budgets(ledger.all_entries_by_type.Custom)
    errors.extend(f"Budget directive: {error.message}" for error in budget_errors)

    active_budgets: dict[str, _ActiveBudget] = {}
    other_currency_budget_count = 0
    outside_expense_budget_count = 0
    excluded_budget_count = 0
    negative_budget_count = 0

    for account, entries in parsed_budgets.items():
        amounts = ledger.budgets.calculate(account, month_start, month_end)
        has_active_amount = any(abs(amount) > tolerance for amount in amounts.values())
        if not has_active_amount:
            continue
        if not _is_account_or_child(account, config.expense_account):
            outside_expense_budget_count += 1
            continue
        if _matches_any_prefix(account, config.exclude_accounts):
            excluded_budget_count += 1
            continue

        other_currency_budget_count += sum(
            1 for currency, amount in amounts.items() if currency != config.currency and abs(amount) > tolerance
        )
        budget = quantize(amounts.get(config.currency, ZERO))
        if budget < -tolerance:
            negative_budget_count += 1
            continue
        if budget <= tolerance:
            continue

        if state == "future":
            pace_budget = ZERO
        elif state == "past":
            pace_budget = budget
        else:
            pace_budget = quantize(
                ledger.budgets.calculate(account, month_start, actual_end).get(config.currency, ZERO)
            )

        active_budgets[account] = _ActiveBudget(
            account=account,
            budget=budget,
            pace_budget=pace_budget,
            partial_month=any(
                entry.currency == config.currency and month_start <= entry.date_start < month_end for entry in entries
            ),
        )

    active_accounts = tuple(
        sorted(active_budgets, key=lambda account: (account.count(":"), len(account)), reverse=True)
    )

    @cache
    def budget_is_active_on(account: str, posting_date: date) -> bool:
        daily_budget = ledger.budgets.calculate(account, posting_date, posting_date + ONE_DAY)
        return daily_budget.get(config.currency, ZERO) > ZERO

    def budget_owner(account: str, posting_date: date) -> str | None:
        for budget_account in active_accounts:
            if _is_account_or_child(account, budget_account) and budget_is_active_on(budget_account, posting_date):
                return budget_account
        return None

    def selected_month_budget_owner(account: str) -> str | None:
        return next(
            (budget_account for budget_account in active_accounts if _is_account_or_child(account, budget_account)),
            None,
        )

    budget_spend: defaultdict[str, Decimal] = defaultdict(Decimal)
    unbudgeted_spend: defaultdict[str, Decimal] = defaultdict(Decimal)
    unbudgeted_unconverted: defaultdict[str, int] = defaultdict(int)
    budget_unconverted: defaultdict[str, int] = defaultdict(int)
    excluded_spend = ZERO
    current_conversion_failures: defaultdict[str, int] = defaultdict(int)
    future_spend = ZERO
    future_posting_count = 0
    future_unconverted_count = 0

    history_start = _shift_month(month_start, -config.suggestion_months)
    history_spend: defaultdict[str, Decimal] = defaultdict(Decimal)
    history_unconverted: defaultdict[str, int] = defaultdict(int)
    first_seen: dict[str, date] = {}

    for entry in ledger.all_entries_by_type.Transaction:
        if entry.date >= month_end:
            break
        for posting in entry.postings:
            if not _is_account_or_child(posting.account, config.expense_account):
                continue

            excluded = _matches_any_prefix(posting.account, config.exclude_accounts)
            selected_owner = selected_month_budget_owner(posting.account)

            if entry.date < month_start:
                if excluded or selected_owner is not None:
                    continue
                first_seen.setdefault(posting.account, _month_start(entry.date))
                if entry.date < history_start:
                    continue
                value, _ = _posting_value(posting, config.currency, ledger, entry.date)
                if value is None:
                    history_unconverted[posting.account] += 1
                else:
                    history_spend[posting.account] += value
                continue

            if actual_end <= entry.date < month_end:
                if excluded:
                    continue
                future_posting_count += 1
                value, _ = _posting_value(posting, config.currency, ledger, entry.date)
                if value is None:
                    future_unconverted_count += 1
                else:
                    future_spend += value
                continue

            if not (month_start <= entry.date < actual_end):
                continue

            value, source_currency = _posting_value(posting, config.currency, ledger, entry.date)
            if value is None:
                current_conversion_failures[source_currency] += 1
                if excluded:
                    continue
                owner = budget_owner(posting.account, entry.date)
                if owner:
                    budget_unconverted[owner] += 1
                else:
                    unbudgeted_unconverted[posting.account] += 1
                continue

            if excluded:
                excluded_spend += value
                continue

            owner = budget_owner(posting.account, entry.date)
            if owner:
                budget_spend[owner] += value
            else:
                unbudgeted_spend[posting.account] += value

    categories = tuple(
        sorted(
            (
                _build_budget_category(
                    budget,
                    active_accounts,
                    budget_spend[budget.account],
                    budget_unconverted[budget.account],
                    config,
                    state,
                    tolerance,
                    quantize,
                )
                for budget in active_budgets.values()
            ),
            key=lambda category: (
                {"over": 0, "watch": 1, "on-track": 2, "under": 3, "planned": 4}[category.status],
                category.label,
            ),
        )
    )

    unbudgeted = _build_unbudgeted_categories(
        unbudgeted_spend,
        unbudgeted_unconverted,
        history_spend,
        history_unconverted,
        first_seen,
        active_accounts,
        config,
        month_start,
        precision,
        tolerance,
        quantize,
    )

    total_budget = quantize(sum((category.budget for category in categories), ZERO))
    budgeted_spend = quantize(sum((category.spent for category in categories), ZERO))
    unbudgeted_total = quantize(sum((category.spent for category in unbudgeted), ZERO))
    excluded_total = quantize(excluded_spend)
    remaining = quantize(total_budget - budgeted_spend)
    pace_budget = quantize(sum((category.pace_budget for category in categories), ZERO))
    projected_spend = _projected_spend(
        budgeted_spend,
        state,
        days_elapsed,
        days_total,
        quantize,
    )
    remaining_per_day = (
        quantize(remaining / remaining_days)
        if state == "current" and remaining > tolerance and remaining_days
        else None
    )
    overall_status, overall_status_label = _status(
        budgeted_spend,
        total_budget,
        pace_budget,
        state,
        tolerance,
    )

    warnings: list[str] = []
    if other_currency_budget_count:
        warnings.append(
            f"{other_currency_budget_count} active budget amount(s) in currencies other than "
            f"{config.currency} are not shown."
        )
    if outside_expense_budget_count:
        warnings.append(
            f"{outside_expense_budget_count} active budget account(s) outside {config.expense_account} are not shown."
        )
    if excluded_budget_count:
        warnings.append(f"{excluded_budget_count} active budget account(s) are hidden by exclude_accounts.")
    if negative_budget_count:
        warnings.append(f"{negative_budget_count} negative budget amount(s) are not shown.")
    if current_conversion_failures:
        failures = ", ".join(f"{count} {currency}" for currency, count in sorted(current_conversion_failures.items()))
        warnings.append(
            f"Current spending excludes postings that could not be converted to {config.currency}: {failures}."
        )
    history_failure_count = sum(history_unconverted.values())
    if history_failure_count:
        warnings.append(
            f"Recent averages exclude {history_failure_count} posting(s) that could not be converted to "
            f"{config.currency}."
        )
    if future_unconverted_count:
        warnings.append(
            f"The future-dated total excludes {future_unconverted_count} posting(s) that could not be converted."
        )

    example_amount = _format_directive_amount(Decimal(500).quantize(quantum), precision)
    return BudgetReport(
        month_start=month_start,
        month_end=month_end,
        month_key=month_start.strftime("%Y-%m"),
        month_label=month_start.strftime("%B %Y"),
        previous_month=_shift_month(month_start, -1).strftime("%Y-%m"),
        next_month=month_end.strftime("%Y-%m"),
        current_month=today.strftime("%Y-%m"),
        state=state,
        as_of_label=as_of_label,
        days_elapsed=days_elapsed,
        days_total=days_total,
        remaining_days=remaining_days,
        currency=config.currency,
        expense_account=config.expense_account,
        suggestion_months=config.suggestion_months,
        categories=categories,
        unbudgeted=unbudgeted,
        total_budget=total_budget,
        budgeted_spend=budgeted_spend,
        remaining=remaining,
        unbudgeted_spend=unbudgeted_total,
        excluded_spend=excluded_total,
        total_expenses=quantize(budgeted_spend + unbudgeted_total + excluded_total),
        pace_budget=pace_budget,
        projected_spend=projected_spend,
        remaining_per_day=remaining_per_day,
        usage_percent=_usage_percentage(budgeted_spend, total_budget),
        progress_percent=_bar_percentage(budgeted_spend, total_budget),
        pace_percent=_bar_percentage(pace_budget, total_budget),
        overall_status=overall_status,
        overall_status_label=overall_status_label,
        future_spend=quantize(future_spend),
        future_posting_count=future_posting_count,
        future_unconverted_count=future_unconverted_count,
        unconverted_posting_count=sum(current_conversion_failures.values()),
        errors=tuple(errors),
        warnings=tuple(warnings),
        budget_directive_example=(
            f'{month_start.isoformat()} custom "budget" {config.expense_account}:Category '
            f'"monthly" {example_amount} {config.currency}'
        ),
    )


def _build_budget_category(
    budget: _ActiveBudget,
    active_accounts: tuple[str, ...],
    raw_spend: Decimal,
    unconverted_count: int,
    config: BudgetConfig,
    state: str,
    tolerance: Decimal,
    quantize: Callable[[Decimal], Decimal],
) -> BudgetCategory:
    spent = quantize(raw_spend)
    remaining = quantize(budget.budget - spent)
    status, status_label = _status(spent, budget.budget, budget.pace_budget, state, tolerance)
    return BudgetCategory(
        account=budget.account,
        label=_account_label(budget.account, config),
        budget=budget.budget,
        spent=spent,
        remaining=remaining,
        pace_budget=budget.pace_budget,
        status=status,
        status_label=status_label,
        usage_percent=_usage_percentage(spent, budget.budget),
        progress_percent=_bar_percentage(spent, budget.budget),
        pace_percent=_bar_percentage(budget.pace_budget, budget.budget),
        has_child_budget=any(
            account != budget.account and _is_account_or_child(account, budget.account) for account in active_accounts
        ),
        partial_month=budget.partial_month,
        unconverted_count=unconverted_count,
    )


def _build_unbudgeted_categories(
    current_spend: Mapping[str, Decimal],
    current_unconverted: Mapping[str, int],
    history_spend: Mapping[str, Decimal],
    history_unconverted: Mapping[str, int],
    first_seen: Mapping[str, date],
    active_accounts: tuple[str, ...],
    config: BudgetConfig,
    month_start: date,
    precision: int,
    tolerance: Decimal,
    quantize: Callable[[Decimal], Decimal],
) -> tuple[UnbudgetedCategory, ...]:
    candidates = set(current_spend) | set(history_spend) | set(current_unconverted)
    categories: list[UnbudgetedCategory] = []

    for account in candidates:
        spent = quantize(current_spend.get(account, ZERO))
        first_month = first_seen.get(account)
        months_available = _months_between(first_month, month_start) if first_month else 0
        sample_months = min(config.suggestion_months, months_available)
        recent_average = quantize(history_spend.get(account, ZERO) / sample_months if sample_months else ZERO)
        if abs(spent) <= tolerance and recent_average <= tolerance and not current_unconverted.get(account):
            continue

        covered_later = any(_is_account_or_child(account, budget_account) for budget_account in active_accounts)
        suggestion_base = max(spent, recent_average, ZERO)
        suggested = (
            quantize(_round_up(suggestion_base, config.suggestion_increment)) if suggestion_base > tolerance else ZERO
        )
        directive = (
            f'{month_start.isoformat()} custom "budget" {account} "monthly" '
            f"{_format_directive_amount(suggested, precision)} {config.currency}"
            if suggested > tolerance and not covered_later
            else None
        )
        categories.append(
            UnbudgetedCategory(
                account=account,
                label=_account_label(account, config),
                spent=spent,
                recent_average=recent_average,
                sample_months=sample_months,
                suggested=suggested,
                directive=directive,
                covered_later=covered_later,
                unconverted_count=current_unconverted.get(account, 0),
                history_incomplete=bool(history_unconverted.get(account)),
            )
        )

    return tuple(sorted(categories, key=lambda category: (category.spent, category.recent_average), reverse=True))


def _posting_value(
    posting: Posting,
    target_currency: str,
    ledger: FavaLedger,
    posting_date: date,
) -> tuple[Decimal | None, str]:
    units = posting.units
    if isinstance(units.number, Decimal) and units.currency == target_currency:
        return units.number, target_currency

    weight = convert.get_weight(posting)
    source_currency = weight.currency
    if not isinstance(weight.number, Decimal):
        return None, source_currency
    if source_currency == target_currency:
        return weight.number, target_currency

    rate = ledger.prices.get_price((source_currency, target_currency), posting_date)
    return (weight.number * rate, target_currency) if rate is not None else (None, source_currency)


def _projected_spend(
    spent: Decimal,
    state: str,
    days_elapsed: int,
    days_total: int,
    quantize: Callable[[Decimal], Decimal],
) -> Decimal | None:
    if state != "current" or days_elapsed < 4 or spent <= ZERO:
        return None
    return quantize(spent * days_total / days_elapsed)


def _status(
    spent: Decimal,
    budget: Decimal,
    pace_budget: Decimal,
    state: str,
    tolerance: Decimal,
) -> tuple[str, str]:
    if spent > budget + tolerance:
        return "over", "Over budget"
    if state == "future":
        return "planned", "Planned"
    if state == "past":
        return ("under", "On budget") if abs(budget - spent) <= tolerance else ("under", "Under budget")
    if spent > pace_budget + tolerance:
        return "watch", "Ahead of pace"
    return "on-track", "On pace"


def _usage_percentage(numerator: Decimal, denominator: Decimal) -> int:
    if denominator <= ZERO:
        return 100 if numerator > ZERO else 0
    return int((numerator / denominator * 100).to_integral_value())


def _bar_percentage(numerator: Decimal, denominator: Decimal) -> int:
    return max(0, min(100, _usage_percentage(numerator, denominator)))


def _round_up(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def _format_directive_amount(value: Decimal, precision: int) -> str:
    return f"{value:.{precision}f}"


def _parse_month(value: str | None, today: date, suggestion_months: int) -> tuple[date, str | None]:
    current_month = date(today.year, today.month, 1)
    if value is None:
        return current_month, None
    if not MONTH_PATTERN.fullmatch(value):
        return current_month, "Month must use YYYY-MM."
    try:
        year, month = (int(part) for part in value.split("-"))
        selected_month = date(year, month, 1)
        _shift_month(selected_month, -suggestion_months)
        _shift_month(selected_month, 1)
    except ValueError:
        return current_month, "Month must use YYYY-MM within the supported date range."
    return selected_month, None


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _months_between(start: date, end: date) -> int:
    return max((end.year - start.year) * 12 + end.month - start.month, 0)


def _is_account_or_child(account: str, prefix: str) -> bool:
    return account == prefix or account.startswith(f"{prefix}:")


def _matches_any_prefix(account: str, prefixes: tuple[str, ...]) -> bool:
    return any(_is_account_or_child(account, prefix) for prefix in prefixes)


def _account_label(account: str, config: BudgetConfig) -> str:
    configured = config.labels.get(account)
    if configured:
        return configured
    relative = account.removeprefix(f"{config.expense_account}:")
    return " / ".join(_humanize(part) for part in relative.split(":"))


def _humanize(value: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
