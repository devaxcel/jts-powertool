import logging
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from app.teams.webhook import router as teams_router
from app.slack_router import slack_router
from app.db.session import get_db_connection
from app.log_stream import structured_handler
from app.logs_router import logs_router
from app.db_router import db_router
from app.context_router import context_router
from app.api_approvals import api_approvals_router
from app.channel_secrets_router import channel_secrets_router, vault_router
from app.auth_router import auth_router, users_router, verify_session_token, SESSION_COOKIE_NAME
from app.usage_router import usage_router
from app.invoice_router import invoice_router
from app.github_router import github_public_router, github_folder_router
from app.site_router import site_preview_router, site_drafts_router, websites_router
from app.organizations_router import organizations_router
from app.global_settings_router import global_settings_router

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(structured_handler)

app = FastAPI(title="JTS PowerTool Local Replica", version="0.1.0")

# Enable CORS for Next.js frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication Middleware for Backend REST & Telemetry APIs
PUBLIC_EXEMPT_PREFIXES = (
    "/health",
    "/api/auth/login",
    "/api/auth/verify-setup-token",
    "/api/auth/set-password",
    "/api/slack",
    "/api/teams",
    # GitHub App install flow + webhooks: protected by single-use state / HMAC signature instead of a login
    "/api/github/connect",
    "/api/github/callback",
    "/api/github/webhook",
    # Website draft previews: each URL carries a signed, expiring token for one draft
    "/api/site-preview/",
    "/docs",
    "/openapi.json",
)

import sys, os
IS_TESTING = "unittest" in sys.modules or os.getenv("TESTING", "").lower() in ("true", "1")

@app.middleware("http")
async def enforce_dashboard_authentication(request: Request, call_next):
    if IS_TESTING:
        return await call_next(request)

    path = request.url.path
    
    # 1. Allow public exempt endpoints (health check, webhooks, login)
    if any(path.startswith(prefix) for prefix in PUBLIC_EXEMPT_PREFIXES) or path == "/":
        return await call_next(request)
        
    # 2. Extract token from Authorization header (tab-isolated) or fallback to cookie
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)
            
    # 3. Require a valid signed session token. Role headers alone never grant access.
    if not token or not verify_session_token(token):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required. Please log in to access JTS Console."}
        )
        
    return await call_next(request)

@app.on_event("startup")
async def on_startup():
    try:
        from app.memory_manager import run_database_migrations
        run_database_migrations()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Startup migration failed: {e}")

    # Determine whether to run background worker loop inside FastAPI.
    # In production EC2, jts-worker.service runs `python -m app.worker` independently.
    # Running both causes duplicate workers contending on queue locks.
    # We allow explicit control via RUN_INTERNAL_WORKER ('true'/'false').
    # If not explicitly specified: if running under systemd or AWS production, skip internal worker;
    # in standalone local development, run it for developer convenience.
    env_run_worker = os.getenv("RUN_INTERNAL_WORKER")
    if env_run_worker is not None:
        should_run_worker = env_run_worker.strip().lower() in ("true", "1", "yes")
    else:
        is_production_or_systemd = bool(os.getenv("INVOCATION_ID") or os.getenv("AWS_SECRET_NAME") or os.getenv("AWS_REGION"))
        should_run_worker = not is_production_or_systemd

    if should_run_worker:
        try:
            import asyncio
            from app.worker import run_worker_loop
            asyncio.create_task(run_worker_loop())
            logging.getLogger(__name__).info("Background worker loop initialized on FastAPI startup.")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Worker startup failed: {e}")
    else:
        logging.getLogger(__name__).info(
            "Internal background worker loop skipped in FastAPI (managed by dedicated jts-worker service)."
        )

@app.get("/")
def root():
    return RedirectResponse(url="/login")

@app.get("/health")
def health_check():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        conn.close()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

app.include_router(teams_router, prefix="/api", tags=["teams"])
app.include_router(slack_router)
app.include_router(logs_router)
app.include_router(db_router)
app.include_router(context_router)
app.include_router(api_approvals_router)
app.include_router(channel_secrets_router)
app.include_router(vault_router)
app.include_router(auth_router, prefix="/api/auth")
app.include_router(users_router)
app.include_router(usage_router)
app.include_router(invoice_router)
app.include_router(github_public_router)
app.include_router(github_folder_router)
app.include_router(site_preview_router)
app.include_router(site_drafts_router)
app.include_router(websites_router)
app.include_router(organizations_router)
app.include_router(global_settings_router)
