"""Exercise the installed plugin over real stdio, HTTP and optionally live job sources."""

import argparse
import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import httpx
from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters


@asynccontextmanager
async def connection(plugin: Path, data: Path, cache: Path, port: int, cold: bool):
    env = {
        **os.environ,
        "JOB_SEARCH_DATA_DIR": str(data),
        "JOB_SEARCH_RUNTIME_DIR": str(cache),
        "JOB_SEARCH_PORT": str(port),
    }
    if cold:
        env["PATH"] = "/usr/bin:/bin"
    manifest = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["job-search"]
    params = StdioServerParameters(
        command=manifest["command"],
        args=manifest["args"],
        env=env,
        cwd=str(plugin / manifest["cwd"]),
    )
    with (data.parent / "plugin-mcp.log").open("a") as log:
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=240)) as client:
                await client.initialize()
                yield client


async def call(client: ClientSession, name: str, arguments: dict | None = None):
    response = await client.call_tool(name, arguments or {})
    if response.isError:
        raise RuntimeError(f"{name}: {response.content}")
    assert response.structuredContent is not None, f"{name} needs structured output"
    return response.structuredContent


async def run(args):
    data = args.data_dir.resolve()
    assert not (data / "search.sqlite").exists(), "Use a fresh test data directory"
    data.parent.mkdir(parents=True, exist_ok=True)
    plugin = args.plugin.resolve()
    cache = args.cache.resolve()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    report = {"plugin": str(plugin), "data_dir": str(data), "port": port}
    async with connection(plugin, data, cache, port, args.cold) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
        report["tools"] = names
        assert {"authorize", "list_companies", "get_company_filters"} <= set(names)
        assert (await call(client, "get_service_status"))["status"] == "stopped"
        assert not (data / "service.json").exists(), "MCP initialization must not start the service"
        status = await call(client, "start_service")
        report["url"] = status["url"]
        sources = (await call(client, "list_sources"))["sources"]
        report["source_defaults"] = {source["id"]: source["default_limits"] for source in sources}
        for limits in report["source_defaults"].values():
            assert limits["max_items_per_query"] > 0 and limits["max_pages_per_query"] > 0
        auth = await call(client, "authorize")
        assert auth == {"authenticated": False, "user": None, "login_url": status["url"] + "/auth"}
        catalog = await client.call_tool("list_companies", {})
        assert catalog.isError and "401" in str(catalog.content) and "/auth" in str(catalog.content)
        report["catalog_requires_login"] = True
        filters = await client.call_tool("get_company_filters", {})
        assert filters.isError and "401" in str(filters.content)
        countries = (await call(client, "list_countries"))["countries"]
        assert len(countries) == 249 and {"code": "DE", "name": "Germany"} in countries
        cities = (await call(client, "find_cities", {"country": "RU", "text": "Москва"}))["cities"]
        city = cities[0]
        assert city["geoname_id"] == 524901 and city["ascii_name"] == "Moscow"
        report["geography"] = {"countries": len(countries), "selected": city}
        report["company_filters_require_login"] = True
        created = await call(
            client,
            "create_brief",
            {
                "brief": {
                    "name": "Проверка плагина · Python",
                    "text": "Python-разработчик",
                    "queries": ["Python"],
                    "filters": {
                        "locations": [
                            {
                                "country": city["country"],
                                "geoname_id": city["geoname_id"],
                                "city_display_name": city["name"],
                                "minimum_work_format": "hybrid",
                                "relocation_support": True,
                            }
                        ]
                    },
                }
            },
        )
        brief_id = created["brief"]["id"]
        saved_place = created["brief"]["filters"]["locations"][0]
        assert saved_place["city"] == "Moscow" and saved_place["city_display_name"] == "Москва"
        assert saved_place["country_name_en"] == "Russia" and saved_place["geoname_id"] == 524901
        assert saved_place["minimum_work_format"] == "hybrid" and saved_place["relocation_support"] is True
        report["brief_id"] = brief_id
        country_brief = await call(
            client,
            "create_brief",
            {
                "brief": {
                    "name": "Проверка всей страны",
                    "filters": {"locations": [{"country": "RU", "scope": "country"}]},
                }
            },
        )
        country_id = country_brief["brief"]["id"]
        country_place = country_brief["brief"]["filters"]["locations"][0]
        assert country_place["scope"] == "country" and country_place["country_name_en"] == "Russia"
        assert country_place.get("geoname_id") is None and country_place.get("city") is None
        report["country_scope"] = country_place
        if args.live:
            started = await call(
                client,
                "start_search",
                {
                    "search": {
                        "brief_id": brief_id,
                        "sources": ["hh_ru", "getmatch"],
                        "limits": {
                            source: {"max_items_per_query": 3, "max_pages_per_query": 1}
                            for source in ("hh_ru", "getmatch")
                        },
                        "company_profiles": True,
                    }
                },
            )
            run_id = started["run"]["id"]
            for _ in range(100):
                run_state = (await call(client, "get_run", {"run_id": run_id}))["run"]
                if run_state["status"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(3)
            else:
                await call(client, "cancel_search", {"run_id": run_id})
                raise TimeoutError("Live search exceeded 300 seconds")
            results = (await call(client, "get_results", {"run_id": run_id}))["results"]
            assert run_state["status"] in {"complete", "partial"}, run_state
            assert results["items"], "Search returned no results"
            report["run"] = run_state
            report["result_count"] = results["total"]
        report["stopped"] = await call(client, "stop_service")
        assert report["stopped"]["status"] == "stopped"
        restarted = await call(client, "start_service")
        assert restarted["instance_id"] != status["instance_id"]
        briefs = (await call(client, "list_briefs"))["briefs"]
        assert any(b["id"] == brief_id for b in briefs)
        reread = next(b for b in briefs if b["id"] == brief_id)
        assert reread["filters"]["locations"] == [saved_place]
        assert next(b for b in briefs if b["id"] == country_id)["filters"]["locations"] == [country_place]
        if args.live:
            after = (await call(client, "get_results", {"run_id": run_id}))["results"]
            assert after == results
        report["persisted_after_restart"] = True
    # The MCP client has exited; the service must still serve the browser.
    async with httpx.AsyncClient(trust_env=False) as http:
        assert (await http.get(f"{report['url']}/api/v1/health")).status_code == 200
        html = await http.get(report["url"])
        assert "stopService" in html.text and "authLink" in html.text
        login = await http.get(report["url"] + "/auth")
        script = await http.get(report["url"] + "/assets/auth.js")
        assert login.status_code == 200 and "authProviders" in login.text
        assert script.status_code == 200 and "/api/v1/auth" in script.text
    report["survives_mcp_disconnect"] = True
    if not args.keep_running:
        async with connection(plugin, data, cache, port, False) as client:
            await call(client, "stop_service")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cold", action="store_true", help="No uv or Python on the child PATH")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--keep-running", action="store_true", help="Leave the test service for browser checks"
    )
    asyncio.run(run(parser.parse_args()))
