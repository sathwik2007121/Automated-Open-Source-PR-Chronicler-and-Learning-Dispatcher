import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.logger import logger
from app.webhook import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=================================================================")
    logger.info("Starting Automated Open-Source PR Chronicler & Learning Dispatcher")
    logger.info(f"Gemini Model: {settings.GEMINI_MODEL}")
    logger.info(f"GitHub Auth Configured: {'Yes' if settings.GITHUB_TOKEN else 'No (using unauthenticated rate limits)'}")
    logger.info(f"SMTP Configured: {'Yes' if settings.SMTP_EMAIL and settings.SMTP_APP_PASSWORD else 'No (credentials missing in environment)'}")
    logger.info(f"Recipient Inbox: {settings.RECIPIENT_EMAIL or 'Not configured'}")
    logger.info("=================================================================")
    yield
    logger.info("Shutting down PR Chronicler service...")


app = FastAPI(
    title="Automated Open-Source PR Chronicler and Learning Dispatcher",
    description=(
        "Event-driven AI service using FastAPI and LangChain. Listens for GitHub PR events, "
        "filters for merged PRs, hydrates diffs and linked issues via the GitHub REST API, "
        "synthesizes structured technical reflections using LCEL & Google Gemini, and emails "
        "a portfolio log to the student."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for local testing tools and frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = f"{duration:.4f}s"
    return response


# Include Webhook Router (POST /webhook/github)
app.include_router(webhook_router)


@app.get("/health")
async def health_check():
    """Health check endpoint to verify service availability and configuration."""
    return {
        "status": "healthy",
        "service": "Automated Open-Source PR Chronicler",
        "version": "1.0.0",
        "gemini_model": settings.GEMINI_MODEL,
        "github_token_set": bool(settings.GITHUB_TOKEN),
        "smtp_configured": bool(settings.SMTP_EMAIL and settings.SMTP_APP_PASSWORD),
        "recipient": settings.RECIPIENT_EMAIL or None,
    }


@app.get("/")
async def root():
    return {
        "message": "Automated Open-Source PR Chronicler API is running.",
        "webhook_endpoint": "/webhook/github",
        "health_check": "/health",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
