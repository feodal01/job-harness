"""Block pages that HTTP status alone does not reveal (spec 6.2)."""

from pathlib import Path

import httpx
import pytest

from src.parsers.aggregators.hh.parser import classify as hh_classify
from src.parsers.blocking import classify_common
from src.parsers.interfaces import ResponseInfo
from tests.test_engine_runs import Recorder, brief, run, service

FIXTURES = Path(__file__).parent / "fixtures"
VPN_CHECK = "https://omsk.hh.ru/vpncheeck?backUrl=%2Fsearch%2Fvacancy"  # Observed live by job-harness.


def info(url="https://hh.ru/search/vacancy", status=200, text="", **headers):
    return ResponseInfo(url, status, headers, text)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (info(status=302, location=VPN_CHECK), "vpn_blocked"),
        (info(status=302, location="/vpncheck?backUrl=%2F"), "vpn_blocked"),
        (info(url=VPN_CHECK), "vpn_blocked"),
        (info(url="https://hh.ru/captcha?x=1"), "captcha"),
        (info(text="<html><head><title>Доступ ограничен</title></head></html>"), "blocked"),
        (info(text="<title>Just a moment...</title>"), "blocked"),
        (info(text="<title>reCAPTCHA</title>"), "captcha"),
        (info(status=302, location="https://hh.ru/search/vacancy?page=1"), None),
        (info(text="<title>Работа QA в Москве</title>"), None),
    ],
)
def test_common_block_signals(response, expected):
    assert classify_common(response) == expected


def test_normal_hh_pages_mentioning_vpn_in_interface_strings_are_not_blocked():
    # Every HH page carries "Проверь, что ты в офисе или у тебя включен VPN" and a vpn_detection flag.
    for name in ("success", "pagination", "no_results", "live-detail-137520939"):
        assert hh_classify(info(text=(FIXTURES / f"{name}.html").read_text())) is None
    assert hh_classify(info(text=(FIXTURES / "blocked.html").read_text())) == "captcha"


async def test_vpn_redirect_blocks_hh_for_the_run_with_a_hint_and_others_continue(tmp_path):
    fake = Recorder({"QA": [1]})

    async def handler(request):
        if request.url.host.endswith("hh.ru"):
            fake.calls.append(request)
            return httpx.Response(302, headers={"location": VPN_CHECK})
        return await fake(request)

    async with service(tmp_path, handler) as (app, client):
        b = await brief(client, ["QA"])
        result = await run(app, client, b["id"], sources=("hh_ru", "getmatch"), company_profiles=False)
    hh = result["sources"]["hh_ru"]
    assert result["status"] == "partial"
    assert hh["status"] == "blocked" and hh["blocked_reason"] == "vpn_blocked"
    assert "VPN" in hh["hint"]
    assert result["sources"]["getmatch"]["status"] == "complete"
    # The redirect is not followed and nothing else is sent to HH.
    assert [str(r.url) for r in fake.calls if r.url.host.endswith("hh.ru")] == [
        "https://hh.ru/search/vacancy?text=QA&page=0"
    ]


async def test_http_451_blocks_the_source(tmp_path):
    async def handler(request):
        return httpx.Response(451)

    async with service(tmp_path, handler) as (app, client):
        b = await brief(client, ["QA"])
        result = await run(app, client, b["id"], company_profiles=False)
    assert result["sources"]["hh_ru"]["status"] == "blocked"
    assert result["sources"]["hh_ru"]["blocked_reason"] == "blocked"
