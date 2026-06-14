#!/usr/bin/env python3
"""Generate Lunavale game release metadata and enforce release version ordering."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


@dataclass(frozen=True)
class Version:
    raw: str
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]

    @classmethod
    def parse(cls, value: str) -> "Version":
        raw = value.strip()
        if raw.startswith("v"):
            raw = raw[1:]
        match = VERSION_RE.match(raw)
        if not match:
            raise ValueError(f"Version '{value}' is not supported semantic version syntax.")
        prerelease = tuple((match.group("pre") or "").split(".")) if match.group("pre") else ()
        return cls(
            raw=raw,
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=prerelease,
        )

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def compare(self, other: "Version") -> int:
        left = (self.major, self.minor, self.patch)
        right = (other.major, other.minor, other.patch)
        if left != right:
            return -1 if left < right else 1
        return compare_prerelease(self.prerelease, other.prerelease)


def compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            left_int = int(left_part)
            right_int = int(right_part)
            return -1 if left_int < right_int else 1
        if left_numeric:
            return -1
        if right_numeric:
            return 1
        return -1 if left_part < right_part else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def run_git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo_root, text=True).strip()


def read_bundle_version(project_root: Path) -> str:
    settings = project_root / "ProjectSettings" / "ProjectSettings.asset"
    match = re.search(r"^\s*bundleVersion:\s*(\S+)\s*$", settings.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find bundleVersion in {settings}.")
    return match.group(1)


def read_editor_version(project_root: Path) -> str:
    version_file = project_root / "ProjectSettings" / "ProjectVersion.txt"
    match = re.search(r"^m_EditorVersion:\s*(\S+)\s*$", version_file.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find m_EditorVersion in {version_file}.")
    return match.group(1)


def repo_files_hash(repo_root: Path) -> str:
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo_root)
    digest = hashlib.sha256()
    for raw_path in paths.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        file_path = repo_root / path
        if not file_path.is_file():
            continue
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(file_path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def fetch_release_versions(release_repo: str, token: str) -> list[Version]:
    url = f"https://api.github.com/repos/{release_repo}/releases?per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "lunavale-game-release-metadata",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    versions: list[Version] = []
    for release in payload:
        candidate = release.get("tag_name") or release.get("name")
        if not candidate:
            continue
        try:
            versions.append(Version.parse(candidate))
        except ValueError:
            print(f"warning: ignoring non-semver game-builds release '{candidate}'", file=sys.stderr)
    return versions


def enforce_release_order(current: Version, releases: list[Version]) -> None:
    if not releases:
        return
    latest = max(releases, key=lambda item: VersionSortKey(item))
    comparison = current.compare(latest)
    if comparison == 0:
        raise RuntimeError(f"game-builds already has release version {current.raw}; bump Unity bundleVersion first.")
    if comparison < 0:
        raise RuntimeError(
            f"Unity bundleVersion {current.raw} is older than existing game-builds release {latest.raw}; "
            "bump Unity bundleVersion before building."
        )


class VersionSortKey:
    def __init__(self, version: Version) -> None:
        self.version = version

    def __lt__(self, other: "VersionSortKey") -> bool:
        return self.version.compare(other.version) < 0


def write_outputs(path: str | None, values: dict[str, str]) -> None:
    if not path:
        for key, value in values.items():
            print(f"{key}={value}")
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--release-repo", default="Lunavale-Collective/game-builds")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--skip-release-check", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    project_root = args.project_root.resolve()
    version = Version.parse(read_bundle_version(project_root))
    timestamp = datetime.now(timezone.utc)

    token = os.environ.get("GAME_BUILDS_RELEASE_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not args.skip_release_check:
        if not token:
            raise RuntimeError("GAME_BUILDS_RELEASE_TOKEN is required to check game-builds releases.")
        enforce_release_order(version, fetch_release_versions(args.release_repo, token))

    outputs = {
        "version": version.raw,
        "is_prerelease": "true" if version.is_prerelease else "false",
        "build_date": timestamp.strftime("%Y-%m-%d"),
        "build_time": timestamp.strftime("%H%M%S"),
        "repo_files_sha256": repo_files_hash(repo_root),
        "source_sha": run_git(repo_root, "rev-parse", "HEAD"),
        "unity_editor_version": read_editor_version(project_root),
    }
    write_outputs(args.output, outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
