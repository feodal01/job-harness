"""Tests call the local filter with the application's single geography catalog."""

from src.geography.catalog import default_catalog
from src.selection import filtering
from src.selection.salary.settings import SalaryRange

# The spec's test coefficients (docs/brief-parameters/salary-minimum-check.md).
TEST_RANGE = SalaryRange(exact=1.25, possible=1.75)


def evaluate(vacancy, filters, **options):
    return filtering.evaluate(vacancy, filters, catalog=default_catalog(), salary_range=TEST_RANGE, **options)
