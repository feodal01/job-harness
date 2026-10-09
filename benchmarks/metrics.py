"""Speed metrics of one scenario execution, computed from engine events (spec 11.1)."""

from collections import Counter, defaultdict


def summarize(
    started: float,
    run: dict,
    events: list[dict],
    results: list[dict],
    settings: dict[str, dict],
    queries: list[str],
    db_seconds: float,
) -> dict:
    wall = run["finished_at"] - started
    saved = [e for e in events if e["event"] == "vacancy.saved"]
    described = [e for e in saved if e["data"]["has_description"]]
    selections = Counter((r.get("selection") or {}).get("status", "none") for r in results)
    shown = selections["accepted"] + selections["unknown"]
    by_source: dict[str, dict] = {}
    for source, state in run["sources"].items():
        own = [e for e in events if e["data"].get("source") == source]
        listed = {e["data"]["parameters"]["query"] for e in own if e["event"] == "search.request"}
        http = [e["data"] for e in own if e["event"] == "http.finished"]
        busy = sum(e["elapsed_ms"] for e in http) / 1000
        policy = settings[source]
        by_source[source] = {
            "status": state["status"],
            "limit_reached": state["limit_reached"],
            "requests": len(http),
            "requests_by_stage": dict(Counter(e["stage"] for e in http)),
            "http_statuses": dict(Counter(e["status_code"] for e in http)),
            "errors": sum(1 for e in own if e["event"].endswith(".error")),
            "unique_vacancies": sum(1 for r in results if r.get("source") == source),
            "reused_cards": state["metrics"]["details_reused"],
            "reused_profiles": state["metrics"]["companies_reused"],
            # Share of the run in which the allowed parallel slots were busy with HTTP.
            "slot_utilization": round(busy / (wall * policy["concurrency"]), 3) if wall else None,
            # Share of the run that the pacing interval alone would require for these requests.
            "pacing_utilization": round(len(http) * policy["interval_seconds"] / wall, 3) if wall else None,
            "wait_for_limit_s": round(sum(e["wait_ms"] for e in http) / 1000, 2),
            "queries_started": f"{len(listed)}/{len(queries) or 1}",
        }
    requests = sum(s["requests"] for s in by_source.values())
    parse_seconds = sum(e["data"]["elapsed_ms"] / 1000 for e in events if e["event"] == "parse.finished")
    return {
        "wall_s": round(wall, 2),
        "first_vacancy_s": round(min(e["time"] for e in saved) - started, 2) if saved else None,
        "first_card_s": round(min(e["time"] for e in described) - started, 2) if described else None,
        "requests": requests,
        "unique_vacancies": len(results),
        "shown_vacancies": shown,
        "selection": dict(selections),
        "requests_per_shown": round(requests / shown, 2) if shown else None,
        "parse_s": round(parse_seconds, 2),
        "db_write_s": round(db_seconds, 2),
        "status": run["status"],
        "sources": by_source,
    }


def aggregate(repeats: list[dict]) -> dict:
    """Median of numeric top-level metrics across repeats; source details from the median-wall repeat."""
    values = defaultdict(list)
    for item in repeats:
        for key, value in item.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key].append(value)
    median = {k: sorted(v)[len(v) // 2] for k, v in values.items()}
    middle = sorted(repeats, key=lambda r: r["wall_s"])[len(repeats) // 2]
    return {**middle, **median, "repeats": len(repeats)}
