#!/usr/bin/env python3
"""Build the Lambda deployment packages.

    build/core.zip        the webhook_platform package. Pure Python, no dependencies
                          beyond the boto3 the runtime provides. Architecture-independent.
    build/compactor.zip   core plus pyarrow for one architecture. Used by the compactor.

The zips are reproducible: entries are sorted, timestamps and permissions are fixed,
and pyarrow is installed from the version pinned in uv.lock. The same source and
lock file give the same bytes, so `terraform plan` shows a change only when the code
changed.

Usage: build_lambdas.py [--arch arm64|x86_64] [--out build]
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "webhook_platform"
PYTHON_VERSION = "3.12"
# Lambda's python3.12 runtime is Amazon Linux 2023 (glibc 2.34); manylinux_2_28 wheels load on it.
PLATFORMS = {"arm64": "aarch64-manylinux_2_28", "x86_64": "x86_64-manylinux_2_28"}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
# Shipped by the wheel, never imported at runtime.
PRUNE_DIRS = {"__pycache__", "tests", "include"}
PRUNE_SUFFIXES = (".pyc", ".pyx", ".pxd", ".h", ".cc", ".cmake")
# pyarrow components the compactor never loads: Arrow Flight (gRPC) and Arrow's own
# S3 filesystem (the handler uses boto3). libarrow_substrait looks unused too but
# pyarrow.lib links against it, so it stays. Removing them takes the zip
# from just under Lambda's 50 MB direct-upload limit to well under it. The build
# imports the pruned package inside the Lambda base image to prove it still loads
# (scripts/check_package.sh).
PRUNE_GLOBS = (
    "pyarrow/libarrow_flight.so*",
    "pyarrow/libarrow_python_flight.so*",
    "pyarrow/_flight.*",
    "pyarrow/flight.py",
    "pyarrow/libarrow_s3.so*",
    "pyarrow/_s3fs.*",
)


def host_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def collect(root: Path, prefix: str = "") -> list[tuple[str, Path]]:
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_dir() or PRUNE_DIRS.intersection(relative.parts) or path.name.endswith(PRUNE_SUFFIXES):
            continue
        if any(fnmatch.fnmatch(relative.as_posix(), pattern) for pattern in PRUNE_GLOBS):
            continue
        entries.append((f"{prefix}{relative.as_posix()}", path))
    return entries


def write_zip(target: Path, entries: list[tuple[str, Path]]) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            executable = path.suffix == ".so" or ".so." in path.name
            info.external_attr = (0o755 if executable else 0o644) << 16
            archive.writestr(info, path.read_bytes())


def install_compactor_dependencies(arch: str, target: Path) -> None:
    requirements = target.parent / "requirements-compactor.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--only-group",
            "lambda-compactor",
            "--no-emit-project",
            "--no-hashes",
            "-o",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(target),
            "--python-platform",
            PLATFORMS[arch],
            "--python-version",
            PYTHON_VERSION,
            "--only-binary",
            ":all:",
            "--no-deps",
            "-r",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def describe(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        unzipped = sum(item.file_size for item in archive.infolist())
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "unzipped_bytes": unzipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arch", choices=sorted(PLATFORMS), default=host_arch())
    parser.add_argument("--out", type=Path, default=ROOT / "build")
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    source = collect(PACKAGE, "webhook_platform/")

    core = out / "core.zip"
    write_zip(core, source)

    deps = out / f"compactor-deps-{args.arch}"
    shutil.rmtree(deps, ignore_errors=True)
    install_compactor_dependencies(args.arch, deps)
    compactor = out / "compactor.zip"
    write_zip(compactor, source + collect(deps))

    manifest = {
        "architecture": args.arch,
        "python": PYTHON_VERSION,
        "core.zip": describe(core),
        "compactor.zip": describe(compactor),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name in ("core.zip", "compactor.zip"):
        info = manifest[name]
        assert isinstance(info, dict)
        print(
            f"{name:14} {info['bytes']:>11,} bytes  sha256 {str(info['sha256'])[:16]}  "
            f"({args.arch if name != 'core.zip' else 'any'})"
        )
    # Lambda limits: 50 MB zipped for direct upload, 250 MB unzipped.
    compactor_info = manifest["compactor.zip"]
    assert isinstance(compactor_info, dict)
    if (
        int(str(compactor_info["bytes"])) > 50 * 1024 * 1024
        or int(str(compactor_info["unzipped_bytes"])) > 250 * 1024 * 1024
    ):
        print("compactor.zip exceeds a Lambda package limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
