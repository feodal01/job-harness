"""Contract-first source scrapers."""

from job_harness.v2.runtime.sources.aggregators.finder_work import FinderWorkSource
from job_harness.v2.runtime.sources.aggregators.geekjob import GeekJobSource
from job_harness.v2.runtime.sources.aggregators.getmatch import GetmatchSource
from job_harness.v2.runtime.sources.aggregators.habr_career import HabrCareerSource
from job_harness.v2.runtime.sources.aggregators.hh_ru import HhRuSource
from job_harness.v2.runtime.sources.aggregators.hirehi import HireHiSource
from job_harness.v2.runtime.sources.aggregators.hirify import HirifySource
from job_harness.v2.runtime.sources.aggregators.it_jobs_uz import ItJobsUzSource
from job_harness.v2.runtime.sources.aggregators.jobturbo import JobTurboSource
from job_harness.v2.runtime.sources.aggregators.staff_am import StaffAmSource
from job_harness.v2.runtime.sources.aggregators.talanto import TalantoSource
from job_harness.v2.runtime.sources.aggregators.talento import TalentoSource
from job_harness.v2.runtime.sources.companies.airslate import AirSlateCareerSource
from job_harness.v2.runtime.sources.companies.amocrm import AmoCRMCareerSource
from job_harness.v2.runtime.sources.companies.appfollow import AppFollowCareerSource
from job_harness.v2.runtime.sources.companies.chainstack import ChainstackCareerSource
from job_harness.v2.runtime.sources.companies.coinspaid import CoinsPaidCareerSource
from job_harness.v2.runtime.sources.companies.ibs import IBSCareerSource
from job_harness.v2.runtime.sources.companies.jetbrains import JetBrainsCareerSource
from job_harness.v2.runtime.sources.companies.outschool import OutschoolCareerSource
from job_harness.v2.runtime.sources.companies.termius import TermiusCareerSource
from job_harness.v2.runtime.sources.companies.three_commas import ThreeCommasCareerSource
from job_harness.v2.runtime.sources.companies.truv import TruvCareerSource
from job_harness.v2.runtime.sources.companies.vk import VKCareerSource
from job_harness.v2.runtime.sources.companies.wallarm import WallarmCareerSource
from job_harness.v2.runtime.sources.companies.wintermute import WintermuteCareerSource
from job_harness.v2.runtime.sources.companies.zeroavia import ZeroAviaCareerSource

__all__ = [
    "AirSlateCareerSource",
    "AmoCRMCareerSource",
    "AppFollowCareerSource",
    "ChainstackCareerSource",
    "CoinsPaidCareerSource",
    "ItJobsUzSource",
    "HireHiSource",
    "HirifySource",
    "JobTurboSource",
    "StaffAmSource",
    "FinderWorkSource",
    "GeekJobSource",
    "GetmatchSource",
    "HabrCareerSource",
    "HhRuSource",
    "IBSCareerSource",
    "JetBrainsCareerSource",
    "OutschoolCareerSource",
    "TalantoSource",
    "TalentoSource",
    "TermiusCareerSource",
    "ThreeCommasCareerSource",
    "TruvCareerSource",
    "VKCareerSource",
    "WallarmCareerSource",
    "WintermuteCareerSource",
    "ZeroAviaCareerSource",
]
