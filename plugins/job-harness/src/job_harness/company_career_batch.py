"""Concurrent incremental runner for company career-page checks."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from json import JSONDecodeError
from pathlib import Path
from urllib.parse import urljoin

from job_harness.browser import configure_playwright_tmpdir, create_browser_async
from job_harness.company_career_search import (
    CompanyVacancyHit,
    LINK_EXTRACTION_SCRIPT,
    _clean_text,
    _dedupe_hits,
    _find_matching_ats_jobs,
    _find_matching_links_from_html,
    _find_matching_links_http,
    _has_known_no_open_positions,
    _is_navigation_or_social,
    _is_non_vacancy_link,
    _is_vacancy_like_link,
    _query_terms,
    _score_text,
)
from job_harness.company_directory import (
    COMPANY_DIRECTORY_PATH,
    CompanyProfile,
    filter_company_directory,
    normalize_company_key,
)
from job_harness.employer_cache import CompanyEntry

DEFAULT_COMPANY_LIVE_WORKERS = 12
RESOLVED_EMPLOYER_CACHE_SOURCE = "resolved-employer-cache"


async def run_company_career_batch(
    query: str,
    *,
    output_jsonl: Path | str,
    summary_json: Path | str,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_companies: int | None = None,
    workers: int = DEFAULT_COMPANY_LIVE_WORKERS,
    timeout_ms: int = 8000,
    directory_path: Path | str = COMPANY_DIRECTORY_PATH,
    employer_cache_paths: Sequence[Path | str] | None = None,
    headless: bool = True,
    progress: bool = False,
) -> dict:
    """Run a resumable concurrent pass and write one JSONL record per company."""
    if workers < 1:
        raise ValueError("workers must be >= 1")

    output_path = Path(output_jsonl)
    summary_path = Path(summary_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    companies = _load_company_targets(
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        max_results=max_companies,
        directory_path=directory_path,
        output_path=output_path,
        employer_cache_paths=employer_cache_paths,
    )
    completed = _read_completed_companies(output_path)
    pending = [company for company in companies if company.name not in completed]

    if not pending:
        return _write_summary(query=query, companies_considered=len(companies), output_path=output_path, summary_path=summary_path)

    queue: asyncio.Queue[tuple[int, CompanyProfile]] = asyncio.Queue()
    for index, company in enumerate(pending, start=1):
        queue.put_nowait((index, company))

    query_terms = _query_terms(query)
    write_lock = asyncio.Lock()
    configure_playwright_tmpdir()

    from rebrowser_playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser, context = await create_browser_async(pw, headless=headless)
        try:
            tasks = [
                asyncio.create_task(
                    _worker(
                        worker_id=worker_id,
                        queue=queue,
                        context=context,
                        query_terms=query_terms,
                        total=len(pending),
                        output_path=output_path,
                        summary_path=summary_path,
                        write_lock=write_lock,
                        timeout_ms=timeout_ms,
                        query=query,
                        companies_considered=len(companies),
                        progress=progress,
                    )
                )
                for worker_id in range(1, min(workers, len(pending)) + 1)
            ]
            await asyncio.gather(*tasks)
        finally:
            await browser.close()

    return _write_summary(query=query, companies_considered=len(companies), output_path=output_path, summary_path=summary_path)


def _load_company_targets(
    *,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_results: int | None = None,
    directory_path: Path | str = COMPANY_DIRECTORY_PATH,
    output_path: Path | str | None = None,
    employer_cache_paths: Sequence[Path | str] | None = None,
) -> list[CompanyProfile]:
    companies = filter_company_directory(
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        max_results=None,
        path=directory_path,
    )
    cache_paths = (
        [Path(path) for path in employer_cache_paths]
        if employer_cache_paths is not None
        else _default_employer_cache_paths(directory_path=Path(directory_path), output_path=Path(output_path) if output_path else None)
    )
    merged = _merge_resolved_employer_caches(companies, cache_paths)
    return merged if max_results is None else merged[:max_results]


def _default_employer_cache_paths(*, directory_path: Path, output_path: Path | None) -> list[Path]:
    paths = [directory_path.parent / "company-careers.json"]
    artifact_cache = _infer_artifact_cache_path(output_path) if output_path is not None else None
    if artifact_cache is not None:
        paths.append(artifact_cache)
    paths.append(Path(".job-harness") / "companies" / "careers.json")
    return _unique_paths(paths)


def _infer_artifact_cache_path(output_path: Path) -> Path | None:
    for parent in [output_path.parent, *output_path.parents]:
        if parent.name == ".job-harness":
            return parent / "companies" / "careers.json"
    return None


def _unique_paths(paths: Sequence[Path]) -> list[Path]:
    unique: dict[Path, Path] = {}
    for path in paths:
        unique[path.resolve() if path.exists() else path] = path
    return list(unique.values())


def _merge_resolved_employer_caches(
    companies: list[CompanyProfile],
    cache_paths: Sequence[Path],
) -> list[CompanyProfile]:
    by_key = {normalize_company_key(company.name): company for company in companies}
    for path in cache_paths:
        for entry in _read_cache_entries(path):
            if entry.ignored or not entry.careers_url:
                continue
            key = normalize_company_key(entry.company)
            existing = by_key.get(key)
            by_key[key] = _merge_cache_entry(existing, entry)
    return sorted(by_key.values(), key=lambda company: company.name.casefold())


def _read_cache_entries(path: Path) -> list[CompanyEntry]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Employer cache must be a JSON object: {path}")

    entries: list[CompanyEntry] = []
    for value in raw.values():
        if not isinstance(value, dict):
            raise ValueError(f"Employer cache entries must be objects: {path}")
        entries.append(CompanyEntry(**value))
    return entries


def _merge_cache_entry(existing: CompanyProfile | None, entry: CompanyEntry) -> CompanyProfile:
    sources = set(existing.sources if existing else ())
    sources.add(RESOLVED_EMPLOYER_CACHE_SOURCE)
    return CompanyProfile(
        name=existing.name if existing else entry.company,
        careers_url=entry.careers_url,
        linkedin_url=existing.linkedin_url if existing else None,
        linkedin_jobs_url=existing.linkedin_jobs_url if existing else None,
        description=existing.description if existing else None,
        industry=existing.industry if existing else None,
        headcount=existing.headcount if existing else None,
        remote=existing.remote if existing else False,
        job_types=existing.job_types if existing else (),
        stack=existing.stack if existing else (),
        countries=existing.countries if existing else (),
        ats_type=entry.ats_type,
        scraper_name=entry.scraper_name,
        last_checked=entry.last_checked,
        last_found_roles=entry.last_found_roles,
        sources=tuple(sorted(sources)),
    )


async def _worker(
    *,
    worker_id: int,
    queue: asyncio.Queue[tuple[int, CompanyProfile]],
    context,
    query_terms: list[str],
    total: int,
    output_path: Path,
    summary_path: Path,
    write_lock: asyncio.Lock,
    timeout_ms: int,
    query: str,
    companies_considered: int,
    progress: bool,
) -> None:
    while True:
        try:
            index, company = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        if progress:
            print(f"[worker {worker_id}] [{index}/{total}] {company.name}", file=sys.stderr)
        record = await _check_company(context, company, query_terms, timeout_ms)
        async with write_lock:
            with output_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
                file.flush()
            _write_summary(
                query=query,
                companies_considered=companies_considered,
                output_path=output_path,
                summary_path=summary_path,
            )
        queue.task_done()


async def _check_company(context, company: CompanyProfile, query_terms: list[str], timeout_ms: int) -> dict:
    if _has_known_no_open_positions(company):
        return {
            "company": company.name,
            "status": "ok",
            "method": "known_no_open_positions",
            "careers_url": company.careers_url,
            "hit_count": 0,
            "hits": [],
        }

    if not company.careers_url:
        if company.linkedin_jobs_url:
            try:
                hits = await asyncio.to_thread(
                    _find_matching_links_http,
                    company,
                    query_terms,
                    url=company.linkedin_jobs_url,
                )
                return {
                    "company": company.name,
                    "status": "ok",
                    "method": "alternate_jobs_http",
                    "careers_url": None,
                    "alternate_url": company.linkedin_jobs_url,
                    "hit_count": len(hits),
                    "hits": [asdict(hit) for hit in hits],
                }
            except Exception as exc:
                return {
                    "company": company.name,
                    "status": "error",
                    "careers_url": None,
                    "alternate_url": company.linkedin_jobs_url,
                    "error": str(exc),
                    "hits": [],
                }
        return {
            "company": company.name,
            "status": "skipped",
            "reason": "missing careers_url",
            "careers_url": None,
            "linkedin_jobs_url": company.linkedin_jobs_url,
            "hits": [],
        }

    attempt_errors = []
    try:
        ats_hits = await asyncio.to_thread(_find_matching_ats_jobs, company, query_terms)
        if ats_hits is not None:
            return {
                "company": company.name,
                "status": "ok",
                "method": "ats_api",
                "careers_url": company.careers_url,
                "hit_count": len(ats_hits),
                "hits": [asdict(hit) for hit in ats_hits],
            }
    except Exception as exc:
        attempt_errors.append({"method": "ats_api", "error": str(exc)})

    page = await context.new_page()
    try:
        await page.goto(company.careers_url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(1000)
        hits = await _find_matching_links_with_timeout(page, company, query_terms, timeout_ms)
        return {
            "company": company.name,
            "status": "ok",
            "method": "browser",
            "careers_url": company.careers_url,
            "hit_count": len(hits),
            "hits": [asdict(hit) for hit in hits],
        }
    except Exception as exc:
        attempt_errors.append({"method": "browser", "error": str(exc)})
        try:
            hits = await _find_matching_browser_html_with_timeout(page, company, query_terms, timeout_ms)
            return {
                "company": company.name,
                "status": "ok",
                "method": "browser_html",
                "careers_url": company.careers_url,
                "hit_count": len(hits),
                "hits": [asdict(hit) for hit in hits],
                "attempt_errors": attempt_errors,
            }
        except Exception as html_exc:
            attempt_errors.append({"method": "browser_html", "error": str(html_exc)})
        try:
            hits = await asyncio.to_thread(_find_matching_links_http, company, query_terms)
            return {
                "company": company.name,
                "status": "ok",
                "method": "http",
                "careers_url": company.careers_url,
                "hit_count": len(hits),
                "hits": [asdict(hit) for hit in hits],
                "attempt_errors": attempt_errors,
            }
        except Exception as http_exc:
            attempt_errors.append({"method": "http", "error": str(http_exc)})
            if company.linkedin_jobs_url:
                try:
                    hits = await asyncio.to_thread(
                        _find_matching_links_http,
                        company,
                        query_terms,
                        url=company.linkedin_jobs_url,
                    )
                    return {
                        "company": company.name,
                        "status": "ok",
                        "method": "alternate_jobs_http",
                        "careers_url": company.careers_url,
                        "alternate_url": company.linkedin_jobs_url,
                        "hit_count": len(hits),
                        "hits": [asdict(hit) for hit in hits],
                        "attempt_errors": attempt_errors,
                    }
                except Exception as alternate_exc:
                    attempt_errors.append({"method": "alternate_jobs_http", "error": str(alternate_exc)})
            return {
                "company": company.name,
                "status": "error",
                "careers_url": company.careers_url,
                "error": attempt_errors[-1]["error"],
                "attempt_errors": attempt_errors,
                "hits": [],
            }
    finally:
        try:
            await asyncio.wait_for(page.close(), timeout=2)
        except Exception:
            pass


async def _find_matching_browser_html_with_timeout(
    page,
    company: CompanyProfile,
    query_terms: list[str],
    timeout_ms: int,
) -> list[CompanyVacancyHit]:
    task = asyncio.create_task(page.content())
    done, _ = await asyncio.wait({task}, timeout=timeout_ms / 1000)
    if task not in done:
        task.add_done_callback(_consume_task_exception)
        raise TimeoutError(f"browser HTML snapshot timeout after {timeout_ms}ms")
    return _find_matching_links_from_html(
        html=task.result(),
        base_url=page.url,
        careers_url=company.careers_url or page.url,
        company=company,
        query_terms=query_terms,
    )


async def _find_matching_links_with_timeout(
    page,
    company: CompanyProfile,
    query_terms: list[str],
    timeout_ms: int,
) -> list[CompanyVacancyHit]:
    task = asyncio.create_task(_find_matching_links_async(page, company, query_terms))
    done, _ = await asyncio.wait({task}, timeout=timeout_ms / 1000)
    if task not in done:
        task.add_done_callback(_consume_task_exception)
        raise TimeoutError(f"link extraction timeout after {timeout_ms}ms")
    return task.result()


def _consume_task_exception(task: asyncio.Task) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _find_matching_links_async(page, company: CompanyProfile, query_terms: list[str]) -> list[CompanyVacancyHit]:
    hits: list[CompanyVacancyHit] = []
    links = await page.evaluate(LINK_EXTRACTION_SCRIPT)
    for link in links:
        if not isinstance(link, dict):
            continue
        href = (link.get("href") or "").strip()
        text = _clean_text(link.get("text") or "")
        if not href or href == "#" or href.startswith("javascript:"):
            continue
        if _is_navigation_or_social(href):
            continue

        absolute_url = urljoin(page.url, href)
        if _is_non_vacancy_link(absolute_url, text):
            continue
        if not _is_vacancy_like_link(absolute_url, text, source_url=page.url):
            continue
        searchable = f"{text} {absolute_url}".casefold()
        score = _score_text(searchable, query_terms)
        if score == 0:
            continue

        hits.append(
            CompanyVacancyHit(
                company=company.name,
                title=(text or absolute_url)[:200],
                vacancy_url=absolute_url,
                careers_url=company.careers_url or page.url,
                matched_text=text[:500],
                score=score,
                countries=list(company.countries),
                stack=list(company.stack),
                job_types=list(company.job_types),
            )
        )
    return _dedupe_hits(hits)


def _read_completed_companies(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed = set()
    for record in _iter_jsonl_records(path):
        completed.add(record["company"])
    return completed


def _build_summary(*, query: str, companies_considered: int, output_path: Path) -> dict:
    records = list(_iter_jsonl_records(output_path))

    hits = []
    for record in records:
        hits.extend(record.get("hits", []))

    return {
        "query": query,
        "companies_considered": companies_considered,
        "companies_recorded": len(records),
        "companies_checked": sum(1 for record in records if record["status"] == "ok"),
        "companies_skipped": sum(1 for record in records if record["status"] == "skipped"),
        "companies_error": sum(1 for record in records if record["status"] == "error"),
        "companies_pending": max(0, companies_considered - len(records)),
        "total": len(hits),
        "hits": sorted(hits, key=lambda hit: (-hit["score"], hit["company"].casefold(), hit["title"].casefold())),
        "errors": [record for record in records if record["status"] == "error"],
        "skipped": [record for record in records if record["status"] == "skipped"],
    }


def _write_summary(*, query: str, companies_considered: int, output_path: Path, summary_path: Path) -> dict:
    summary = _build_summary(query=query, companies_considered=companies_considered, output_path=output_path)
    tmp_path = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(summary_path)
    return summary


def _iter_jsonl_records(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except JSONDecodeError:
                continue
