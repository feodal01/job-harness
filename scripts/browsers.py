#!/usr/bin/env python3
"""Install rebrowser-playwright browsers."""

import subprocess
import sys


def main():
    print("Installing Chromium for rebrowser-playwright...")
    result = subprocess.run(
        [sys.executable, "-m", "rebrowser_playwright", "install", "chromium"],
        check=False,
    )
    if result.returncode == 0:
        print("Chromium installed successfully.")
    else:
        print(f"Failed to install Chromium (exit code {result.returncode})")
        sys.exit(1)


if __name__ == "__main__":
    main()
