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
WORKFLOW_OUTPUT_DIR = Path(os.environ.get("WORKFLOW_BADGE_OUTPUT_DIR", ".badges/workflows"))
LOCAL_ROADMAP_ROOT = Path(os.environ.get("ROADMAP_LOCAL_ROOT", "../scripts-roadmap"))
TOKEN = (
    os.environ.get("ROADMAP_BADGE_TOKEN")
    or os.environ.get("GH_TOKEN")
    or os.environ.get("GITHUB_TOKEN")
)
ALLOW_TBD = os.environ.get("ALLOW_TBD_BADGES", "").lower() in {"1", "true", "yes"}
ACTIVE_STATUSES = {"planned", "in_progress"}
INACTIVE_STATUSES = {"not_planned"}

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


def parse_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


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
    for issue_path in sorted(issue_dir.rglob("*.yml")):
        values = parse_scalar_yaml(issue_path.read_text(encoding="utf-8"))
        issue_id = values.get("id") or issue_path.stem
        if not issue_id:
            continue
        if str(issue_id) in issues and "closed" in issue_path.parts:
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


def roadmap_today() -> dt.date:
    today_text = os.environ.get("ROADMAP_TODAY")
    return dt.date.fromisoformat(today_text) if today_text else dt.date.today()


def issue_parent_id(issue: dict[str, str | None]) -> str | None:
    parent = issue.get("parent")
    if not parent or parent in {"None", "null"}:
        return None
    return str(parent)


def root_phase(issue: dict[str, str | None], issues: dict[str, dict[str, str | None]]) -> dict[str, str | None]:
    current = issue
    seen: set[str] = set()
    while True:
        issue_id = str(current.get("id") or "")
        if issue_id in seen:
            return current
        seen.add(issue_id)
        parent_id = issue_parent_id(current)
        if not parent_id or parent_id not in issues:
            return current
        current = issues[parent_id]


def phase_title(issue: dict[str, str | None] | None) -> str:
    if not issue:
        return "Unknown"
    return str(issue.get("title") or issue.get("id") or "Unknown")


def phase_payload(
    label: str,
    issue: dict[str, str | None] | None,
    *,
    color: str,
    empty_message: str,
) -> dict[str, str | None]:
    if not issue:
        return {
            "color": "lightgrey",
            "end": None,
            "issue_id": None,
            "label": label,
            "message": empty_message,
            "parent_id": None,
            "start": None,
            "status": None,
        }

    return {
        "color": color,
        "end": issue.get("end"),
        "issue_id": issue.get("id"),
        "label": label,
        "message": phase_title(issue),
        "parent_id": issue_parent_id(issue),
        "start": issue.get("start"),
        "status": issue.get("status"),
    }


def phase_candidates(issues: dict[str, dict[str, str | None]]) -> list[dict[str, str | None]]:
    candidates = []
    for issue in issues.values():
        if issue_parent_id(issue):
            continue
        if (issue.get("status") or "").lower() in INACTIVE_STATUSES:
            continue
        if not parse_iso_date(issue.get("start")) and not parse_iso_date(issue.get("end")):
            continue
        candidates.append(issue)
    return sorted(candidates, key=issue_sort_date)


def current_phase_issue(
    issues: dict[str, dict[str, str | None]],
    today: dt.date,
) -> dict[str, str | None] | None:
    candidates = phase_candidates(issues)
    current = [
        issue
        for issue in candidates
        if (issue.get("status") or "").lower() in ACTIVE_STATUSES
        and (parse_iso_date(issue.get("start")) or dt.date.max) <= today
        and today <= (parse_iso_date(issue.get("end")) or dt.date.min)
    ]
    if current:
        return sorted(current, key=issue_sort_date)[0]

    upcoming = [
        issue
        for issue in candidates
        if (issue.get("status") or "").lower() in ACTIVE_STATUSES
        and (parse_iso_date(issue.get("start")) or dt.date.min) >= today
    ]
    if upcoming:
        return sorted(upcoming, key=issue_sort_date)[0]

    active = [
        issue
        for issue in issues.values()
        if (issue.get("status") or "").lower() in ACTIVE_STATUSES
    ]
    if not active:
        return None

    current_children = []
    for issue in active:
        start = parse_iso_date(issue.get("start"))
        end = parse_iso_date(issue.get("end"))
        if start and end and start <= today <= end:
            current_children.append(issue)

    upcoming = [
        issue
        for issue in active
        if (parse_iso_date(issue.get("start")) or dt.date.min) >= today
    ]
    selected = sorted(current_children or upcoming or active, key=issue_sort_date)[0]
    return root_phase(selected, issues)


def previous_phase_issue(
    issues: dict[str, dict[str, str | None]],
    today: dt.date,
    current: dict[str, str | None] | None,
) -> dict[str, str | None] | None:
    cutoff = parse_iso_date(current.get("start")) if current else today
    if not cutoff:
        cutoff = today

    previous = [
        issue
        for issue in phase_candidates(issues)
        if issue.get("id") != (current or {}).get("id")
        and (parse_iso_date(issue.get("end")) or dt.date.max) < cutoff
    ]
    return sorted(
        previous,
        key=lambda issue: parse_iso_date(issue.get("end")) or dt.date.min,
        reverse=True,
    )[0] if previous else None


