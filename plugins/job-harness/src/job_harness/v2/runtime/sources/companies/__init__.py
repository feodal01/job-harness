"""Company career-site source scrapers."""

from job_harness.v2.runtime.sources.companies.airslate import AirSlateCareerSource
from job_harness.v2.runtime.sources.companies.amocrm import AmoCRMCareerSource
from job_harness.v2.runtime.sources.companies.appfollow import AppFollowCareerSource
from job_harness.v2.runtime.sources.companies.coinspaid import CoinsPaidCareerSource
from job_harness.v2.runtime.sources.companies.ibs import IBSCareerSource
from job_harness.v2.runtime.sources.companies.jetbrains import JetBrainsCareerSource
from job_harness.v2.runtime.sources.companies.vk import VKCareerSource

__all__ = [
    "AmoCRMCareerSource",
    "AppFollowCareerSource",
    "AirSlateCareerSource",
    "CoinsPaidCareerSource",
    "IBSCareerSource",
    "JetBrainsCareerSource",
    "VKCareerSource",
]
