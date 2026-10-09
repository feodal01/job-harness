import json

import pytest

from benchmarks.run import SCENARIOS, execute


@pytest.mark.parametrize("scenario_id", ["hh-qa-moscow", "getmatch-feed"])
async def test_overhead_benchmark_runs_scenario_on_simulated_sources(tmp_path, scenario_id):
    scenario = next(s for s in json.loads(SCENARIOS.read_text()) if s["id"] == scenario_id)
    metrics, raw = await execute(scenario, "overhead", tmp_path)

    assert raw["run"]["status"] == "complete"
    assert metrics["requests"] > 0 and metrics["unique_vacancies"] > 0
    assert 0 <= metrics["first_vacancy_s"] <= metrics["first_card_s"] <= metrics["wall_s"]
    for source in metrics["sources"].values():
        assert source["errors"] == 0
        assert set(source["http_statuses"]) == {200}
