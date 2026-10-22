#!/usr/bin/env python3
"""
simulate_merge.py
Mock Payload Simulation script for Automated Open-Source PR Chronicler (GenAI 101)
Simulates GitHub pull_request webhook events against http://localhost:8000/webhook/github.
Tests 3 cases:
  1. Merged PR (action='closed', merged=True) -> Pipeline executes
  2. Closed but unmerged PR (action='closed', merged=False) -> Ignored (HTTP 200)
  3. Opened/Updated PR (action='opened', merged=False) -> Ignored (HTTP 200)
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
from typing import Dict, Any, Optional
import httpx


def build_mock_payload(
    action: str,
    merged: bool,
    owner: str = "pallets",
    repo: str = "flask",
    pr_number: int = 5000,
    pr_title: str = "Remove setup.py in favor of pyproject.toml",
    pr_body: str = "Migrate packaging configuration to pyproject.toml using flit. Fixes #4999",
    author: str = "davidism",
) -> Dict[str, Any]:
    """Generates a realistic GitHub pull_request webhook payload."""
    repo_full_name = f"{owner}/{repo}"
    repo_url = f"https://github.com/{repo_full_name}"
    pr_url = f"{repo_url}/pull/{pr_number}"

    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "id": 123456700 + pr_number,
            "number": pr_number,
            "title": pr_title,
            "body": pr_body,
            "state": "closed" if action == "closed" else "open",
            "merged": merged,
            "merged_at": "2023-03-24T18:00:00Z" if merged else None,
            "html_url": pr_url,
            "diff_url": f"{pr_url}.diff",
            "user": {
                "login": author,
                "id": 10001,
                "avatar_url": f"https://avatars.githubusercontent.com/u/10001?v=4"
            },
            "head": {
                "ref": "feature/modern-packaging",
                "sha": "a1b2c3d4e5f67890123456789abcdef012345678"
            },
            "base": {
                "ref": "main",
                "sha": "9876543210fedcba9876543210fedcba98765432"
            }
        },
        "repository": {
            "id": 999999,
            "name": repo,
            "full_name": repo_full_name,
            "html_url": repo_url,
            "owner": {
                "login": owner,
                "id": 50000
            }
        },
        "sender": {
            "login": author,
            "id": 10001
        }
    }


def compute_signature(secret: str, body: bytes) -> str:
    """Computes X-Hub-Signature-256 for GitHub webhook testing."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def send_simulation(
    target_url: str,
    payload: Dict[str, Any],
    secret: Optional[str] = None,
    delivery_id: Optional[str] = None,
) -> Tuple[int, Dict[str, Any], float]:
    """Sends payload via HTTP POST and measures latency."""
    raw_bytes = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_id or f"mock-delivery-{int(time.time() * 1000)}",
        "User-Agent": "GitHub-Hookshot/simulate_merge",
    }

    if secret:
        headers["X-Hub-Signature-256"] = compute_signature(secret, raw_bytes)

    start_time = time.perf_counter()
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(target_url, content=raw_bytes, headers=headers)
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        print(f"  [Note] Direct TCP connection to {target_url} failed ({exc}).")
        print("         Falling back to FastAPI TestClient for in-process simulation...")
        from fastapi.testclient import TestClient
        from main import app
        direct_client = TestClient(app)
        response = direct_client.post("/webhook/github", content=raw_bytes, headers=headers)

    latency = time.perf_counter() - start_time

    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}

    return response.status_code, data, latency


