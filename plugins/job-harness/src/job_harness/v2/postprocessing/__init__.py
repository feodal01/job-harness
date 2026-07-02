"""Post-processing layer for v2 raw search evidence."""

from job_harness.v2.postprocessing.criteria_plan import CriteriaProcessingPlanner
from job_harness.v2.postprocessing.filter_policy import (
    VacancyFilterCriteria,
    VacancyFilterDecision,
    VacancyFilterFacts,
    decide_vacancy_filter,
)
from job_harness.v2.postprocessing.pipeline import (
    ProcessedResults,
    ProcessingPhase,
    ResultTablePostProcessor,
)

__all__ = [
    "CriteriaProcessingPlanner",
    "ProcessedResults",
    "ProcessingPhase",
    "ResultTablePostProcessor",
    "VacancyFilterCriteria",
    "VacancyFilterDecision",
    "VacancyFilterFacts",
    "decide_vacancy_filter",
]
