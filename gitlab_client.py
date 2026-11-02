"""
Thin client around GitLab's REST API for pulling issues
from gitlab-com/gl-infra/production.

Phase 0 usage: fetch_all_issues() pulls everything so we can
inspect volume, date range, and label distribution before
building the rest of the pipeline.
"""

import time
import requests
from src.config import BASE_URL, PROJECT_PATH, HEADERS


def fetch_issues_page(page=1, per_page=100, state="closed"):
    """Fetch a single page of issues. Returns (issues_list, response_headers)."""
    url = f"{BASE_URL}/projects/{PROJECT_PATH}/issues"
    params = {
        "state": state,
        "per_page": per_page,
        "page": page,
        "order_by": "created_at",
        "sort": "asc",
    }
    response = requests.get(url, headers=HEADERS, params=params, timeout=30)

    # Respect rate limiting if GitLab asks us to back off
    if response.status_code == 429:
        wait = int(response.headers.get("Retry-After", 5))
        print(f"Rate limited. Waiting {wait}s...")
        time.sleep(wait)
        return fetch_issues_page(page, per_page, state)

    response.raise_for_status()
    return response.json(), response.headers


def _has_next_page(headers):
    """
    GitLab may omit X-Total-Pages on large collections (deprecated for
    performance). The reliable way to know if more data exists is the
    'Link' header, which contains a rel="next" URL only if another page exists.
    """
    link_header = headers.get("Link", "")
    return 'rel="next"' in link_header


def fetch_all_issues(state="closed", max_pages=None, per_page=100):
    """
    Pages through every issue in the project.
    max_pages: cap for quick testing (e.g. 3), None = pull everything.
    """
    all_issues = []
    page = 1

    while True:
        issues, headers = fetch_issues_page(page=page, per_page=per_page, state=state)
        if not issues:
            break

        all_issues.extend(issues)
        has_next = _has_next_page(headers)
        print(f"Fetched page {page} ({len(all_issues)} issues so far, "
              f"more pages: {has_next})")

        if not has_next:
            break
        if max_pages and page >= max_pages:
            print(f"Stopping early at max_pages={max_pages} (testing mode).")
            break

        page += 1
        time.sleep(0.2)  # small courtesy delay between requests

    return all_issues


def fetch_project_labels():
    """Pulls the real label list for this project (to verify names like 'postmortem')."""
    url = f"{BASE_URL}/projects/{PROJECT_PATH}/labels"
    params = {"per_page": 100}
    response = requests.get(url, headers=HEADERS, params=params, timeout=30)
    response.raise_for_status()
    return [label["name"] for label in response.json()]