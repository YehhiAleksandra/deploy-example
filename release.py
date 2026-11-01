#!/usr/bin/env python3
"""Release helper: build a checksummed artifact, deploy it into a release directory,
switch a `current` pointer atomically and roll back to the previous release.

Demo scope: local filesystem targets only. The production version adds an SSH/rsync
transport, CI-run attestation and a health check against the live domain.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

RELEASES = "releases"
CURRENT = "current"
PREVIOUS = "previous"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(source: Path, output: Path, version: str) -> dict:
    if not (source / "index.html").is_file():
        raise SystemExit(f"{source} has no index.html")
    output.mkdir(parents=True, exist_ok=True)
    name = f"site-{version}.tar"
    artifact = output / name
    with tarfile.open(artifact, "w") as archive:
        archive.add(source, arcname="public")
    digest = sha256_file(artifact)
    (output / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
    return {"artifact": str(artifact), "sha256": digest, "version": version}


def verify(artifact: Path, digest: str) -> None:
    actual = sha256_file(artifact)
    if actual != digest:
        raise SystemExit(f"checksum mismatch: expected {digest}, got {actual}")


def extract(artifact: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(artifact) as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise SystemExit(f"unsafe path in archive: {member.name}")
            if member.issym() or member.islnk():
                raise SystemExit(f"links are not allowed in a release: {member.name}")
        archive.extractall(destination, filter="data")


def read_pointer(target: Path, name: str) -> str | None:
    path = target / f".{name}"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


def write_pointer(target: Path, name: str, value: str | None) -> None:
    path = target / f".{name}"
    if value is None:
        path.unlink(missing_ok=True)
        return
    temporary = target / f".{name}.tmp"
    temporary.write_text(value + "\n", encoding="utf-8")
    os.replace(temporary, path)


def resolve_current(target: Path) -> Path | None:
    link = target / CURRENT
    if link.is_symlink():
        resolved = link.resolve()
        if resolved.is_dir():
            return resolved
    pointer = read_pointer(target, CURRENT)
    if pointer:
        return target / RELEASES / pointer
    return None


def switch(target: Path, release: Path) -> None:
    write_pointer(target, CURRENT, release.name)
    link = target / CURRENT
    temporary = target / f".{CURRENT}.tmp"
    try:
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        os.symlink(os.path.relpath(release, target), temporary, target_is_directory=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        os.replace(temporary, link)
    except OSError:
        # Windows without symlink privilege: the pointer file stays authoritative.
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def prune(target: Path, keep: int, current: str | None) -> list[str]:
    releases = sorted(
        (path for path in (target / RELEASES).iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for release in releases[keep:]:
        if release.name == current:
            continue
        shutil.rmtree(release)
        removed.append(release.name)
    return removed


def deploy(args) -> dict:
    artifact = Path(args.artifact)
    target = Path(args.target)
    target.mkdir(parents=True, exist_ok=True)
    (target / RELEASES).mkdir(exist_ok=True)
    digest = args.sha256 or ""
    if not digest:
        checksum_file = artifact.with_name(artifact.name + ".sha256")
        if not checksum_file.is_file():
            raise SystemExit("pass --sha256 or keep <artifact>.sha256 next to the artifact")
        digest = checksum_file.read_text(encoding="utf-8").split()[0]
    verify(artifact, digest)

    version = args.version or artifact.stem.replace("site-", "")
    destination = target / RELEASES / version
    if destination.exists():
        raise SystemExit(f"release {version} already exists")

    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=target))
    try:
        extract(artifact, staging)
        if args.health_file and not (staging / args.health_file).is_file():
            raise SystemExit(f"health check failed: {args.health_file} is missing")
        (staging / "RELEASE.json").write_text(
            json.dumps({"version": version, "sha256": digest}, indent=2),
            encoding="utf-8",
        )
        staging.rename(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    old = resolve_current(target)
    write_pointer(target, PREVIOUS, old.name if old else None)
    switch(target, destination)
    removed = prune(target, args.keep, version)
    return {
        "status": "success",
        "version": version,
        "current": str(destination),
        "previous": old.name if old else None,
        "pruned": removed,
    }


def rollback(args) -> dict:
    target = Path(args.target)
    current = resolve_current(target)
    previous_name = read_pointer(target, PREVIOUS)
    if not previous_name:
        raise SystemExit("no previous release to roll back to")
    previous = target / RELEASES / previous_name
    if not previous.is_dir():
        raise SystemExit(f"previous release {previous_name} is missing")
    write_pointer(target, PREVIOUS, current.name if current else None)
    switch(target, previous)
    return {"status": "success", "current": str(previous)}


def status(args) -> dict:
    target = Path(args.target)
    current = resolve_current(target)
    releases = sorted(
        path.name for path in (target / RELEASES).iterdir() if path.is_dir()
    )
    return {
        "current": current.name if current else None,
        "previous": read_pointer(target, PREVIOUS),
        "releases": releases,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build_cmd = commands.add_parser("build")
    build_cmd.add_argument("--source", default="example/site")
    build_cmd.add_argument("--output", default="build")
    build_cmd.add_argument("--version", required=True)

    deploy_cmd = commands.add_parser("deploy")
    deploy_cmd.add_argument("--target", required=True)
    deploy_cmd.add_argument("--artifact", required=True)
    deploy_cmd.add_argument("--sha256", default="")
    deploy_cmd.add_argument("--version", default="")
    deploy_cmd.add_argument("--keep", type=int, default=3)
    deploy_cmd.add_argument("--health-file", default="public/index.html")

    rollback_cmd = commands.add_parser("rollback")
    rollback_cmd.add_argument("--target", required=True)

    status_cmd = commands.add_parser("status")
    status_cmd.add_argument("--target", required=True)

    args = parser.parse_args(argv)
    if args.command == "build":
        result = build(Path(args.source), Path(args.output), args.version)
    elif args.command == "deploy":
        result = deploy(args)
    elif args.command == "rollback":
        result = rollback(args)
    else:
        result = status(args)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
