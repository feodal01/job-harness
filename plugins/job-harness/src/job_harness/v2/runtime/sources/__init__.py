"""Contract-first source scrapers."""

from job_harness.v2.runtime.sources.aggregators.habr_career import HabrCareerSource
from job_harness.v2.runtime.sources.aggregators.hh_ru import HhRuSource
from job_harness.v2.runtime.sources.aggregators.talanto import TalantoSource
from job_harness.v2.runtime.sources.companies.jetbrains import JetBrainsCareerSource
from job_harness.v2.runtime.sources.companies.vk import VKCareerSource

__all__ = [
    "HabrCareerSource",
    "HhRuSource",
    "JetBrainsCareerSource",
    "TalantoSource",
    "VKCareerSource",
]