def next_phase_issue(
    issues: dict[str, dict[str, str | None]],
    today: dt.date,
    current: dict[str, str | None] | None,
) -> dict[str, str | None] | None:
    cutoff = parse_iso_date(current.get("end")) if current else today
    if not cutoff:
        cutoff = today

    upcoming = [
        issue
        for issue in phase_candidates(issues)
        if issue.get("id") != (current or {}).get("id")
        and (issue.get("status") or "").lower() in ACTIVE_STATUSES
        and (parse_iso_date(issue.get("start")) or dt.date.min) > cutoff
    ]
    return sorted(upcoming, key=issue_sort_date)[0] if upcoming else None


def roadmap_phases(issues: dict[str, dict[str, str | None]]) -> dict[str, dict[str, str | None]]:
    today = roadmap_today()
    current = current_phase_issue(issues, today)
    previous = previous_phase_issue(issues, today, current)
    next_issue = next_phase_issue(issues, today, current)
    return {
        "last": phase_payload(
            "Last Phase",
            previous,
            color="6f42c1",
            empty_message="No previous phase",
        ),
        "current": phase_payload(
            "Current Phase",
            current,
            color="0969da",
            empty_message="No active phase",
        ),
        "next": phase_payload(
            "Next Phase",
            next_issue,
            color="1f883d",
            empty_message="No next phase",
        ),
    }


def actual_duration_days(issue: dict[str, str | None]) -> int:
    effort = parse_int(issue.get("effort"))
    start = parse_iso_date(issue.get("start"))
    end = parse_iso_date(issue.get("end"))
    if not start or not end or end < start:
        return 0 if effort == 0 else 1
    return (end - start).days + 1


def completed_effort_summary(
    issues: dict[str, dict[str, str | None]],
) -> dict[str, int | float]:
    completed = [
        issue
        for issue in issues.values()
        if (issue.get("status") or "").lower() == "complete"
    ]
    expected_days = sum(parse_int(issue.get("effort")) for issue in completed)
    actual_days = sum(actual_duration_days(issue) for issue in completed)
    delta_days = actual_days - expected_days
    delta_percent = 0.0 if expected_days == 0 else (delta_days / expected_days) * 100
    return {
        "actual_days": actual_days,
        "count": len(completed),
        "delta_days": delta_days,
        "delta_percent": delta_percent,
        "expected_days": expected_days,
    }


def schedule_variance_color(delta_percent: float) -> str:
    saved_percent = -delta_percent
    if saved_percent >= 50:
        return "1f883d"
    if saved_percent > 1:
        return "97ca00"
    if saved_percent >= -1:
        return "dfb317"
    return "cf222e"


def format_variance_days(delta_days: int) -> str:
    if delta_days < 0:
        return f"Ahead {abs(delta_days)}d"
    if delta_days > 0:
        return f"Behind {delta_days}d"
    return "On Track 0d"


def format_variance_percent(delta_percent: float) -> str:
    if delta_percent < 0:
        return f"Ahead {abs(delta_percent):.1f}%"
    if delta_percent > 0:
        return f"Behind {delta_percent:.1f}%"
    return "On Track 0.0%"


def write_schedule_variance_badges(issues: dict[str, dict[str, str | None]]) -> dict[str, int | float]:
    summary = completed_effort_summary(issues)
    color = schedule_variance_color(float(summary["delta_percent"]))
    write_badge(
        ROADMAP_OUTPUT_DIR / "schedule-variance.json",
        label="Schedule Variance",
        message=format_variance_days(int(summary["delta_days"])),
        color=color,
    )
    write_badge(
        ROADMAP_OUTPUT_DIR / "schedule-delta.json",
        label="Schedule Delta",
        message=format_variance_percent(float(summary["delta_percent"])),
        color=color,
    )
    return summary


def current_phase(issues: dict[str, dict[str, str | None]]) -> dict[str, str | None]:
    return roadmap_phases(issues)["current"]


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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def read_badge_message(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    message = data.get("message")
    return message if isinstance(message, str) and message else None


def main() -> int:
    RELEASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ROADMAP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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

    issues = read_local_issue_files()
    phases = roadmap_phases(issues)
    for phase_id, phase in phases.items():
        write_badge(
            ROADMAP_OUTPUT_DIR / f"{phase_id}-phase.json",
            label=str(phase["label"]),
            message=str(phase["message"]),
            color=str(phase["color"]),
        )
        (ROADMAP_OUTPUT_DIR / f"{phase_id}-phase-meta.json").write_text(
            json.dumps(phase, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    variance_summary = write_schedule_variance_badges(issues)

    roadmap_updated = read_badge_message(WORKFLOW_OUTPUT_DIR / "roadmap-updated.json")
    write_badge(
        WORKFLOW_OUTPUT_DIR / "release-dates-updated.json",
        label="Release Dates Updated",
        message=roadmap_updated or "unknown",
        color="0969da" if roadmap_updated else "lightgrey",
    )

    for item in resolved:
        print(f"{item['id']}: {item['message']} ({item['source']})")
    for phase_id, phase in phases.items():
        print(f"{phase_id}-phase: {phase['message']}")
    print(
        "schedule-variance: "
        f"{format_variance_days(int(variance_summary['delta_days']))}; "
        f"{format_variance_percent(float(variance_summary['delta_percent']))}"
    )
    print(f"release-dates-updated: {roadmap_updated or 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
