"""Re-run a pull-request run's failed jobs when all of them failed downloading
an action. The conditions are documented in this directory's action.yml."""

import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

# Messages the runner emits when an action archive download fails,
# whether on a 404 (one attempt) or after exhausting its retries.
DOWNLOAD_FAILURE = re.compile(
    r"Failed to download archive '[^']+' after \d+ attempts"
    r"|An action could not be found at the URI '"
)
RERUN_ATTEMPTS = 3

repo = os.environ["GITHUB_REPOSITORY"]
gate_job = os.environ["GATE_JOB"]

if os.environ["GITHUB_EVENT_NAME"] != "workflow_run":
    sys.exit("rerun-action-download-failures must run in a workflow_run job")
with open(os.environ["GITHUB_EVENT_PATH"]) as f:
    workflow_run = json.load(f)["workflow_run"]
run_id = workflow_run["id"]


def try_gh_api(path, *args):
    # gh authenticates with GH_TOKEN, which action.yml sets from the github-token input.
    result = subprocess.run(
        ["gh", "api", *args, path], capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"gh api {path} failed: {result.stderr.strip()}")
        return False, None
    return True, json.loads(result.stdout) if result.stdout.strip() else None


def gh_api(path, *args, attempts=5):
    for attempt in range(1, attempts + 1):
        ok, data = try_gh_api(path, *args)
        if ok:
            return data
        if attempt < attempts:
            time.sleep(2 * attempt)
    sys.exit(f"gh api {path} failed after {attempts} attempts")


def skip(reason):
    print(f"Not re-running run {run_id}: {reason}")
    sys.exit(0)


def require_rerunnable():
    """Skip unless the run is still on attempt 1 and is the PR's latest run.

    The event payload is a snapshot: a manual re-run or a new push can change
    either since, and a stale re-run would cancel a newer run of the PR.
    """
    run = gh_api(f"repos/{repo}/actions/runs/{run_id}")
    if run["run_attempt"] != 1 or run["status"] != "completed":
        skip(f"run is now attempt {run['run_attempt']}, status {run['status']}")
    runs = gh_api(
        f"repos/{repo}/actions/workflows/{workflow_run['workflow_id']}/runs"
        f"?branch={quote(workflow_run['head_branch'], safe='')}&event=pull_request&per_page=20"
    )["workflow_runs"]
    pr_runs = sorted(
        (r for r in runs
         if any(p["number"] == pr_number for p in r.get("pull_requests") or [])),
        key=lambda r: r["run_number"], reverse=True,
    )
    # Fail closed: an empty or incomplete list cannot confirm this run is the newest.
    if not pr_runs or pr_runs[0]["id"] != run_id:
        newest = pr_runs[0]["id"] if pr_runs else "unknown"
        skip(f"not confirmed as the latest run for PR #{pr_number} (newest: {newest})")


if workflow_run["conclusion"] != "failure":
    skip(f"run concluded {workflow_run['conclusion']}")
if workflow_run["run_attempt"] != 1:
    skip(f"run is on attempt {workflow_run['run_attempt']}")
if workflow_run["event"] != "pull_request":
    skip(f"run was triggered by {workflow_run['event']}, not pull_request")
head_repo = (workflow_run.get("head_repository") or {}).get("full_name")
if head_repo != repo:
    skip(f"head repository {head_repo} is not {repo}")
pull_requests = workflow_run.get("pull_requests") or []
if not pull_requests:
    skip("no pull request attached to the run")
pr_number = pull_requests[0]["number"]

jobs = gh_api(
    f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page=100",
    "--paginate", "--slurp",
)
jobs = [j for page in jobs for j in page["jobs"]]
failed = [
    j for j in jobs
    if j["conclusion"] in ("failure", "timed_out") and j["name"] != gate_job
]
if not failed:
    skip(f"no failed jobs besides {gate_job}")
for job in failed:
    annotations = gh_api(f"repos/{repo}/check-runs/{job['id']}/annotations") or []
    if not any(DOWNLOAD_FAILURE.search(a.get("message") or "") for a in annotations):
        skip(f"job '{job['name']}' failed for another reason")
    print(f"Job '{job['name']}' failed downloading an action in setup.")

# Checked last, right before the POST, to keep the window for a manual re-run
# or a new push as short as the API allows.
require_rerunnable()

print(f"Re-running {len(failed)} failed job(s) of run {run_id} (PR #{pr_number}).")
for attempt in range(1, RERUN_ATTEMPTS + 1):
    ok, _ = try_gh_api(f"repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs", "-X", "POST")
    if ok:
        sys.exit(0)
    if attempt < RERUN_ATTEMPTS:
        time.sleep(2 * attempt)
        # An error does not prove the re-run did not start, and the PR may have
        # a newer run by now: a blind retry could start an attempt 3 or cancel it.
        require_rerunnable()
sys.exit(f"rerun-failed-jobs failed after {RERUN_ATTEMPTS} attempts")
