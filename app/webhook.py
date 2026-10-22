import hmac
import hashlib
import json
import time
from typing import Set, Dict, Any, Optional

from fastapi import APIRouter, Request, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.logger import (
    logger,
    log_webhook_received,
    log_event_decision,
)
from app.models import WebhookProcessingResponse
from app.github_client import GitHubContextHydrator
from app.synthesizer import synthesize_pr_reflection
from app.email_dispatcher import dispatch_reflection_email

router = APIRouter()

# In-memory delivery/idempotency tracking set
PROCESSED_DELIVERIES: Set[str] = set()

# Initialize context hydrator instance
hydrator = GitHubContextHydrator()


def verify_signature(secret: str, raw_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Validates GitHub HMAC-SHA256 signature from the X-Hub-Signature-256 header.
    """
    if not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    actual_mac = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected_mac, actual_mac)


@router.post("/webhook/github", response_model=WebhookProcessingResponse)
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
    x_github_delivery: Optional[str] = Header(None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """
    FastAPI endpoint ingesting GitHub webhook events.
    Filters exclusively for merged PR events ('closed' and pull_request.merged == True).
    All other events (opened, synchronize, closed-unmerged, ping, etc.) are logged
    and acknowledged with HTTP 200 without invoking the LLM.
    """
    raw_body = await request.body()

    # 1. Webhook Authenticity Verification (HMAC-SHA256)
    if settings.GITHUB_WEBHOOK_SECRET:
        if not verify_signature(settings.GITHUB_WEBHOOK_SECRET, raw_body, x_hub_signature_256):
            logger.warning("[WEBHOOK_AUTH] Invalid or missing X-Hub-Signature-256 signature.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Hub-Signature-256 signature."
            )
    else:
        logger.debug("[WEBHOOK_AUTH] GITHUB_WEBHOOK_SECRET not configured. Skipping HMAC check.")

    # 2. Parse JSON Payload safely
    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        logger.error(f"[WEBHOOK_ERROR] Failed to parse JSON payload: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {exc}"
        )

    # Handle GitHub 'ping' event gracefully
    if x_github_event == "ping" or ("zen" in payload and "hook_id" in payload):
        logger.info("[WEBHOOK_PING] Received GitHub ping event. Webhook is healthy.")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "action": "ping",
                "reason": "GitHub ping event acknowledged successfully."
            }
        )

    action = payload.get("action", "unknown")
    pull_request = payload.get("pull_request")
    repository = payload.get("repository", {})
    repo_name = repository.get("full_name") or repository.get("name") or "unknown/repository"
    owner = repository.get("owner", {}).get("login", "")
    if not owner and "/" in repo_name:
        owner = repo_name.split("/")[0]

    pr_number = None
    pr_title = ""
    is_merged = False
    pr_url = None
    pr_body = ""
    pr_author = "unknown"

    if isinstance(pull_request, dict):
        pr_number = pull_request.get("number")
        pr_title = pull_request.get("title", "")
        is_merged = bool(pull_request.get("merged", False))
        pr_url = pull_request.get("html_url")
        pr_body = pull_request.get("body", "") or ""
        pr_author = pull_request.get("user", {}).get("login", "unknown")

    # 3. Log receipt
    log_webhook_received(
        action=action,
        repo=repo_name,
        pr_number=pr_number or 0,
        pr_title=pr_title or "N/A"
    )

    # 4. Idempotency Check
    idempotency_key = x_github_delivery or f"{repo_name}#{pr_number}#{action}#{pull_request.get('merged_at') if isinstance(pull_request, dict) else ''}"
    if idempotency_key in PROCESSED_DELIVERIES:
        reason = f"Duplicate event delivery detected ({idempotency_key}). Skipping to maintain idempotency."
        log_event_decision(accepted=False, reason=reason, repo=repo_name, pr_number=pr_number or 0)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "action": action,
                "reason": reason,
                "repo": repo_name,
                "pr_number": pr_number
            }
        )

    # 5. Event Filter: Run synthesis pipeline ONLY when action == "closed" and pull_request.merged is True
    if action != "closed":
        reason = f"Event action '{action}' is not 'closed'. Pipeline executes only when a PR is merged."
        log_event_decision(accepted=False, reason=reason, repo=repo_name, pr_number=pr_number or 0)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "action": action,
                "reason": reason,
                "repo": repo_name,
                "pr_number": pr_number
            }
        )

    if not is_merged:
        reason = "Pull request action was 'closed' but merged status is False. PR was discarded or rejected."
        log_event_decision(accepted=False, reason=reason, repo=repo_name, pr_number=pr_number or 0)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "action": action,
                "reason": reason,
                "repo": repo_name,
                "pr_number": pr_number
            }
        )

    if pr_number is None:
        reason = "Payload missing pull_request number."
        log_event_decision(accepted=False, reason=reason, repo=repo_name, pr_number=0)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "action": action,
                "reason": reason,
                "repo": repo_name,
                "pr_number": None
            }
        )

    # Event accepted! Record idempotency key
    PROCESSED_DELIVERIES.add(idempotency_key)
    log_event_decision(
        accepted=True,
        reason="Pull request was merged. Executing context hydration, LCEL synthesis, and email dispatch.",
        repo=repo_name,
        pr_number=pr_number
    )

    start_time = time.perf_counter()

    # 6. Context Hydration (GitHub REST API)
    short_repo = repo_name.split("/")[-1] if "/" in repo_name else repo_name
    hydration_owner = owner or (repo_name.split("/")[0] if "/" in repo_name else "unknown")

    context = await hydrator.hydrate_full_pr_context(
        owner=hydration_owner,
        repo=short_repo,
        pull_number=pr_number,
        pr_body=pr_body,
    )

    # 7. LangChain LCEL Synthesis
    reflection = await synthesize_pr_reflection(
        repo_name=repo_name,
        pr_number=pr_number,
        pr_title=pr_title,
        pr_author=pr_author,
        pr_body=pr_body,
        changed_files_summary=context["changed_files_summary"],
        diff_patch=context["diff_text"],
        issue_context=context["issue_context_str"],
    )

    # 8. Automated Email Dispatch (SMTP)
    email_success, email_msg = await dispatch_reflection_email(
        repo_name=repo_name,
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url,
        reflection=reflection,
    )

    total_duration = time.perf_counter() - start_time
    logger.info(
        f"[PIPELINE_COMPLETE] Merged PR #{pr_number} processed in {total_duration:.2f}s | Email Dispatched: {email_success}"
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "processed",
            "action": action,
            "reason": "PR merged successfully; synthesis and email dispatch completed.",
            "repo": repo_name,
            "pr_number": pr_number,
            "diff_characters": context["total_diff_chars"],
            "reflection": reflection.model_dump(),
            "email_dispatched": email_success,
            "processing_time_seconds": round(total_duration, 3)
        }
    )
