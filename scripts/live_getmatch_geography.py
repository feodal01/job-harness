"""Live acceptance of Getmatch search geography (docs/getmatch-geography-plan.md §6).

Runs the real application in-process against getmatch.ru with an isolated database:
brief via POST /api/v1/briefs (cities chosen from /api/v1/geography/cities), run via
POST /api/v1/runs, real SearchEngine/adapter/network. Every HTTP exchange is recorded.
A fresh unfiltered feed is the control: the raw IDs of each branch, before local
filtering, are compared with the offers whose structured locations fall into its zones.

    uv run python scripts/live_getmatch_geography.py --output docs/experiments/getmatch-geography-2026-11-03
"""

import argparse
import asyncio
import json
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from src.app import create_app
from src.core.config import Settings

FEED = "https://getmatch.ru/api/offers?sa=any&p=1&limit=100&pa=all&offset="
OFFICE = {"office", "hybrid"}
DECODED = {"content-encoding", "content-length", "transfer-encoding"}

# (case, [(country, city text or None for whole country, expected geoname_id, minimum format)])
CASES = [
    ("moscow", [("RU", "Москва", 524901, "office")]),
    ("saint-petersburg", [("RU", "Санкт-Петербург", 498817, "office")]),
    ("kazan", [("RU", "Казань", 551487, "office")]),
    ("russia", [("RU", None, None, "office")]),
    ("yerevan-and-moscow", [("AM", "Ереван", 616052, "office"), ("RU", "Москва", 524901, "hybrid")]),
    ("remote-worldwide", [("RU", "Москва", 524901, "remote_worldwide")]),
]


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.log: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        entry: dict = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "method": request.method,
            "url": str(request.url),
            "status": response.status_code,
        }
        if request.url.path == "/api/offers" and response.status_code == 200:
            data = json.loads(body)
            entry["meta"] = data.get("meta")
            entry["ids"] = [o["id"] for o in data.get("offers", [])]
            entry["offers"] = data.get("offers", [])
        self.log.append(entry)
        # The body is already decoded: drop transfer headers so it is not decoded twice.
        headers = [(k, v) for k, v in response.headers.items() if k.lower() not in DECODED]
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)


def requirements(offer: dict) -> list[dict]:
    return [r for r in offer.get("location_requirements") or [] if not r.get("exclude")]


def ancestors(req: dict) -> set[str]:
    return {req["location_id"], *req.get("ancestors", [])}


# Control predicates observed on 2026-11-02 (plan §2); they are evidence, not production code.
ZONES = {
    "moscow": lambda o: any(r["format"] in OFFICE and "moscow" in ancestors(r) for r in requirements(o)),
    "saints_p": lambda o: any(
        r["format"] in OFFICE and "saint-petersburg" in ancestors(r) for r in requirements(o)
    ),
    "regions_ru": lambda o: any(
        r["format"] in OFFICE
        and "russia" in ancestors(r)
        and not {"moscow", "saint-petersburg"} & ancestors(r)
        for r in requirements(o)
    ),
    "relocate": lambda o: any(
        (r["format"] in OFFICE or r["format"] == "relocation_company") and "russia" not in ancestors(r)
        for r in requirements(o)
    ),
    "remote": lambda o: any(r["format"] == "remote" for r in requirements(o)),
}


async def choose(client: httpx.AsyncClient, country: str, text: str | None, geoname_id: int | None, minimum):
    if text is None:
        return {"country": country, "scope": "country", "minimum_work_format": minimum}
    suggestions = (
        await client.get("/api/v1/geography/cities", params={"country": country, "text": text})
    ).json()
    chosen = next(s for s in suggestions if s["geoname_id"] == geoname_id)  # the user's pick
    return {
        "country": country,
        "geoname_id": chosen["geoname_id"],
        "city_display_name": chosen["name"],
        "minimum_work_format": minimum,
    }


