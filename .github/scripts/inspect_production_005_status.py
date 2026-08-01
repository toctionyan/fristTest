#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
TARGET_SHA = os.environ["TARGET_MAIN_SHA"]
OUTPUT = ROOT / ".github/diagnostics/production-005-runs.json"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "production-005-status-inspector",
}


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_for(workflow: str) -> dict | None:
    query = urllib.parse.urlencode({"branch": "main", "per_page": 30})
    url = f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{workflow}/runs?{query}"
    payload = request_json(url)
    for run in payload.get("workflow_runs") or []:
        if str(run.get("head_sha") or "") == TARGET_SHA:
            return {
                "id": int(run["id"]),
                "name": str(run.get("name") or ""),
                "event": str(run.get("event") or ""),
                "status": str(run.get("status") or ""),
                "conclusion": run.get("conclusion"),
                "head_sha": str(run.get("head_sha") or ""),
                "run_number": int(run.get("run_number") or 0),
                "run_attempt": int(run.get("run_attempt") or 0),
                "created_at": str(run.get("created_at") or ""),
                "updated_at": str(run.get("updated_at") or ""),
                "html_url": str(run.get("html_url") or ""),
            }
    return None


result: dict[str, object] = {
    "schema_version": 1,
    "target_main_sha": TARGET_SHA,
    "request_id": "production-certification-20260801-005",
    "observed_at_epoch": int(time.time()),
    "request_workflow": None,
    "release_workflow": None,
}

for _ in range(120):
    result["request_workflow"] = latest_for("production-certification-request.yml")
    result["release_workflow"] = latest_for("release.yml")
    if result["request_workflow"] is not None and result["release_workflow"] is not None:
        break
    time.sleep(2)

result["observed_at_epoch"] = int(time.time())
OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
