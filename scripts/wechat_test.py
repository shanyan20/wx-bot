"""Reproducible offline suite; optional read-only profile probe in a timed subprocess."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-config", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/wechat-tests")
    args = parser.parse_args()
    folder = args.output.resolve() / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    steps = [
        ("pytest", ["-m", "pytest", "-q", f"--junitxml={folder / 'pytest.xml'}"]),
        ("ruff", ["-m", "ruff", "check", "."]),
        ("dependencies", ["-m", "pip", "check"]),
        ("smoke", ["scripts/smoke.py"]),
    ]
    if args.probe_config:
        steps.append(("readonly_probe", ["scripts/wechat_probe.py", "--config",
                                         str(args.probe_config.resolve())]))
    summary = {"started_at": datetime.now(UTC).isoformat(), "python": sys.version,
               "live_send": "not_executed", "checks": []}
    failed = False
    for name, command in steps:
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, *command], cwd=ROOT,
                                    capture_output=True, text=True, encoding="utf-8",
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                    errors="replace",
                                    timeout=60 if name == "readonly_probe" else 180)
            code, output = result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            code, output = 124, "Subprocess timed out and was terminated.\n"
        (folder / f"{name}.txt").write_text(output, encoding="utf-8")
        summary["checks"].append({"name": name, "exit_code": code,
                                  "seconds": round(time.monotonic() - started, 3)})
        (folder / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{name}: exit={code}", flush=True)
        failed |= code != 0
    print(f"Reports: {folder}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
