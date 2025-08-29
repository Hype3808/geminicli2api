import logging
import os
import json
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from .gemini_routes import router as gemini_router
from .openai_routes import router as openai_router
from .auth import get_credentials, get_user_project_id, onboard_user
from .dashboard import router as dashboard_router

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    logging.info("Environment variables loaded from .env file")
except ImportError:
    logging.warning("python-dotenv not installed, .env file will not be loaded automatically")
except Exception as e:
    logging.warning(f"Could not load .env file: {e}")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


from contextlib import asynccontextmanager

# Use FastAPI lifespan event handler instead of deprecated on_event
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logging.info("Starting Gemini proxy server with multi-credential support...")
        from .auth import list_credential_files, load_credentials_from_file
        cred_files = await list_credential_files()
        if not cred_files:
            logging.warning("No credential files found in the auth folder. Server will require authentication and credentials setup.")
        else:
            available_projects = []
            for file in cred_files:
                try:
                    creds = await load_credentials_from_file(file)
                    if creds and creds.token:
                        with open(file, 'r') as f:
                            data = json.load(f)
                            pid = data.get('project_id')
                            if pid:
                                available_projects.append(pid)
                except Exception:
                    continue
            if available_projects:
                logging.info(f"Available credential projects: {available_projects}")
                # Optionally, onboard the first available project at startup
                from .auth import get_credentials, get_user_project_id, onboard_user
                creds = await get_credentials(project_id=available_projects[0])
                if creds:
                    try:
                        proj_id = await get_user_project_id(creds)
                        if proj_id:
                            await onboard_user(creds, proj_id)
                            logging.info(f"Successfully onboarded with project ID: {proj_id}")
                        logging.info("Gemini proxy server started successfully with multi-credential support")
                        logging.info("Authentication required - Password: see .env file")
                    except Exception as e:
                        logging.error(f"Setup failed: {str(e)}")
                        logging.warning("Server started but may not function properly until setup issues are resolved.")
                else:
                    logging.warning("Credential file exists but could not be loaded. Server started - authentication will be required on first request.")
            else:
                logging.warning("No valid credentials found in the auth folder. Server will require authentication and credentials setup.")
    except Exception as e:
        logging.error(f"Startup error: {str(e)}")
        logging.warning("Server may not function properly.")
    yield

app = FastAPI(lifespan=lifespan)

# Add CORS middleware for preflight requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)



@app.options("/{full_path:path}")
async def handle_preflight(request: Request, full_path: str):
    """Handle CORS preflight requests without authentication."""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Credentials": "true",
        }
    )

# The dashboard at '/' now provides credential status and upload.

# Health check endpoint for Docker/Hugging Face
@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {"status": "healthy", "service": "geminicli2api"}

app.include_router(dashboard_router)
app.include_router(openai_router)
app.include_router(gemini_router)