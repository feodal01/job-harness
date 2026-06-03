"""Job board scrapers. Importing this module registers all scrapers."""

from job_harness.scrapers.cis_sources import (  # noqa: F401
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
from job_harness.scrapers.company_directory import CompanyDirectoryScraper  # noqa: F401
from job_harness.scrapers.habr_career import HabrCareerScraper  # noqa: F401
from job_harness.scrapers.hh_ru import (  # noqa: F401
    HeadHunterKgScraper,
    HHKzScraper,
    HHRuScraper,
    HHUzScraper,
    RabotaByScraper,
)
