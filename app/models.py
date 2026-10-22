from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class PRReflection(BaseModel):
    """
    Structured technical reflection schema produced by the LangChain LCEL pipeline.
    Requires:
    1. issue_details: Root cause of the bug or core feature requirement.
    2. execution_summary: Specific code changes grounded in the PR diff.
    3. key_learnings: Exactly 3 to 4 concrete engineering takeaways.
    """
    issue_details: str = Field(
        ...,
        description="Root cause of the bug or core feature requirement described by maintainers."
    )
    execution_summary: str = Field(
        ...,
        description="Files changed, functions refactored, and logic implemented, grounded directly in the PR diff."
    )
    key_learnings: List[str] = Field(
        ...,
        min_length=3,
        max_length=4,
        description="Three to four concrete engineering takeaways, such as API/library behavior, edge cases, async patterns, or memory considerations."
    )


class GitHubUser(BaseModel):
    login: str = "unknown"
    id: Optional[int] = None
    avatar_url: Optional[str] = None


class GitHubRepoOwner(BaseModel):
    login: str = "unknown"


class GitHubRepository(BaseModel):
    name: str = ""
    full_name: str = ""
    html_url: Optional[str] = None
    owner: Optional[GitHubRepoOwner] = None


class GitHubPullRequest(BaseModel):
    id: Optional[int] = None
    number: int
    title: str = ""
    body: Optional[str] = ""
    state: str = "closed"
    merged: Optional[bool] = False
    merged_at: Optional[str] = None
    user: Optional[GitHubUser] = None
    html_url: Optional[str] = None
    diff_url: Optional[str] = None


class GitHubWebhookPayload(BaseModel):
    action: str
    number: Optional[int] = None
    pull_request: Optional[GitHubPullRequest] = None
    repository: Optional[GitHubRepository] = None
    sender: Optional[GitHubUser] = None
    installation: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"


class WebhookProcessingResponse(BaseModel):
    status: str
    action: str
    reason: str
    repo: Optional[str] = None
    pr_number: Optional[int] = None
    diff_characters: Optional[int] = 0
    reflection: Optional[PRReflection] = None
    email_dispatched: Optional[bool] = False
    processing_time_seconds: Optional[float] = None
