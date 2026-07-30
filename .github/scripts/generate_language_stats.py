from __future__ import annotations

import html
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.github.com"
OUTPUT_PATH = Path("assets/language-stats.svg")

TOKEN = os.environ.get("LANGUAGE_STATS_TOKEN", "").strip()
MAX_LANGUAGES = int(os.environ.get("MAX_LANGUAGES", "6"))

EXCLUDED_REPOS = {
    repo.strip().lower()
    for repo in os.environ.get("EXCLUDED_REPOS", "").split(",")
    if repo.strip()
}


def github_get(url: str) -> object:
    """Make an authenticated request to the GitHub REST API."""
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": "profile-language-stats",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed: HTTP {error.code}\n{body}"
        ) from error


def get_owned_repositories() -> list[dict]:
    """Return every owned repository visible to the token."""
    repositories: list[dict] = []
    page = 1

    while True:
        query = urlencode(
            {
                "affiliation": "owner",
                "visibility": "all",
                "per_page": 100,
                "page": page,
                "sort": "full_name",
            }
        )

        result = github_get(f"{API_BASE}/user/repos?{query}")

        if not isinstance(result, list):
            raise RuntimeError("Unexpected response while listing repositories.")

        repositories.extend(result)

        if len(result) < 100:
            break

        page += 1

    return repositories


def collect_language_totals(
    repositories: list[dict],
) -> tuple[dict[str, int], int]:
    """Combine GitHub Linguist byte totals from eligible repositories."""
    totals: dict[str, int] = defaultdict(int)
    counted_repositories = 0

    for repository in repositories:
        name = str(repository.get("name", ""))
        full_name = str(repository.get("full_name", name))

        if repository.get("fork", False):
            continue

        if name.lower() in EXCLUDED_REPOS or full_name.lower() in EXCLUDED_REPOS:
            continue

        languages_url = repository.get("languages_url")
        if not languages_url:
            continue

        language_data = github_get(str(languages_url))

        if not isinstance(language_data, dict) or not language_data:
            continue

        for language, byte_count in language_data.items():
            if isinstance(byte_count, int) and byte_count > 0:
                totals[str(language)] += byte_count

        counted_repositories += 1

    return dict(totals), counted_repositories


def build_display_rows(totals: dict[str, int]) -> list[tuple[str, int, float]]:
    total_bytes = sum(totals.values())

    if total_bytes <= 0:
        return []

    ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    displayed = ordered[:MAX_LANGUAGES]
    remainder = sum(value for _, value in ordered[MAX_LANGUAGES:])

    if remainder > 0:
        displayed.append(("Other", remainder))

    return [
        (language, byte_count, byte_count / total_bytes * 100)
        for language, byte_count in displayed
    ]


def generate_svg(
    rows: list[tuple[str, int, float]],
    repository_count: int,
) -> str:
    width = 660
    top_padding = 78
    row_height = 43
    bottom_padding = 30
    height = top_padding + len(rows) * row_height + bottom_padding

    label_x = 28
    bar_x = 180
    bar_width = 350
    percentage_x = 630

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Aggregated programming language usage">'
        ),
        "<style>",
        (
            "text { font-family: ui-monospace, SFMono-Regular, "
            "Consolas, 'Liberation Mono', monospace; }"
        ),
        ".title { fill: #f0f6fc; font-size: 20px; font-weight: 700; }",
        ".subtitle { fill: #8b949e; font-size: 12px; }",
        ".language { fill: #c9d1d9; font-size: 14px; }",
        ".percentage { fill: #f0f6fc; font-size: 13px; font-weight: 600; }",
        "</style>",
        (
            f'<rect width="{width}" height="{height}" rx="10" '
            'fill="#0d1117" stroke="#30363d"/>'
        ),
        '<rect x="0" y="0" width="6" height="100%" rx="3" fill="#c93636"/>',
        '<text x="28" y="34" class="title">LANGUAGES</text>',
        (
            f'<text x="28" y="56" class="subtitle">'
            f'Aggregated across {repository_count} owned, non-fork repositories'
            "</text>"
        ),
    ]

    for index, (language, _, percentage) in enumerate(rows):
        y = top_padding + index * row_height
        filled_width = max(2, round(bar_width * percentage / 100))

        safe_language = html.escape(language)

        lines.extend(
            [
                (
                    f'<text x="{label_x}" y="{y + 18}" '
                    f'class="language">{safe_language}</text>'
                ),
                (
                    f'<rect x="{bar_x}" y="{y + 5}" width="{bar_width}" '
                    'height="16" rx="8" fill="#21262d"/>'
                ),
                (
                    f'<rect x="{bar_x}" y="{y + 5}" width="{filled_width}" '
                    'height="16" rx="8" fill="#c93636"/>'
                ),
                (
                    f'<text x="{percentage_x}" y="{y + 18}" '
                    f'text-anchor="end" class="percentage">'
                    f"{percentage:.1f}%</text>"
                ),
            ]
        )

    lines.append("</svg>")
    return "\n".join(lines)


def main() -> int:
    if not TOKEN:
        print("LANGUAGE_STATS_TOKEN is not configured.", file=sys.stderr)
        return 1

    repositories = get_owned_repositories()
    totals, repository_count = collect_language_totals(repositories)
    rows = build_display_rows(totals)

    if not rows:
        print("No language data was returned.", file=sys.stderr)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        generate_svg(rows, repository_count),
        encoding="utf-8",
    )

    print(
        f"Generated {OUTPUT_PATH} from "
        f"{repository_count} repositories and {len(totals)} languages."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
