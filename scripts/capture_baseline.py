"""
Collect CI baseline stats for merged PRs before MCP connection.

Usage:
    python scripts/capture_baseline.py --repos angelcantugr/ci-feedback-relay --limit 20

Output:
    data/baseline.json — one entry per PR with:
      pr_number, merged_at, total_ci_runs, time_to_green_seconds, first_sha, merge_sha
"""

from __future__ import annotations

import argparse
import json
import os

from github import Github


def collect_baseline(repo_name: str, limit: int) -> list[dict]:
    g = Github(os.environ["GITHUB_TOKEN"])  # personal token, read-only
    repo = g.get_repo(repo_name)

    results = []
    pulls = repo.get_pulls(state="closed", sort="updated", direction="desc")

    count = 0
    for pr in pulls:
        if not pr.merged_at:
            continue
        if count >= limit:
            break

        commits = list(pr.get_commits())
        total_runs = 0
        first_failure_at = None
        green_at = None

        for commit in commits:
            for check_run in commit.get_check_runs():
                total_runs += 1
                if check_run.conclusion == "failure" and first_failure_at is None:
                    first_failure_at = check_run.started_at
                if check_run.conclusion == "success":
                    green_at = check_run.completed_at

        time_to_green = None
        if first_failure_at and green_at:
            time_to_green = int((green_at - first_failure_at).total_seconds())

        results.append(
            {
                "pr_number": pr.number,
                "title": pr.title,
                "merged_at": pr.merged_at.isoformat(),
                "total_ci_runs": total_runs,
                "commit_count": len(commits),
                "time_to_green_secs": time_to_green,
                "first_sha": commits[0].sha if commits else None,
                "merge_sha": pr.merge_commit_sha,
            }
        )
        count += 1
        print(f"  PR #{pr.number}: {total_runs} CI runs, {len(commits)} commits")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    os.makedirs("data", exist_ok=True)
    all_results: dict[str, list[dict]] = {}

    for repo in args.repos:
        print(f"\nCollecting baseline for {repo}...")
        all_results[repo] = collect_baseline(repo, args.limit)

    output_path = "data/baseline.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)

    total_prs = sum(len(v) for v in all_results.values())
    avg_runs = sum(r["total_ci_runs"] for v in all_results.values() for r in v) / max(
        total_prs, 1
    )
    print(f"\nBaseline captured: {total_prs} PRs, avg {avg_runs:.1f} CI runs/PR")
    print(f"Saved to {output_path}")
