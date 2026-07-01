from __future__ import annotations

import argparse
import importlib.metadata as metadata
import platform
import sys
from pathlib import Path

TARGET_PYTHON = "3.12.4"


def parse_lock(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        result[name.strip().lower().replace("_", "-")] = version.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    expected = parse_lock(Path(args.lock))
    errors: list[str] = []
    py_version = platform.python_version()
    if py_version != TARGET_PYTHON:
        errors.append(f"python expected {TARGET_PYTHON}, actual {py_version}")

    rows: list[tuple[str, str, str]] = []
    for package, wanted in expected.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError:
            actual = "<missing>"
        rows.append((package, wanted, actual))
        if actual != wanted:
            errors.append(f"{package} expected {wanted}, actual {actual}")

    if not args.quiet:
        print(f"python=={py_version}")
        for package, _wanted, actual in rows:
            print(f"{package}=={actual}")

    if errors:
        print("\n[ENV-ERROR] Environment mismatch:", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("\n[OK] Environment versions match requirements.lock.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
