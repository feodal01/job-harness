"""Recompute k₁ and k₂ of the salary check from the vacancies in the database (settings.toml [salary_range]).

Of the ranges «A–B» with both bounds named, the upper bound is at least k₁·A in 75% of them and at least
k₂·A in 25% (docs/brief-parameters/salary-minimum-check.md). The script only prints the values; a person
saves them to settings.toml, so the application never changes them while it runs.

    uv run python scripts/salary_coefficients.py [--database PATH]
"""

import argparse
import json
import sqlite3
import statistics
from datetime import date
from pathlib import Path

from src.core.config import Settings
from src.models.vacancy import Vacancy


def ratios(database: Path) -> list[float]:
    found = []
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        for (data,) in connection.execute("SELECT data FROM vacancies"):
            vacancy = Vacancy.from_stored(json.loads(data))
            for record in vacancy.salary:
                if record.state == "specified" and record.from_ and record.to and record.to >= record.from_:
                    found.append(record.to / record.from_)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Settings().database_path)
    values = ratios(parser.parse_args().database)
    if len(values) < 4:
        raise SystemExit("Not enough salary ranges in the database")
    quartiles = statistics.quantiles(values, n=4)
    print(f"[salary_range]\nexact = {quartiles[0]:.2f}\npossible = {quartiles[2]:.2f}")
    print(f"measured_on = {date.today().isoformat()}\nmeasured_ranges = {len(values)}")


if __name__ == "__main__":
    main()