def run_tests(
    url: str,
    case: str,
    secret: Optional[str],
    owner: str,
    repo: str,
    pr_number: int,
) -> bool:
    print("\n" + "=" * 75)
    print(" AUTOMATED OPEN-SOURCE PR CHRONICLER — SIMULATION PROTOCOL")
    print(" Target Endpoint :", url)
    print(" Test Repository :", f"{owner}/{repo} (PR #{pr_number})")
    print("=" * 75)

    all_passed = True

    # --------------------------------------------------------------------------
    # Case 1: Merged PR (Pipeline executes)
    # --------------------------------------------------------------------------
    if case in ("all", "merged"):
        print("\n[TEST CASE 1] Merged Pull Request (action='closed', merged=True)")
        payload = build_mock_payload(
            action="closed",
            merged=True,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_title="Migrate configuration to modern pyproject.toml",
            pr_body="Fixes #4999. Modernizes packaging setup and removes deprecated setup.py logic.",
        )
        status_code, res, latency = send_simulation(url, payload, secret)
        print(f"  HTTP Status  : {status_code}")
        print(f"  Latency      : {latency:.3f}s (Assignment validation target: <15s)")
        print(f"  Event Status : {res.get('status')}")
        print(f"  Reason       : {res.get('reason')}")
        print(f"  Diff Chars   : {res.get('diff_characters')}")
        print(f"  Email Sent   : {res.get('email_dispatched')}")

        if status_code == 200 and res.get("status") == "processed":
            print("  --> PASS: Pipeline successfully executed for merged PR.")
            if res.get("reflection"):
                ref = res["reflection"]
                print(f"  --> Reflection Issue: {ref.get('issue_details')[:80]}...")
                print(f"  --> Reflection Key Learnings Count: {len(ref.get('key_learnings', []))}")
        else:
            print("  --> FAIL: Expected 200 with status='processed'.")
            all_passed = False

    # --------------------------------------------------------------------------
    # Case 2: Closed but Unmerged PR (Ignored)
    # --------------------------------------------------------------------------
    if case in ("all", "unmerged"):
        print("\n[TEST CASE 2] Closed but Unmerged PR (action='closed', merged=False)")
        payload = build_mock_payload(
            action="closed",
            merged=False,
            owner=owner,
            repo=repo,
            pr_number=pr_number + 1,
            pr_title="Experimental refactor closed without merging",
            pr_body="This experiment was abandoned.",
        )
        status_code, res, latency = send_simulation(url, payload, secret)
        print(f"  HTTP Status  : {status_code}")
        print(f"  Latency      : {latency:.3f}s")
        print(f"  Event Status : {res.get('status')}")
        print(f"  Reason       : {res.get('reason')}")

        if status_code == 200 and res.get("status") == "ignored":
            print("  --> PASS: Successfully ignored closed-unmerged PR without LLM call.")
        else:
            print("  --> FAIL: Expected 200 with status='ignored'.")
            all_passed = False

    # --------------------------------------------------------------------------
    # Case 3: Opened or Synchronized PR (Ignored)
    # --------------------------------------------------------------------------
    if case in ("all", "opened"):
        print("\n[TEST CASE 3] Opened/Updated PR (action='opened', merged=False)")
        payload = build_mock_payload(
            action="opened",
            merged=False,
            owner=owner,
            repo=repo,
            pr_number=pr_number + 2,
            pr_title="WIP: Add new feature",
            pr_body="Work in progress.",
        )
        status_code, res, latency = send_simulation(url, payload, secret)
        print(f"  HTTP Status  : {status_code}")
        print(f"  Latency      : {latency:.3f}s")
        print(f"  Event Status : {res.get('status')}")
        print(f"  Reason       : {res.get('reason')}")

        if status_code == 200 and res.get("status") == "ignored":
            print("  --> PASS: Successfully ignored opened PR without LLM call.")
        else:
            print("  --> FAIL: Expected 200 with status='ignored'.")
            all_passed = False

    print("\n" + "=" * 75)
    if all_passed:
        print(" ALL SIMULATION TEST CASES PASSED SUCCESSFULLY.")
    else:
        print(" SOME SIMULATION TEST CASES FAILED.")
    print("=" * 75 + "\n")

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Simulate GitHub PR webhooks for local testing.")
    parser.add_argument("--url", default="http://localhost:8000/webhook/github", help="Webhook URL endpoint")
    parser.add_argument("--case", choices=["all", "merged", "unmerged", "opened"], default="all", help="Test case to run")
    parser.add_argument("--secret", default=None, help="GitHub Webhook Secret (HMAC) if enabled")
    parser.add_argument("--owner", default="pallets", help="GitHub repo owner for realistic simulation")
    parser.add_argument("--repo", default="flask", help="GitHub repo name for realistic simulation")
    parser.add_argument("--pr", type=int, default=5000, help="PR number for realistic simulation")

    args = parser.parse_args()

    success = run_tests(
        url=args.url,
        case=args.case,
        secret=args.secret,
        owner=args.owner,
        repo=args.repo,
        pr_number=args.pr,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
