from collections.abc import Awaitable
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from src.models.brief import BriefInput
from src.models.run import RunInput
from src.runtime.manager import ServiceError, ServiceManager
from src.schemas.service import ServiceStatus
from src.schemas.vacancies import PoolStatusInput, PoolView, SelectionView

Page = Annotated[int, Field(ge=1)]
PageSize = Annotated[int, Field(ge=1, le=100)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=100)]
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


async def perform[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except ServiceError as exc:
        raise ToolError(str(exc)) from exc
    except httpx.RequestError as exc:
        raise ToolError("Связь с сервисом потеряна. Проверьте get_service_status перед повтором.") from exc


def create_mcp(manager: ServiceManager | None = None) -> FastMCP:
    manager = manager or ServiceManager()
    mcp = FastMCP(
        "job-search",
        instructions=(
            "Локальный поиск вакансий. Сначала согласуйте бриф, затем создайте его и запустите поиск. "
            "Поиск асинхронный: используйте get_run и get_results. Все данные сохраняются. "
            "Обычные инструменты автоматически запускают сервис; get_service_status и stop_service — нет. "
            "Не выключайте сервис без просьбы пользователя. Перед cancel_active=true получите "
            "подтверждение отмены активных поисков. Сайты работодателей сохраняются, "
            "но поиск контактов и обход карьерных лент не выполняются."
        ),
        log_level="WARNING",
    )

    async def request(method: str, path: str, **kwargs: Any) -> object:
        return await perform(manager.request(method, path, **kwargs))

    @mcp.tool(annotations=READ)
    async def get_service_status() -> ServiceStatus:
        """Проверить состояние сервиса без его запуска. Вернуть адрес интерфейса и активные поиски."""
        return await perform(manager.status())

    @mcp.tool(annotations=WRITE)
    async def start_service() -> ServiceStatus:
        """Запустить сервис или использовать уже запущенный. Вернуть ссылку на веб-интерфейс."""
        return await perform(manager.start())

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
    async def stop_service(cancel_active: bool = False) -> ServiceStatus:
        """Остановить сервис. cancel_active=true допустим после подтверждения отмены поисков пользователем."""
        return await perform(manager.stop(cancel_active))

    @mcp.tool(annotations=READ)
    async def list_sources() -> dict[str, Any]:
        """Подключённые источники и возможности фильтрации. Не проверяет доступность сайтов в сети."""
        return {"sources": await request("GET", "/api/v1/sources")}

    @mcp.tool(annotations=READ)
    async def list_countries() -> dict[str, Any]:
        """Независимый локальный справочник стран для условий брифа."""
        return {"countries": await request("GET", "/api/v1/geography/countries")}

    @mcp.tool(annotations=READ)
    async def find_cities(
        country: Annotated[str, Field(min_length=2, max_length=2)],
        text: Annotated[str, Field(min_length=2, max_length=100)],
    ) -> dict[str, Any]:
        """Найти город в локальном справочнике страны перед сохранением географических условий."""
        return {
            "cities": await request(
                "GET", "/api/v1/geography/cities", params={"country": country, "text": text}
            )
        }

    @mcp.tool(annotations=READ)
    async def list_briefs() -> dict[str, Any]:
        """Сохранённые брифы, их условия, количество вакансий и последние поиски."""
        return {"briefs": await request("GET", "/api/v1/briefs")}

    @mcp.tool(annotations=WRITE)
    async def create_brief(brief: BriefInput) -> dict[str, Any]:
        """Сохранить согласованный бриф. text — исходное описание; queries и filters — условия движка."""
        return {"brief": await request("POST", "/api/v1/briefs", body=brief.model_dump(mode="json"))}

    @mcp.tool(annotations=WRITE)
    async def update_brief(brief_id: Identifier, brief: BriefInput) -> dict[str, Any]:
        """Заменить условия брифа и пересчитать отбор его вакансий. История поисков сохраняется."""
        return {
            "brief": await request("PUT", f"/api/v1/briefs/{brief_id}", body=brief.model_dump(mode="json"))
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def start_search(search: RunInput) -> dict[str, Any]:
        """Начать поиск по brief_id и явно выбранным источникам/лимитам. Вернуть run.id без ожидания конца."""
        return {"run": await request("POST", "/api/v1/runs", body=search.model_dump(mode="json"))}

    @mcp.tool(annotations=READ)
    async def list_runs(brief_id: Identifier | None = None) -> dict[str, Any]:
        """История последних 100 поисков, при необходимости для одного брифа."""
        return {
            "runs": await request("GET", "/api/v1/runs", params={"brief_id": brief_id} if brief_id else {})
        }

    @mcp.tool(annotations=READ)
    async def get_run(run_id: Identifier) -> dict[str, Any]:
        """Состояние поиска, прогресс, ошибки и статистика по источникам."""
        return {"run": await request("GET", f"/api/v1/runs/{run_id}")}

    @mcp.tool(annotations=WRITE)
    async def cancel_search(run_id: Identifier) -> dict[str, Any]:
        """Отменить один поиск по просьбе пользователя; уже найденные вакансии сохраняются."""
        return {"run": await request("POST", f"/api/v1/runs/{run_id}/cancel")}

    @mcp.tool(annotations=READ)
    async def get_results(
        run_id: Identifier, page: Page = 1, page_size: PageSize = 20, selection: SelectionView = "all"
    ) -> dict[str, Any]:
        """Страница сохранённых результатов поиска с описаниями вакансий и ссылками на работодателей."""
        return {
            "results": await request(
                "GET",
                f"/api/v1/runs/{run_id}/results",
                params={
                    "page": page,
                    "page_size": page_size,
                    "selection": selection,
                },
            )
        }

    @mcp.tool(annotations=READ)
    async def get_events(run_id: Identifier, after: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
        """Диагностика поиска: до 1000 событий после указанного ID."""
        return {"events": await request("GET", f"/api/v1/runs/{run_id}/events", params={"after": after})}

    @mcp.tool(annotations=READ)
    async def list_vacancies(
        brief_id: Identifier,
        page: Page = 1,
        page_size: PageSize = 20,
        selection: SelectionView = "candidates",
        status: PoolView = "all",
    ) -> dict[str, Any]:
        """Общая лента вакансий брифа из всех поисков с пользовательскими статусами."""
        return {
            "vacancies": await request(
                "GET",
                f"/api/v1/briefs/{brief_id}/vacancies",
                params={
                    "page": page,
                    "page_size": page_size,
                    "selection": selection,
                    "status": status,
                },
            )
        }

    @mcp.tool(annotations=WRITE)
    async def update_vacancy(
        brief_id: Identifier, vacancy_key: str, update: PoolStatusInput
    ) -> dict[str, Any]:
        """Сохранить этап и заметку вакансии по просьбе пользователя. Ничего не отправляет работодателю."""
        key = quote(vacancy_key, safe="")
        return {
            "vacancy": await request(
                "PATCH", f"/api/v1/briefs/{brief_id}/vacancies/{key}", body=update.model_dump(mode="json")
            )
        }

    return mcp