async def control_feed(recorder: Recorder) -> dict[int, dict]:
    offers: dict[int, dict] = {}
    async with httpx.AsyncClient(transport=recorder, timeout=30) as client:
        offset = 0
        while True:
            data = (await client.get(FEED + str(offset))).raise_for_status().json()
            offers.update({o["id"]: o for o in data["offers"]})
            offset += 100
            if offset >= data["meta"]["total"]:
                return offers
            await asyncio.sleep(1)


async def main(output: Path) -> None:
    recorder = Recorder()
    database = Path(tempfile.mkdtemp(prefix="gm-accept-")) / "search.sqlite"
    app = create_app(Settings(database_path=database), transport=recorder)
    cases = []
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        # Card enrichment is outside the geography path; it would add a request per vacancy.
        engine.sources["getmatch"] = replace(engine.sources["getmatch"], vacancy_details=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost", timeout=60
        ) as c:
            for name, places in CASES:
                locations = [await choose(c, *p) for p in places]
                brief = await c.post(
                    "/api/v1/briefs", json={"name": name, "filters": {"locations": locations}}
                )
                brief.raise_for_status()
                start = len(recorder.log)
                run = await c.post(
                    "/api/v1/runs",
                    json={
                        "brief_id": brief.json()["id"],
                        "sources": ["getmatch"],
                        "company_profiles": False,
                        "limits": {"getmatch": {"max_items_per_query": 5000, "max_pages_per_query": 50}},
                    },
                )
                run.raise_for_status()
                await asyncio.gather(*engine.tasks.values())
                run_id = run.json()["id"]
                result = (await c.get(f"/api/v1/runs/{run_id}")).json()
                events = (await c.get(f"/api/v1/runs/{run_id}/events")).json()
                if not any(e["event"] == "search.plan" for e in events):
                    raise RuntimeError(json.dumps([result, events[:5]], ensure_ascii=False)[:3000])
                cases.append(
                    {
                        "case": name,
                        "brief": brief.json(),
                        "run_id": run_id,
                        "status": result["sources"]["getmatch"]["status"],
                        "metrics": result["metrics"],
                        "plan": next(e["data"] for e in events if e["event"] == "search.plan"),
                        "requests": recorder.log[start:],
                    }
                )
                print(name, cases[-1]["status"], flush=True)
    control = await control_feed(recorder)
    report = [verdict(case, control) for case in cases]
    for case in cases:
        for entry in case["requests"]:
            entry.pop("offers", None)  # keep IDs and meta; full bodies stay in the control snapshot
    (output / "runs.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1))
    (output / "control-feed.json").write_text(
        json.dumps(
            {i: {"location_requirements": o.get("location_requirements")} for i, o in control.items()},
            ensure_ascii=False,
        )
    )
    (output / "verdicts.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for row in report:
        print(json.dumps(row, ensure_ascii=False))


def verdict(case: dict, control: dict[int, dict]) -> dict:
    branches: dict[tuple[str, ...], dict] = {}
    for entry in case["requests"]:
        if "/api/offers" not in entry["url"]:
            continue
        zones = tuple(parse_qs(urlsplit(entry["url"]).query).get("l", []))
        branch = branches.setdefault(zones, {"pages": 0, "ids": set(), "total": None})
        branch["pages"] += 1
        branch["ids"].update(entry.get("ids", []))
        branch["total"] = (entry.get("meta") or {}).get("total")
    rows = []
    for zones, branch in branches.items():
        expected = {i for i, o in control.items() if any(ZONES[z](o) for z in zones)}
        rows.append(
            {
                "zones": list(zones),
                "pages": branch["pages"],
                "server_total": branch["total"],
                "raw_ids": len(branch["ids"]),
                "control_expected": len(expected),
                "missing_vs_control": sorted(expected - branch["ids"])[:20],
                "extra_vs_control": sorted(branch["ids"] - expected)[:20],
            }
        )
    return {
        "case": case["case"],
        "status": case["status"],
        "places": [
            (p["status"], [(c["condition"], c["status"], c["params"]) for c in p["conditions"]])
            for p in case["plan"]["places"]
        ],
        "application_locations": case["plan"]["application"]["locations"],
        "form_requests": sum("/vacancies" in e["url"] for e in case["requests"]),
        "feed_size": len(control),
        "branches": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args.output))
