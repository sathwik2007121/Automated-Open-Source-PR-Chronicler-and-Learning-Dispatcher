import re
from typing import Dict, Any, List, Optional, Tuple
import httpx

from app.config import settings
from app.logger import logger, log_github_api, log_diff_stats

# Regex to capture GitHub closing issue references like:
# "Fixes #12", "Fixed #45", "Closes #10", "Closed: #99", "Resolves #102",
# or full URLs like "Fixes https://github.com/owner/repo/issues/12"
ISSUE_REF_PATTERN = re.compile(
    r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*[:#-]?\s*(?:https?://github\.com/[^/\s]+/[^/\s]+/issues/|#)?(\d+)\b"
)


class GitHubContextHydrator:
    """
    Handles asynchronous GitHub REST API hydration for pull requests,
    files, diffs, and linked issues.
    """

    def __init__(self, token: Optional[str] = None, base_url: str = "https://api.github.com"):
        self.base_url = base_url.rstrip("/")
        self.token = token or settings.GITHUB_TOKEN
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Automated-OS-PR-Chronicler/1.0",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def extract_issue_numbers(self, pr_body: Optional[str]) -> List[int]:
        """
        Extracts linked issue numbers from the PR description using regex matching
        closing patterns (e.g., 'Fixes #12', 'Closes #42', 'Resolves #99').
        """
        if not pr_body:
            return []

        matches = ISSUE_REF_PATTERN.findall(pr_body)
        unique_issues: List[int] = []
        for match in matches:
            try:
                num = int(match)
                if num not in unique_issues:
                    unique_issues.append(num)
            except ValueError:
                continue
        return unique_issues

    async def fetch_pr_files(
        self, owner: str, repo: str, pull_number: int
    ) -> Tuple[List[Dict[str, Any]], str, int]:
        """
        Fetches changed files and patch snippets from GET /repos/{owner}/{repo}/pulls/{pull_number}/files.
        Handles pagination and records total character count.
        Returns:
            (changed_files_list, combined_patch_text, total_character_count)
        """
        endpoint = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pull_number}/files"
        changed_files: List[Dict[str, Any]] = []
        combined_patches: List[str] = []
        page = 1
        per_page = 100

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            while True:
                url = f"{endpoint}?per_page={per_page}&page={page}"
                try:
                    resp = await client.get(url, headers=self.headers)
                    log_github_api(f"GET {url}", resp.status_code)

                    if resp.status_code == 403 and "rate limit" in resp.text.lower():
                        logger.error(
                            f"[GITHUB_API] Rate limit exceeded on {endpoint}. "
                            f"Remaining: {resp.headers.get('x-ratelimit-remaining')}"
                        )
                        break

                    if resp.status_code == 401:
                        logger.error("[GITHUB_API] Authentication failed. Check GITHUB_TOKEN.")
                        break

                    if resp.status_code != 200:
                        logger.warning(
                            f"[GITHUB_API] Failed to fetch PR files: {resp.status_code} - {resp.text[:200]}"
                        )
                        break

                    files_batch = resp.json()
                    if not files_batch or not isinstance(files_batch, list):
                        break

                    for file_item in files_batch:
                        filename = file_item.get("filename", "unknown")
                        status = file_item.get("status", "modified")
                        additions = file_item.get("additions", 0)
                        deletions = file_item.get("deletions", 0)
                        patch = file_item.get("patch", "")

                        if not patch:
                            patch_summary = (
                                f"--- {filename} ({status}, +{additions}/-{deletions})\n"
                                f"[Patch omitted by GitHub: large or binary file]"
                            )
                        else:
                            patch_summary = (
                                f"--- {filename} ({status}, +{additions}/-{deletions})\n{patch}"
                            )

                        changed_files.append({
                            "filename": filename,
                            "status": status,
                            "additions": additions,
                            "deletions": deletions,
                            "patch_length": len(patch) if patch else 0
                        })
                        combined_patches.append(patch_summary)

                    if len(files_batch) < per_page:
                        break
                    page += 1

                except httpx.RequestError as exc:
                    log_github_api(url, 0, error=str(exc))
                    break

        # If combined_patches is empty due to API failure or empty PR, provide a fallback note
        full_diff_text = "\n\n".join(combined_patches) if combined_patches else "[No diff content retrieved]"
        total_chars = len(full_diff_text)
        log_diff_stats(char_count=total_chars, file_count=len(changed_files))

        return changed_files, full_diff_text, total_chars

    async def fetch_issue_context(
        self, owner: str, repo: str, issue_number: int
    ) -> Dict[str, Any]:
        """
        Retrieves linked issue details from GET /repos/{owner}/{repo}/issues/{issue_number}.
        Handles absent or unavailable issues gracefully.
        """
        endpoint = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            try:
                resp = await client.get(endpoint, headers=self.headers)
                log_github_api(f"GET {endpoint}", resp.status_code)

                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "issue_number": issue_number,
                        "title": data.get("title", ""),
                        "body": data.get("body", "") or "[No description provided in issue]",
                        "labels": [lbl.get("name") for lbl in data.get("labels", []) if isinstance(lbl, dict)],
                        "found": True,
                    }
                elif resp.status_code == 404:
                    logger.info(f"[GITHUB_API] Issue #{issue_number} not found (404).")
                    return {
                        "issue_number": issue_number,
                        "found": False,
                        "note": f"Issue #{issue_number} was not found on repository {owner}/{repo}.",
                    }
                else:
                    logger.warning(
                        f"[GITHUB_API] Issue #{issue_number} returned status {resp.status_code}: {resp.text[:150]}"
                    )
                    return {
                        "issue_number": issue_number,
                        "found": False,
                        "note": f"HTTP {resp.status_code} while fetching issue #{issue_number}.",
                    }
            except httpx.RequestError as exc:
                log_github_api(endpoint, 0, error=str(exc))
                return {
                    "issue_number": issue_number,
                    "found": False,
                    "note": f"Network exception while fetching issue #{issue_number}: {exc}",
                }

    async def hydrate_full_pr_context(
        self, owner: str, repo: str, pull_number: int, pr_body: Optional[str]
    ) -> Dict[str, Any]:
        """
        Aggregates PR file diffs and all linked issue details into a single
        structured dictionary for the LLM synthesis pipeline.
        """
        changed_files, diff_text, total_chars = await self.fetch_pr_files(owner, repo, pull_number)

        issue_numbers = self.extract_issue_numbers(pr_body)
        linked_issues: List[Dict[str, Any]] = []

        if issue_numbers:
            logger.info(
                f"[CONTEXT_HYDRATION] Found linked issue reference(s): {issue_numbers} in PR #{pull_number}"
            )
            for num in issue_numbers[:3]:  # Hydrate up to 3 referenced issues
                issue_ctx = await self.fetch_issue_context(owner, repo, num)
                linked_issues.append(issue_ctx)
        else:
            logger.info(
                f"[CONTEXT_HYDRATION] No linked issue reference found in PR #{pull_number} body."
            )

        # Build readable issue summary string
        if linked_issues:
            issue_summary_lines = []
            for item in linked_issues:
                if item.get("found"):
                    issue_summary_lines.append(
                        f"Issue #{item['issue_number']}: {item['title']}\n"
                        f"Description:\n{item['body']}"
                    )
                else:
                    issue_summary_lines.append(
                        f"Issue #{item['issue_number']}: {item.get('note')}"
                    )
            issue_context_str = "\n\n".join(issue_summary_lines)
        else:
            issue_context_str = (
                "No linked issue reference found in PR body. Rely directly on the PR description "
                "and code diff to ascertain requirements and root cause."
            )

        changed_files_summary = ", ".join([f["filename"] for f in changed_files]) if changed_files else "None"

        return {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "changed_files": changed_files,
            "changed_files_summary": changed_files_summary,
            "diff_text": diff_text,
            "total_diff_chars": total_chars,
            "linked_issues": linked_issues,
            "issue_context_str": issue_context_str,
        }
