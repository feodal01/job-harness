"""Deterministic HH and Getmatch built from real saved pages, with simulated response latency.

Every response body is a real fixture with substituted identities, so parsing costs match live pages.
The same request always returns the same data; phrases overlap by a fixed share of vacancies.
"""

import asyncio
import copy
import html
import json
import zlib
from pathlib import Path
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Mean live HTTP time per stage, from reports/live-hh-final.json and reports/getmatch-feed-check.json.
LATENCY = {
    ("hh.ru", "listing"): 0.98,
    ("hh.ru", "detail"): 0.76,
    ("hh.ru", "company"): 0.93,
    ("api.hh.ru", "geography"): 0.2,
    ("getmatch.ru", "listing"): 0.76,
    ("getmatch.ru", "detail"): 0.51,
}
HH_PAGE_SIZE = 50  # Live fixture success.html: 50 vacancies per page.
HH_PAGES = 5  # Pages available for every phrase and area.
HH_SHARED_SHARE = 3  # Of every 10 positions, this many are the same vacancy for all phrases.
HH_EMPLOYERS = 40
HH_DETAIL_EMPLOYER = 12192262  # Employer id inside live-detail-137520939.html.
GETMATCH_TOTAL = 921  # meta.total of the live feed on 2026-09-28.
GETMATCH_PROMOS = 2  # Live first page carries two promo offers beyond the page size.


def _crc(value: str) -> int:
    return zlib.crc32(value.encode())


class FakeSources:
    def __init__(self, latency_scale: float = 1.0):
        self.latency_scale = latency_scale
        page = (FIXTURES / "success.html").read_text()
        marker = page.find('id="HH-Lux-InitialState"')
        start = page.find(">", marker) + 1
        end = page.find("</template>", start)
        self.hh_prefix, self.hh_suffix = page[:start], page[end:]
        self.hh_state = json.loads(html.unescape(page[start:end]))
        self.hh_items = self.hh_state["vacancySearchResult"]["vacancies"]
        self.hh_detail = (FIXTURES / "live-detail-137520939.html").read_text()
        self.hh_employer = (FIXTURES / "employer_profile_official_site.html").read_text()
        self.hh_countries = (FIXTURES / "hh_countries.json").read_text()
        self.hh_cities = (FIXTURES / "hh_city_suggestions.json").read_text()
        self.hh_city_names = {c["id"]: c["text"] for c in json.loads(self.hh_cities)["items"]}
        # The detail page repeats the listing's city and employer, as on the live site.
        self.hh_vacancy_city: dict[str, str] = {}
        self.hh_vacancy_employer: dict[str, int] = {}
        offers = []
        for name in ("feed_page1", "feed_page2", "feed_last"):
            offers += json.loads((FIXTURES / "getmatch" / f"{name}.json").read_text())["offers"]
        self.gm_offers = [o for o in offers if o.get("offer_type") != "one_day_offer_v3"]
        self.gm_promos = [o for o in offers if o.get("offer_type") == "one_day_offer_v3"]
        self.gm_detail = (FIXTURES / "getmatch" / "detail-34499.html").read_text()
        self.gm_promo_detail = (FIXTURES / "getmatch" / "detail-36107.html").read_text()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        params = {k: v for k, v in parse_qs(request.url.query.decode()).items()}
        if host == "api.hh.ru":
            stage, body = "geography", self.hh_countries if path == "/areas/countries" else self.hh_cities
        elif host == "hh.ru" and path == "/search/vacancy":
            stage, body = "listing", self._hh_listing(params)
        elif host == "hh.ru" and path.startswith("/vacancy/"):
            vacancy_id = path.rsplit("/", 1)[1]
            city = self.hh_vacancy_city.get(vacancy_id, "Ижевск")
            employer = str(self.hh_vacancy_employer.get(vacancy_id, HH_DETAIL_EMPLOYER))
            body = self.hh_detail.replace("137520939", vacancy_id).replace("Ижевск", city)
            stage, body = "detail", body.replace(str(HH_DETAIL_EMPLOYER), employer)
        elif host == "hh.ru" and path.startswith("/employer/"):
            stage, body = "company", self.hh_employer
        elif host == "getmatch.ru" and path == "/api/offers":
            stage, body = "listing", self._gm_listing(params)
        elif host == "getmatch.ru" and path.startswith("/vacancies/"):
            stage, body = "detail", self._gm_detail(path)
        else:
            return httpx.Response(404)
        await asyncio.sleep(LATENCY[(host, stage)] * self.latency_scale)
        return httpx.Response(200, text=body)

    def _hh_listing(self, params: dict[str, list[str]]) -> str:
        text, area = params["text"][0], params.get("area", ["0"])[0]
        page = int(params.get("page", ["0"])[0])
        vacancies = []
        for position in range(page * HH_PAGE_SIZE, (page + 1) * HH_PAGE_SIZE):
            if position % 10 < HH_SHARED_SHARE:
                vacancy_id = 10_000_000 + (_crc(area) % 1000) * 1000 + position
            else:
                vacancy_id = 20_000_000 + (_crc(text + area) % 10_000) * 1000 + position
            item = copy.deepcopy(self.hh_items[vacancy_id % len(self.hh_items)])
            item["vacancyId"] = vacancy_id
            item["company"]["id"] = 5_000_000 + vacancy_id % HH_EMPLOYERS
            if area in self.hh_city_names:  # A city search returns vacancies of that city.
                item["area"] = {"@id": int(area), "name": self.hh_city_names[area], "path": f".113.{area}."}
            self.hh_vacancy_city[str(vacancy_id)] = item["area"]["name"]
            self.hh_vacancy_employer[str(vacancy_id)] = item["company"]["id"]
            vacancies.append(item)
        state = dict(self.hh_state)
        state["vacancySearchResult"] = {
            **self.hh_state["vacancySearchResult"],
            "vacancies": vacancies,
            "totalResults": HH_PAGES * HH_PAGE_SIZE,
            "paging": {"next": {"page": page + 1, "disabled": page + 1 >= HH_PAGES}},
        }
        return self.hh_prefix + html.escape(json.dumps(state, ensure_ascii=False)) + self.hh_suffix

    def _gm_listing(self, params: dict[str, list[str]]) -> str:
        offset, limit = int(params["offset"][0]), int(params["limit"][0])
        regions = params.get("l", [])
        total = GETMATCH_TOTAL if not regions else max(50, GETMATCH_TOTAL * len(regions) // 5)
        offers = []
        if offset == 0:
            for n, template in enumerate(self.gm_promos[:GETMATCH_PROMOS]):
                offers.append(self._gm_offer(template, 90_000 + n, "one_day_offer_v3"))
        for position in range(offset, min(offset + limit, total)):
            template = self.gm_offers[position % len(self.gm_offers)]
            offers.append(self._gm_offer(template, 50_000 + position, "vacancy"))
        return json.dumps({"offers": offers, "meta": {"total": total, "offset": offset, "limit": limit}})

    @staticmethod
    def _gm_offer(template: dict, offer_id: int, offer_type: str) -> dict:
        offer = copy.deepcopy(template)
        offer.update(id=offer_id, url=f"/vacancies/{offer_id}-vacancy", offer_type=offer_type)
        return offer

    def _gm_detail(self, path: str) -> str:
        offer_id = path.split("/")[2].split("-")[0]
        if int(offer_id) >= 90_000:
            return self.gm_promo_detail.replace("36107", offer_id)
        return self.gm_detail.replace("34499", offer_id)
