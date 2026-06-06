"""Per-company career site scrapers — one per company.

Each scraper is async and registers in the main job_harness.registry
under a `career:<name>` key. Importing this package side-effect
registers all of them.
"""

from job_harness.scrapers.career.ibs import IBSCareerScraper  # noqa: F401
from job_harness.scrapers.career.vk import VKCareerScraper  # noqa: F401
