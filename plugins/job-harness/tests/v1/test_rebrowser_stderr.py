from __future__ import annotations

import unittest

from job_harness.v1.rebrowser_stderr import filter_rebrowser_stderr_lines


class RebrowserStderrFilterTest(unittest.TestCase):
    def test_filter_removes_known_frame_context_error_block(self) -> None:
        lines = [
            b"[worker 1] [1/2] Alpha\n",
            (
                b"[rebrowser-patches][frames._context] cannot get world, error: "
                b"ProtocolError: Protocol error (Runtime.evaluate): Cannot find context with specified id\n"
            ),
            b"    at /tmp/rebrowser_playwright/driver/package/lib/server/chromium/crConnection.js:116:57\n",
            b"    at async Frame.evaluateExpression (/tmp/rebrowser_playwright/driver/package/lib/server/frames.js:645:21) {\n",
            b"  type: 'error',\n",
            b"  method: 'Runtime.evaluate',\n",
            b"  logs: undefined\n",
            b"}\n",
            b"[worker 2] [2/2] Beta\n",
        ]

        filtered = filter_rebrowser_stderr_lines(lines)

        self.assertEqual(
            [
                b"[worker 1] [1/2] Alpha\n",
                b"[worker 2] [2/2] Beta\n",
            ],
            filtered,
        )

    def test_filter_keeps_other_rebrowser_errors(self) -> None:
        lines = [
            b"[rebrowser-patches][other] real warning\n",
            b"Error: browser crashed\n",
        ]

        filtered = filter_rebrowser_stderr_lines(lines)

        self.assertEqual(lines, filtered)

    def test_filter_recovers_when_unexpected_line_interrupts_block(self) -> None:
        lines = [
            b"[rebrowser-patches][frames._context] cannot get world, error: ProtocolError\n",
            b"plain stderr from another writer\n",
            b"  type: 'error',\n",
        ]

        filtered = filter_rebrowser_stderr_lines(lines)

        self.assertEqual(
            [
                b"plain stderr from another writer\n",
                b"  type: 'error',\n",
            ],
            filtered,
        )


if __name__ == "__main__":
    unittest.main()
