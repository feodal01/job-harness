"""Company career-site source scrapers."""

from job_harness.v2.runtime.sources.companies.amocrm import AmoCRMCareerSource
from job_harness.v2.runtime.sources.companies.ibs import IBSCareerSource
from job_harness.v2.runtime.sources.companies.vk import VKCareerSource

__all__ = [
    "AmoCRMCareerSource",
    "IBSCareerSource",
    "VKCareerSource",
]
