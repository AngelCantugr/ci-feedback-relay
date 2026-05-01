import json
import re
import subprocess
import sys

import httpx

hook_input = json.loads(sys.stdin.read())

tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})

if tool_name == "Bash":
    command = tool_input.get("command", "")
    if "git push" in command:
        # Handle patterns like: git push origin branch, git push -u origin branch
        branch_match = re.search(r"git push(?:\s+-\S+)*\s+\w+\s+(\S+)", command)
        if branch_match:
            branch = branch_match.group(1)
        else:
            # Fallback: ask git for the current branch name
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                branch = result.stdout.strip() if result.returncode == 0 else None
            except Exception:
                branch = None

        if branch:
            try:
                httpx.post(
                    "http://localhost:8080/internal/register-watch",
                    json={
                        "branch": branch,
                        "session_id": hook_input.get("session_id", "default"),
                    },
                    timeout=2.0,
                )
            except Exception:
                pass  # Never interrupt the agent for hook failures
