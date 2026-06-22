"""Contract-first source scrapers."""

from job_harness.v2.runtime.sources.aggregators.finder_work import FinderWorkSource
from job_harness.v2.runtime.sources.aggregators.geekjob import GeekJobSource
from job_harness.v2.runtime.sources.aggregators.getmatch import GetmatchSource
from job_harness.v2.runtime.sources.aggregators.habr_career import HabrCareerSource
from job_harness.v2.runtime.sources.aggregators.hh_ru import HhRuSource
from job_harness.v2.runtime.sources.aggregators.it_jobs_uz import ItJobsUzSource
from job_harness.v2.runtime.sources.aggregators.talanto import TalantoSource
from job_harness.v2.runtime.sources.aggregators.talento import TalentoSource
from job_harness.v2.runtime.sources.companies.jetbrains import JetBrainsCareerSource
from job_harness.v2.runtime.sources.companies.vk import VKCareerSource

__all__ = [
    "ItJobsUzSource",
    "FinderWorkSource",
    "GeekJobSource",
    "GetmatchSource",
    "HabrCareerSource",
    "HhRuSource",
    "JetBrainsCareerSource",
    "TalantoSource",
    "TalentoSource",
    "VKCareerSource",
]
