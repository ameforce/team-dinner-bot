# -*- coding: utf-8 -*-
"""Run L1 + L2 in a loop until all pass (max attempts)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
MAX_ATTEMPTS = 5


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def main() -> int:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n========== Attempt {attempt}/{MAX_ATTEMPTS} ==========\n")

        code, out = run(
            [str(PY), "-B", "-m", "pytest", "tests/", "-q", "--tb=line", "-p", "no:cacheprovider"]
        )
        print(out)
        if code != 0:
            print("L1 FAILED")
            continue

        print("L1 OK - running L2...")
        code2, out2 = run([str(PY), "-B", "scripts/run_scenario_tests.py"])
        print(out2)
        if code2 != 0:
            print("L2 FAILED")
            continue

        print("\n*** ALL AUTOMATED SCENARIOS GREEN ***\n")
        return 0

    print(f"\nFailed after {MAX_ATTEMPTS} attempts.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
