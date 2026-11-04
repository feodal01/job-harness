"""Общие HTTP-ошибки для документации операций."""

from typing import Any

from src.schemas.errors import ErrorResponse, ValidationErrorResponse


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        401: "Для доступа к каталогу требуется вход.",
        403: "Изменение данных с другого origin запрещено.",
        404: "Бриф, запуск или вакансия не найдены.",
        409: "Операция конфликтует с текущим состоянием приложения.",
        422: "Ошибка проверки HTTP-параметров либо неподходящие параметры поиска.",
        429: "Слишком много незавершённых попыток входа.",
        502: "Ошибка связи с Supabase или некорректный ответ.",
        503: "Не удалось прочитать или сохранить локальную сессию.",
        500: "Внутренняя ошибка приложения.",
    }
    return {
        status: {
            "model": ErrorResponse | ValidationErrorResponse if status == 422 else ErrorResponse,
            "description": descriptions[status],
        }
        for status in (*statuses, 500)
    }
