"""Post-processing layer for v2 raw search evidence."""

from job_harness.v2.postprocessing.criteria_plan import CriteriaProcessingPlanner
from job_harness.v2.postprocessing.pipeline import (
    ProcessedResults,
    ResultTablePostProcessor,
)
from job_harness.v2.postprocessing.report import (
    render_processed_results_html,
    render_processed_results_html_file,
    write_processed_results_html_file,
)

__all__ = [
    "CriteriaProcessingPlanner",
    "ProcessedResults",
    "ResultTablePostProcessor",
    "render_processed_results_html",
    "render_processed_results_html_file",
    "write_processed_results_html_file",
]
