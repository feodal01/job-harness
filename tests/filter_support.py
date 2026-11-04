"""Tests call the local filter with the application's single geography catalog."""

from src.geography.catalog import default_catalog
from src.selection import filtering


def evaluate(vacancy, filters, **options):
    return filtering.evaluate(vacancy, filters, catalog=default_catalog(), **options)
