"""Exercise the running service; record real timings, results and all search events."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from statistics import mean

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--filters", type=Path, help="JSON object with common filter parameters")
    parser.add_argument("--country")
    parser.add_argument("--city")
    parser.add_argument("--source", action="append", help="Source id; repeat for several (default: hh_ru)")
    parser.add_argument("--items", type=int, default=10, help="New vacancies per phrase")
    parser.add_argument("--pages", type=int, default=2, help="Pages per native request")
    parser.add_argument("--company-profiles", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--output", type=Path, default=Path("reports/live-hh.json"))
    args = parser.parse_args()
    args.source = args.source or ["hh_ru"]
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=15) as client:
        filters = json.loads(args.filters.read_text()) if args.filters else {}
        if args.country:
            if "locations" in filters:
                raise ValueError("Choose locations in --filters or --country, not both")
            response = client.get("/api/v1/geography/countries")
            response.raise_for_status()
            matches = [c for c in response.json() if args.country in (c["code"], c["name"])]
            if len(matches) != 1:
                raise ValueError("Choose a country code or name from the common catalog")
            filters["locations"] = [{"country": matches[0]["code"], "city": args.city}]
        elif args.city:
            raise ValueError("--city requires --country")
        brief = client.post(
            "/api/v1/briefs",
            json={
                "name": "Проверка · " + (", ".join(args.query) or ", ".join(args.source)),
                "queries": args.query,
                "filters": filters,
            },
        )
        brief.raise_for_status()
        response = client.post(
            "/api/v1/runs",
            json={
                "brief_id": brief.json()["id"],
                "sources": args.source,
                "limits": {
                    source: {"max_items_per_query": args.items, "max_pages_per_query": args.pages}
                    for source in args.source
                },
                "company_profiles": args.company_profiles,
            },
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        print("Run:", run_id, flush=True)
        started = time.monotonic()
        while True:
            run = client.get(f"/api/v1/runs/{run_id}")
            run.raise_for_status()
            if run.json()["status"] not in {"queued", "running"}:
                break
            if time.monotonic() - started > 600:
                client.post(f"/api/v1/runs/{run_id}/cancel").raise_for_status()
                raise TimeoutError("Smoke exceeded 600s; cancellation requested")
            time.sleep(0.5)
        results = []
        page = 1
        while True:
            response = client.get(f"/api/v1/runs/{run_id}/results", params={"page": page})
            response.raise_for_status()
            batch = response.json()
            results.extend(batch["items"])
            if page >= batch["pages"]:
                break
            page += 1
        events = []
        while True:
            response = client.get(
                f"/api/v1/runs/{run_id}/events", params={"after": events[-1]["id"] if events else 0}
            )
            response.raise_for_status()
            batch = response.json()
            events.extend(batch)
            if len(batch) < 1000:
                break
    responses = [e["data"] for e in events if e["event"] == "http.finished"]
    stages = {}
    for stage in ("geography", "preparation", "listing", "detail", "company"):
        timings = [e["elapsed_ms"] for e in responses if e["stage"] == stage]
        stages[stage] = {
            "requests": len(timings),
            "total_http_ms": round(sum(timings), 2),
            "mean_http_ms": round(mean(timings), 2) if timings else None,
        }
    report = {
        "run": run.json(),
        "results": results,
        "events": events,
        "http_statuses": dict(Counter(e["status_code"] for e in responses)),
        "http_by_stage": stages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["run"]["status"],
                "metrics": report["run"]["metrics"],
                "http_statuses": report["http_statuses"],
                "http_by_stage": stages,
            },
            ensure_ascii=False,
        )
    )
    print("Report:", args.output)


if __name__ == "__main__":
    main()
