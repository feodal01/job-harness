"""Statistical tails of open salary records (spec: «Записи», k₁ and k₂).

From the distribution of ranges in the corpus: the upper bound is at least k₁·lower in 75% of ranges and
at least k₂·lower in 25%. Product settings (settings.toml, [salary_range]); `scripts/salary_coefficients.py`
recomputes them from the database, a person saves them. They never change while the application runs.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class SalaryRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    exact: float = Field(default=1.26, gt=1, description="k₁: «от A» is exact up to k₁·A")
    possible: float = Field(default=1.79, gt=1, description="k₂: «от A» is possible up to k₂·A")
    measured_on: date | None = Field(default=None, description="Date of the corpus the values come from")
    measured_ranges: int | None = Field(default=None, ge=0, description="Number of ranges in that corpus")
