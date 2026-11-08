import builtins
import json
from collections.abc import Iterable
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from src.schemas.companies import (
    CompanyFilterField,
    CompanyFilterOption,
    CompanyFiltersPage,
    CompanyLink,
    CompanyLinksPage,
)
from src.services.auth import AuthError, AuthService, LoginRequired

_companies = TypeAdapter(list[CompanyLink])
_filter_options = TypeAdapter(list[CompanyFilterOption])


class CompanyCatalog:
    def __init__(self, auth: AuthService) -> None:
        self.auth = auth

    async def list(
        self,
        page: int,
        page_size: int,
        name: str | None = None,
        category: str | None = None,
        specialization: str | None = None,
    ) -> CompanyLinksPage:
        params = {
            "select": "id,name,url,link_kind,provider,categories,specialization",
            "order": "name.asc,id.asc",
        }
        for column, value in (("name", name), ("specialization", specialization)):
            if value:
                params[column] = "ilike.%" + _like(value) + "%"
        if category:
            params["categories"] = "cs.{" + json.dumps(category, ensure_ascii=False) + "}"
        items, total = await self._get_page("/rest/v1/company_links", params, _companies, page, page_size)
        return CompanyLinksPage(items=items, page=page, page_size=page_size, total=total)

    async def rows(
        self,
        *,
        name: str | None = None,
        ids: Iterable[str] | None = None,
        providers: Iterable[str] | None = None,
        page_size: int = 1000,
        max_rows: int = 20000,
    ) -> builtins.list[CompanyLink]:
        """Every catalog row matching the filters, read now with the user's session; nothing is kept."""
        params = {"select": "id,name,url,link_kind,provider,categories,specialization", "order": "id.asc"}
        if name:
            params["name"] = "ilike.%" + _like(name) + "%"
        if ids is not None:
            params["id"] = "in.(" + ",".join(str(UUID(value)) for value in ids) + ")"
        if providers is not None:
            params["provider"] = "in.(" + ",".join(_quoted(value) for value in providers) + ")"
        if params.get("id") == "in.()" or params.get("provider") == "in.()":
            return []
        rows: builtins.list[CompanyLink] = []
        while True:
            # Offsets follow the rows received: Supabase may return fewer rows than asked for.
            items, total = await self._get_range(
                "/rest/v1/company_links", params, _companies, len(rows), page_size
            )
            if total > max_rows:
                raise AuthError("Каталог компаний слишком большой для одного запроса. Сузьте условия.")
            rows.extend(items)
            if not items or len(rows) >= total:
                return rows

    async def filters(
        self,
        field: CompanyFilterField,
        page: int,
        page_size: int,
        category: str | None = None,
        query: str | None = None,
    ) -> CompanyFiltersPage:
        params = {"p_field": field, "order": "link_count.desc,value.asc"}
        if category is not None:
            params["p_category"] = category
        if query is not None:
            params["p_query"] = query
        items, total = await self._get_page(
            "/rest/v1/rpc/company_filter_options", params, _filter_options, page, page_size
        )
        return CompanyFiltersPage(field=field, items=items, page=page, page_size=page_size, total=total)

    async def _get_page[T](
        self,
        path: str,
        params: dict[str, str],
        adapter: TypeAdapter[builtins.list[T]],
        page: int,
        page_size: int,
    ) -> tuple[builtins.list[T], int]:
        return await self._get_range(path, params, adapter, (page - 1) * page_size, page_size)

    async def _get_range[T](
        self,
        path: str,
        params: dict[str, str],
        adapter: TypeAdapter[builtins.list[T]],
        offset: int,
        limit: int,
    ) -> tuple[builtins.list[T], int]:
        token = await self.auth.access_token()
        response = await self.auth.request(
            "GET",
            path,
            params={**params, "offset": str(offset), "limit": str(limit)},
            headers={
                "Authorization": "Bearer " + token,
                "Prefer": "count=exact",
            },
        )
        if response.status_code == 401:
            await self.auth.invalidate(token)
            raise LoginRequired(self.auth.login_url)
        if response.status_code == 403:
            raise AuthError("У этого аккаунта нет доступа к каталогу компаний.", 403)
        if response.status_code not in {200, 206, 416}:
            raise AuthError("Не удалось получить каталог компаний. Повторите запрос позже.")
        try:
            total = int(response.headers["content-range"].rsplit("/", 1)[1])
            if total < 0:
                raise ValueError
            if response.status_code == 416:
                # PostgREST reports offsets beyond the last row as PGRST103, with the exact count.
                error = response.json()
                if not isinstance(error, dict) or error.get("code") != "PGRST103" or offset < total:
                    raise ValueError
                items: builtins.list[T] = []
            else:
                items = adapter.validate_json(response.content)
            return items, total
        except (ValueError, ValidationError, KeyError, IndexError):
            raise AuthError("Supabase вернул некорректный каталог компаний.") from None


def _like(value: str) -> str:
    """A PostgREST ilike literal that matches the text itself, not its wildcards."""
    literal = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return literal.replace("*", "\\*")


def _quoted(value: str) -> str:
    """One PostgREST `in` list item; quotes keep commas and parentheses inside the value."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
