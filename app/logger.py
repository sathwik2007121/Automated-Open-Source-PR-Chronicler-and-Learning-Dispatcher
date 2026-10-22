import logging
import sys
from pathlib import Path
from typing import Optional

LOG_FILE_PATH = Path("pr_chronicler.log")


def setup_logger(name: str = "pr_chronicler") -> logging.Logger:
    """
    Configures and returns a logger that outputs to both the terminal (StreamHandler)
    and a local file named 'pr_chronicler.log' (FileHandler) with ISO-like timestamps.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers if setup_logger is called repeatedly
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. Terminal StreamHandler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 2. Local FileHandler for pr_chronicler.log
        file_handler = logging.FileHandler(LOG_FILE_PATH, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Ensure propagating to root doesn't cause duplicate lines if root has handlers
    logger.propagate = False
    return logger


logger = setup_logger()


# Structured logging helper utilities matching assignment specification
def log_webhook_received(action: str, repo: str, pr_number: int, pr_title: str) -> None:
    logger.info(
        f"[WEBHOOK_RECEIPT] Action='{action}' | Repo='{repo}' | PR #{pr_number} | Title='{pr_title}'"
    )


def log_event_decision(accepted: bool, reason: str, repo: str, pr_number: int) -> None:
    status = "ACCEPTED" if accepted else "IGNORED"
    logger.info(
        f"[EVENT_DECISION] Status={status} | Repo='{repo}' | PR #{pr_number} | Reason='{reason}'"
    )


def log_github_api(endpoint: str, status_code: int, error: Optional[str] = None) -> None:
    if error:
        logger.error(
            f"[GITHUB_API] Endpoint='{endpoint}' | StatusCode={status_code} | Error='{error}'"
        )
    else:
        logger.info(
            f"[GITHUB_API] Endpoint='{endpoint}' | StatusCode={status_code}"
        )


def log_diff_stats(char_count: int, file_count: int) -> None:
    logger.info(
        f"[DIFF_METRICS] Total Files={file_count} | Diff/Patch Total Characters={char_count}"
    )


def log_parser_output(raw_json: str) -> None:
    logger.info(
        f"[PARSER_OUTPUT] Raw Generated JSON:\n{raw_json}"
    )


def log_smtp_status(success: bool, recipient: str, message: str, exc: Optional[Exception] = None) -> None:
    if success:
        logger.info(
            f"[SMTP_SUCCESS] Transmission successful to '{recipient}' | Details: {message}"
        )
    else:
        err_msg = f"[SMTP_FAILURE] Transmission failed to '{recipient}' | Details: {message}"
        if exc:
            logger.error(err_msg, exc_info=exc)
        else:
            logger.error(err_msg)
