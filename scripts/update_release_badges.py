#!/usr/bin/env python3
"""Generate Shields endpoint JSON for Lunavale roadmap badges."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


API_ROOT = "https://api.github.com"
OWNER = os.environ.get("ROADMAP_OWNER", "Lunavale-Collective")
ROADMAP_REPO = os.environ.get("ROADMAP_REPO", f"{OWNER}/scripts-roadmap")
ROADMAP_REF = os.environ.get("ROADMAP_REF", "main")
RELEASE_OUTPUT_DIR = Path(
    os.environ.get(
        "RELEASE_BADGE_OUTPUT_DIR",
        os.environ.get("BADGE_OUTPUT_DIR", ".badges/releases"),
    )
)
ROADMAP_OUTPUT_DIR = Path(os.environ.get("ROADMAP_BADGE_OUTPUT_DIR", ".badges/roadmap"))
LOCAL_ROADMAP_ROOT = Path(os.environ.get("ROADMAP_LOCAL_ROOT", "../scripts-roadmap"))
TOKEN = (
    os.environ.get("ROADMAP_BADGE_TOKEN")
    or os.environ.get("GH_TOKEN")
    or os.environ.get("GITHUB_TOKEN")
)
ALLOW_TBD = os.environ.get("ALLOW_TBD_BADGES", "").lower() in {"1", "true", "yes"}
ACTIVE_STATUSES = {"planned", "in_progress"}

RELEASES = [
    {
        "id": "internal-alpha",
        "label": "Internal Alpha",
        "color": "6f42c1",
    },
    {
        "id": "internal-beta",
        "label": "Internal Beta",
        "color": "0969da",
    },
    {
        "id": "public-beta",
        "label": "Public Beta",
        "color": "1f883d",
    },
    {
        "id": "public-early-release",
        "label": "Public Early Release",
        "color": "bc4c00",
    },
    {
        "id": "public-release",
        "label": "Public Release",
        "color": "cf222e",
    },
]


class GithubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def github_json(path_or_url: str, *, optional: bool = False) -> dict | None:
    if not TOKEN:
        return None

    if path_or_url.startswith("https://"):
        url = path_or_url
    else:
        url = f"{API_ROOT}{path_or_url}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": "lunavale-release-badge-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if optional and exc.code in {401, 403, 404, 422}:
            print(f"warning: GitHub API {exc.code} for {url}: {body}", file=sys.stderr)
            return None
        raise GithubError(exc.code, f"GitHub API {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        if optional:
            print(f"warning: GitHub API request failed for {url}: {exc}", file=sys.stderr)
            return None
        raise


def parse_scalar_yaml(text: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for line in text.splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if not value or value == "null":
            values[key.strip()] = None
        elif value.startswith('"') and value.endswith('"'):
            values[key.strip()] = value[1:-1].replace('\\"', '"')
        else:
            values[key.strip()] = value
    return values


def parse_roadmap_body(body: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for line in body.splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z _-]*):\s*(.*)$", line.strip())
        if not match:
            continue
        key = match.group(1).strip().lower().replace(" ", "_").replace("-", "_")
        value = match.group(2).strip()
        values[key] = None if value in {"", "None", "null"} else value
    return values


def date_message(date_text: str | None) -> str | None:
    if not date_text:
        return None
    try:
        value = dt.date.fromisoformat(date_text)
    except ValueError:
        return date_text
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def parse_iso_date(date_text: str | None) -> dt.date | None:
    if not date_text:
        return None
    try:
        return dt.date.fromisoformat(date_text)
    except ValueError:
        return None


def search_issue(release_id: str) -> dict[str, str | None] | None:
    marker = f"ID: {release_id}"
    query = f'org:{OWNER} type:issue in:body "{marker}"'
    params = urllib.parse.urlencode({"q": query, "per_page": "10"})
    data = github_json(f"/search/issues?{params}", optional=True)
    if not data:
        return None

    for item in data.get("items", []):
        body = item.get("body") or ""
        if marker not in body and f"ID: `{release_id}`" not in body:
            continue
        values = parse_roadmap_body(body)
        if values.get("id") != release_id:
            continue
        return {
            "id": values.get("id"),
            "repo": values.get("repo"),
            "start": values.get("start"),
            "end": values.get("end"),
            "status": values.get("status"),
            "source": "github_issue",
            "source_url": item.get("html_url"),
        }
    return None


def read_github_issue_file(release_id: str) -> dict[str, str | None] | None:
    encoded_path = urllib.parse.quote(f"data/issues/{release_id}.yml", safe="/")
    data = github_json(
        f"/repos/{ROADMAP_REPO}/contents/{encoded_path}?ref={urllib.parse.quote(ROADMAP_REF)}",
        optional=True,
    )
    if not data or data.get("encoding") != "base64":
        return None
    content = base64.b64decode(data["content"]).decode("utf-8")
    values = parse_scalar_yaml(content)
    values["source"] = "github_roadmap_yml"
    values["source_url"] = data.get("html_url")
    return values


def read_local_issue_file(release_id: str) -> dict[str, str | None] | None:
    issue_path = LOCAL_ROADMAP_ROOT / "data" / "issues" / f"{release_id}.yml"
    if not issue_path.exists():
        return None
    values = parse_scalar_yaml(issue_path.read_text(encoding="utf-8"))
    values["source"] = "local_roadmap_yml"
    values["source_url"] = str(issue_path)
    return values


def read_local_issue_files() -> dict[str, dict[str, str | None]]:
    issue_dir = LOCAL_ROADMAP_ROOT / "data" / "issues"
    if not issue_dir.exists():
        return {}

    issues: dict[str, dict[str, str | None]] = {}
    for issue_path in sorted(issue_dir.glob("*.yml")):
        values = parse_scalar_yaml(issue_path.read_text(encoding="utf-8"))
        issue_id = values.get("id") or issue_path.stem
        if not issue_id:
            continue
        values["id"] = issue_id
        values["source"] = "local_roadmap_yml"
        values["source_url"] = str(issue_path)
        issues[str(issue_id)] = values
    return issues


def issue_sort_date(issue: dict[str, str | None]) -> dt.date:
    for key in ("start", "end"):
        value = parse_iso_date(issue.get(key))
        if value:
            return value
    return dt.date.max


def current_phase(issues: dict[str, dict[str, str | None]]) -> dict[str, str | None]:
    today_text = os.environ.get("ROADMAP_TODAY")
    today = dt.date.fromisoformat(today_text) if today_text else dt.date.today()
    active = [
        issue
        for issue in issues.values()
        if (issue.get("status") or "").lower() in ACTIVE_STATUSES
    ]
    if not active:
        return {
            "label": "Current Phase",
            "message": "No active phase",
            "color": "lightgrey",
            "issue_id": None,
            "parent_id": None,
        }

    current = []
    for issue in active:
        start = parse_iso_date(issue.get("start"))
        end = parse_iso_date(issue.get("end"))
        if start and end and start <= today <= end:
            current.append(issue)

    upcoming = [
        issue
        for issue in active
        if (parse_iso_date(issue.get("start")) or dt.date.min) >= today
    ]
    selected = sorted(current or upcoming or active, key=issue_sort_date)[0]
    parent_id = selected.get("parent")
    parent = issues.get(str(parent_id)) if parent_id else None
    title = (parent or selected).get("title") or parent_id or selected.get("id") or "Unknown"
    return {
        "label": "Current Phase",
        "message": str(title),
        "color": "0969da",
        "issue_id": selected.get("id"),
        "parent_id": parent_id,
    }


def load_release(release: dict[str, str]) -> dict[str, str | None]:
    release_id = release["id"]
    values = read_local_issue_file(release_id)
    if values is None:
        values = search_issue(release_id)
    if values is None:
        values = read_github_issue_file(release_id)
    if values is None:
        values = {"id": release_id, "source": "missing"}

    return {
        "id": release_id,
        "label": release["label"],
        "color": release["color"],
        "repo": values.get("repo"),
        "start": values.get("start"),
        "end": values.get("end"),
        "status": values.get("status"),
        "source": values.get("source"),
        "source_url": values.get("source_url"),
    }


def write_badge(path: Path, *, label: str, message: str, color: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "label": label,
                "message": message,
                "color": color,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    RELEASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ROADMAP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    resolved = [load_release(release) for release in RELEASES]

    missing = [item["id"] for item in resolved if not item.get("end")]
    if missing and not ALLOW_TBD:
        joined = ", ".join(missing)
        raise SystemExit(
            "error: missing release dates for "
            f"{joined}; configure ROADMAP_BADGE_TOKEN or set ROADMAP_LOCAL_ROOT"
        )

    for item in resolved:
        message = date_message(item.get("end")) or "TBD"
        color = item["color"] if item.get("end") else "lightgrey"
        write_badge(
            RELEASE_OUTPUT_DIR / f"{item['id']}.json",
            label=item["label"],
            message=message,
            color=color,
        )
        item["message"] = message

    index = {
        "roadmap_ref": ROADMAP_REF,
        "roadmap_repo": ROADMAP_REPO,
        "releases": [
            {
                "end": item.get("end"),
                "id": item["id"],
                "label": item["label"],
                "message": item["message"],
                "start": item.get("start"),
                "status": item.get("status"),
            }
            for item in resolved
        ],
    }
    (RELEASE_OUTPUT_DIR / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    phase = current_phase(read_local_issue_files())
    write_badge(
        ROADMAP_OUTPUT_DIR / "current-phase.json",
        label=str(phase["label"]),
        message=str(phase["message"]),
        color=str(phase["color"]),
    )
    (ROADMAP_OUTPUT_DIR / "current-phase-meta.json").write_text(
        json.dumps(phase, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for item in resolved:
        print(f"{item['id']}: {item['message']} ({item['source']})")
    print(f"current-phase: {phase['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
