"""Start or reuse the same persistent service as the MCP plugin."""

import asyncio

from src.runtime.manager import ServiceError, ServiceManager


async def main() -> None:
    try:
        status = await ServiceManager().start()
    except ServiceError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Сервис запущен: {status.url}")
    print("Остановка — кнопкой в интерфейсе или через MCP stop_service.")


if __name__ == "__main__":
    asyncio.run(main())
