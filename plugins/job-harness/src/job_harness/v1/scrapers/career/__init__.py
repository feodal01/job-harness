"""Per-company career site scrapers — one per company.

Each scraper is async and registers in the main job_harness.v1.registry
under a `career:<name>` key. Importing this package side-effect
registers all of them.
"""

from job_harness.v1.scrapers.career.ibs import IBSCareerScraper  # noqa: F401
from job_harness.v1.scrapers.career.vk import VKCareerScraper  # noqa: F401
