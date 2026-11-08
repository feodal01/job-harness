"""Run reference search scenarios in-process and record speed metrics (spec 11.1).

Modes:
  offline   simulated HH/Getmatch from real fixtures, live latencies, production limits from settings.toml
  overhead  the same simulated sources without latency or pacing: engine's own cost and request counts
  live      real sources; every request counts against the real HH/Getmatch budget

The service is started in-process with a temporary database, so a running local service is not needed
and its data is not touched. Limits are not coordinated with a separately running service.
"""

import argparse
import asyncio
import hashlib
import json
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx

from benchmarks.fake_sources import FakeSources
from benchmarks.metrics import aggregate, summarize
from src.app import create_app
from src.core.config import ROOT, Settings
from src.db.events import EventRepository
from src.db.resources import ResourceRepository
from src.db.runs import RunRepository
from src.db.vacancies import VacancyRepository

SCENARIOS = Path(__file__).with_name("scenarios.json")
RUN_TIMEOUT_SECONDS = 1800  # Measure whole scenarios; the product timeout would truncate them.


def code_version() -> str:
    digest = hashlib.sha256()
    for path in sorted([*(ROOT / "src").rglob("*.py"), ROOT / "src/web/index.html", ROOT / "settings.toml"]):
        digest.update(path.relative_to(ROOT).as_posix().encode() + path.read_bytes())
    return digest.hexdigest()[:12]


@contextmanager
def timed_store():
    """Accumulate time spent in repository writes without changing the service code."""
    spent = [0.0]
    targets = [
        (VacancyRepository, "save_vacancy"),
        (EventRepository, "event"),
        (RunRepository, "progress"),
        (ResourceRepository, "resource"),
    ]
    originals = {(cls, name): getattr(cls, name) for cls, name in targets}

    def wrap(method):
        def timed(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                return method(self, *args, **kwargs)
            finally:
                spent[0] += time.perf_counter() - started

        return timed

    for (cls, name), method in originals.items():
        setattr(cls, name, wrap(method))
    try:
        yield spent
    finally:
        for (cls, name), method in originals.items():
            setattr(cls, name, method)


async def execute(scenario: dict, mode: str, workdir: Path) -> tuple[dict, dict]:
    overrides: dict = {}
    if mode == "overhead":
        overrides["sources"] = {
            source: {"interval_seconds": 0, "retry_base_seconds": 0} for source in ("hh_ru", "getmatch")
        }
    settings = Settings(
        database_path=workdir / "bench.sqlite", run_timeout_seconds=RUN_TIMEOUT_SECONDS, **overrides
    )
    transport = None if mode == "live" else FakeSources(0 if mode == "overhead" else 1).transport()
    app = create_app(settings, transport=transport)
    with timed_store() as db_spent:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://localhost", timeout=60
            ) as client:
                brief = await client.post(
                    "/api/v1/briefs",
                    json={
                        "name": scenario["id"],
                        "queries": scenario["queries"],
                        "filters": scenario["filters"],
                    },
                )
                brief.raise_for_status()
                body = {
                    "brief_id": brief.json()["id"],
                    "source_rules": {"mode": "selected", "include": scenario["sources"]},
                    "limits": scenario["limits"],
                    "company_profiles": scenario.get("company_profiles"),
                }
                for _ in range(scenario.get("runs_in_a_row", 1)):  # Only the last run is measured.
                    started, db_spent[0] = time.time(), 0.0
                    response = await client.post("/api/v1/runs", json=body)
                    response.raise_for_status()
                    run_id = response.json()["id"]
                    run = await wait_finished(client, run_id)
                events = await all_events(client, run_id)
                results = await all_results(client, run_id)
    policies = {source: getattr(settings.sources, source).model_dump() for source in scenario["sources"]}
    metrics = summarize(started, run, events, results, policies, scenario["queries"], db_spent[0])
    return metrics, {"run": run, "events": events, "results": results}


async def all_results(client: httpx.AsyncClient, run_id: str) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        response = await client.get(f"/api/v1/runs/{run_id}/results", params={"page": page})
        response.raise_for_status()
        batch = response.json()
        results.extend(batch["items"])
        if page >= batch["pages"]:
            return results
        page += 1


async def wait_finished(client: httpx.AsyncClient, run_id: str) -> dict:
    while True:
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        if run["status"] not in {"queued", "running"}:
            return run
        await asyncio.sleep(0.2)


async def all_events(client: httpx.AsyncClient, run_id: str) -> list[dict]:
    events: list[dict] = []
    while True:
        after = events[-1]["id"] if events else 0
        batch = (await client.get(f"/api/v1/runs/{run_id}/events", params={"after": after})).json()
        events.extend(batch)
        if len(batch) < 1000:
            return events


def table(rows: list[tuple[str, dict]]) -> str:
    lines = [
        "| Сценарий | Всего, с | 1-я вакансия, с | 1-я карточка, с | Запросов | Уникальных | Показано "
        "| Запросов на показанную | Разбор, с | Запись в БД, с | Фразы начаты | Загрузка слотов |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for scenario_id, m in rows:
        sources = m["sources"].values()
        lines.append(
            f"| {scenario_id} | {m['wall_s']} | {m['first_vacancy_s']} | {m['first_card_s']} "
            f"| {m['requests']} | {m['unique_vacancies']} | {m['shown_vacancies']} "
            f"| {m['requests_per_shown']} | {m['parse_s']} "
            f"| {m['db_write_s']} | {', '.join(s['queries_started'] for s in sources)} "
            f"| {', '.join(str(s['slot_utilization']) for s in sources)} |"
        )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=["offline", "overhead", "live"], default="overhead")
    parser.add_argument("--scenario", action="append", help="Scenario id; default: all")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, help="Directory for raw data and summary")
    args = parser.parse_args()
    scenarios = json.loads(SCENARIOS.read_text())
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] in args.scenario]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or ROOT / "reports" / "benchmarks" / f"{stamp}-{args.mode}"
    output.mkdir(parents=True, exist_ok=True)
    summary = {"mode": args.mode, "code_version": code_version(), "started": stamp, "scenarios": {}}
    rows = []
    for scenario in scenarios:
        repeats = []
        for n in range(args.repeats):
            with tempfile.TemporaryDirectory() as workdir:
                metrics, raw = await execute(scenario, args.mode, Path(workdir))
            repeats.append(metrics)
            raw_path = output / f"{scenario['id']}-{n + 1}.json"
            raw_path.write_text(json.dumps({"metrics": metrics, **raw}, ensure_ascii=False, indent=1))
            print(f"{scenario['id']} #{n + 1}: {metrics['wall_s']} s, {metrics['requests']} requests")
        summary["scenarios"][scenario["id"]] = {"median": aggregate(repeats), "repeats": repeats}
        rows.append((scenario["id"], summary["scenarios"][scenario["id"]]["median"]))
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(table(rows))
    print("Output:", output)


if __name__ == "__main__":
    asyncio.run(main())
