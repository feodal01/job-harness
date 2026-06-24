"""Presentation renderers for v2 processed search results."""

from job_harness.v2.presentation.formatters import render_processed_results_markdown
from job_harness.v2.presentation.report import render_processed_results_html

__all__ = [
    "render_processed_results_html",
    "render_processed_results_markdown",
]
