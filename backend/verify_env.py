from __future__ import annotations

import argparse
import importlib.metadata as metadata
import platform
import sys
from pathlib import Path

TARGET_PYTHON = "3.12.4"
CORE_PACKAGES = {
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-core",
    "python-multipart",
    "starlette",
    "anyio",
    "idna",
    "typing-extensions",
    "annotated-types",
    "click",
    "h11",
    "python-dotenv",
    "colorama",
    "pyyaml",
    "numpy",
    "pillow",
    "tifffile",
    "psutil",
    "pyinstaller",
    "altgraph",
    "pefile",
    "pyinstaller-hooks-contrib",
    "pywin32-ctypes",
    "setuptools",
}


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
    parser.add_argument(
        "--profile",
        choices=["full", "core"],
        default="full",
        help="full checks exact locked versions; core only checks packages needed to start the local system.",
    )
    args = parser.parse_args()

    expected = parse_lock(Path(args.lock))
    if args.profile == "core":
        expected = {
            package: wanted
            for package, wanted in expected.items()
            if package in CORE_PACKAGES
        }

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
        if args.profile == "core":
            if actual == "<missing>":
                errors.append(f"{package} expected installed, actual <missing>")
        elif actual != wanted:
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
        if args.profile == "core":
            print("\n[OK] Core backend environment is ready")
        else:
            print("\n[OK] Environment versions match requirements.lock.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
