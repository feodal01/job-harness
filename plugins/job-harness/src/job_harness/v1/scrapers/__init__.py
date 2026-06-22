"""Job board scrapers. Importing this module registers all scrapers."""

from job_harness.v1.scrapers.cis_sources import (  # noqa: F401
    FinderWorkScraper,
    GeekJobScraper,
    GetmatchScraper,
    HireHiScraper,
    HirifyScraper,
    ItJobsUzScraper,
    JobTurboScraper,
    StaffAmScraper,
    TalentoScraper,
)
from job_harness.v1.scrapers.company_careers import CompanyCareersScraper  # noqa: F401
from job_harness.v1.scrapers.company_directory import CompanyDirectoryScraper  # noqa: F401
from job_harness.v1.scrapers.habr_career import HabrCareerScraper  # noqa: F401
from job_harness.v1.scrapers.hh_ru import (  # noqa: F401
    HeadHunterKgScraper,
    HHKzScraper,
    HHRuScraper,
    HHUzScraper,
    RabotaByScraper,
)
