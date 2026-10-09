import asyncio
import logging
import sys

from src.core.logger import JsonFormatter, TextFormatter


async def test_task_group_logs_nested_failure_stack_without_messages():
    async def broken_parser():
        try:
            raise ValueError("PRIVATE_SOURCE_BODY")
        except ValueError as exc:
            raise RuntimeError("PRIVATE_URL") from exc

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(broken_parser())
    except ExceptionGroup:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "source.failed", (), sys.exc_info())
    else:
        raise AssertionError("Expected TaskGroup failure")
    for formatter in (TextFormatter(), JsonFormatter()):
        output = formatter.format(record)
        assert all(
            text in output for text in ("ExceptionGroup", "RuntimeError", "ValueError", "broken_parser")
        )
        assert "PRIVATE" not in output


def test_exception_chain_cycle_is_finite():
    first, second = ValueError("PRIVATE_ONE"), RuntimeError("PRIVATE_TWO")
    first.__cause__, second.__cause__ = second, first
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failure", (), (type(first), first, None))
    output = JsonFormatter().format(record)
    assert "ValueError" in output and "RuntimeError" in output
    assert "PRIVATE" not in output
