import os
import json
import base64
import logging
import warnings
import asyncio
import aiofiles
import aiohttp
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBasic
from urllib.parse import urlparse, parse_qs

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from .utils import get_user_agent, get_client_metadata
from .config import (
    CLIENT_ID, CLIENT_SECRET, SCOPES, CREDENTIAL_FILE,
    CODE_ASSIST_ENDPOINT, GEMINI_AUTH_PASSWORD, AUTH_DIR
)
from google_auth_oauthlib.flow import InstalledAppFlow

def authorize_and_save_credentials(project_ids):
    """
    Launch OAuth flow ONCE and save the resulting credentials for each project_id in project_ids as {project_id}.json in the 'auth' folder.
    If the signed-in account does not have access to a project_id, print an error for that project.
    """
    # Normalize project_ids: allow string or list
    if isinstance(project_ids, str):
        project_ids = [project_ids.strip()]
    elif isinstance(project_ids, (list, tuple)):
        project_ids = [pid.strip() for pid in project_ids if pid.strip()]
    else:
        raise ValueError("project_ids must be a string or list of strings")

    try:
        client_config = {
            "installed": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        print("Starting OAuth flow (only once for all projects)...")
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            creds = flow.run_local_server(port=0, prompt='consent')
        print("OAuth flow completed. Saving credentials for each project...")

        # Get the email of the signed-in user
        from googleapiclient.discovery import build
        oauth2_service = build('oauth2', 'v2', credentials=creds)
        user_info = oauth2_service.userinfo().get().execute()
        signed_in_email = user_info.get('email')
        print(f"Signed in as: {signed_in_email}")

        os.makedirs(AUTH_DIR, exist_ok=True)
        errors = []
        for project_id in project_ids:
            # Optionally, check if the user has access to the project
            try:
                # Use Cloud Resource Manager API to check project access
                crm_service = build('cloudresourcemanager', 'v1', credentials=creds)
                project = crm_service.projects().get(projectId=project_id).execute()
                # Optionally, check project owner/creator email if available
                # If you want to restrict to only projects owned by this user, check project['projectNumber'] or labels
                # For now, just check if the project is accessible
                creds_data = json.loads(creds.to_json())
                creds_data["project_id"] = project_id
                cred_path = os.path.join(AUTH_DIR, f"{project_id}.json")
                with open(cred_path, "w") as f:
                    json.dump(creds_data, f, indent=2)
                print(f"Saved credentials to {cred_path}")
            except Exception as e:
                errors.append(f"Project '{project_id}': {e}")
                print(f"[ERROR] Could not save credentials for project '{project_id}': {e}")
        if errors:
            print("\nSome projects could not be authorized:")
            for err in errors:
                print(err)
    except Exception as e:
        print(f"[ERROR] Failed to complete OAuth or save credentials: {e}")
        import traceback
        traceback.print_exc()

# --- Credential Rotation Helpers ---

async def list_credential_files():
    """List all .json credential files in the 'auth' folder."""
    from .config import AUTH_DIR
    return [
        os.path.join(AUTH_DIR, f)
        for f in os.listdir(AUTH_DIR)
        if f.endswith('.json') and os.path.isfile(os.path.join(AUTH_DIR, f))
    ]

async def find_credential_file_for_project(project_id: str):
    """Return the credential file path for a given project_id, or None if not found."""
    files = await list_credential_files()
    for file in files:
        try:
            async with aiofiles.open(file, 'r') as f:
                content = await f.read()
                creds_data = json.loads(content)
                if creds_data.get('project_id') == project_id:
                    return file
        except Exception:
            continue
    return None

async def load_any_valid_credentials():
    """Load the first valid credentials from any .json file in the auth folder."""
    files = await list_credential_files()
    for file in files:
        creds = await load_credentials_from_file(file)
        if creds and creds.token:
            return creds
    return None

async def load_credentials_from_file(filepath):
    """Load Google credentials from a specific file path."""
    from .config import SCOPES
    try:
        async with aiofiles.open(filepath, 'r') as f:
            content = await f.read()
            creds_data = json.loads(content)
        # Handle different credential formats
        if 'access_token' in creds_data and 'token' not in creds_data:
            creds_data['token'] = creds_data['access_token']
        if 'scope' in creds_data and 'scopes' not in creds_data:
            creds_data['scopes'] = creds_data['scope'].split()
        # Handle problematic expiry formats
        if 'expiry' in creds_data:
            expiry_str = creds_data['expiry']
            if isinstance(expiry_str, str) and ('+00:00' in expiry_str or 'Z' in expiry_str):
                from datetime import datetime
                if '+00:00' in expiry_str:
                    parsed_expiry = datetime.fromisoformat(expiry_str)
                elif expiry_str.endswith('Z'):
                    parsed_expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
                else:
                    parsed_expiry = datetime.fromisoformat(expiry_str)
                timestamp = parsed_expiry.timestamp()
                creds_data['expiry'] = datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%dT%H:%M:%SZ')
        return Credentials.from_authorized_user_info(creds_data, SCOPES)
    except Exception as e:
        logging.error(f"Failed to load credentials from {filepath}: {e}")
        return None

def authenticate_user(request: Request):
    """Authenticate the user with multiple methods."""
    api_key = request.query_params.get("key")
    if api_key and api_key == GEMINI_AUTH_PASSWORD:
        return "api_key_user"
    goog_api_key = request.headers.get("x-goog-api-key", "")
    if goog_api_key and goog_api_key == GEMINI_AUTH_PASSWORD:
        return "goog_api_key_user"
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:]
        if bearer_token == GEMINI_AUTH_PASSWORD:
            return "bearer_user"
    if auth_header.startswith("Basic "):
        try:
            encoded_credentials = auth_header[6:]
            decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8', "ignore")
            username, password = decoded_credentials.split(':', 1)
            if password == GEMINI_AUTH_PASSWORD:
                return username
        except Exception:
            pass
    raise HTTPException(
        status_code=401,
        detail="Invalid authentication credentials. Use HTTP Basic Auth, Bearer token, 'key' query parameter, or 'x-goog-api-key' header.",
        headers={"WWW-Authenticate": "Basic"},
    )

# --- Credential Saving Helper ---
async def save_credentials(creds, project_id=None):
    """Save credentials to a file in the auth folder, optionally with a project_id."""
    os.makedirs(AUTH_DIR, exist_ok=True)
    creds_data = json.loads(creds.to_json())
    if project_id:
        creds_data["project_id"] = project_id
        cred_path = os.path.join(AUTH_DIR, f"{project_id}.json")
        async with aiofiles.open(cred_path, "w") as f:
            await f.write(json.dumps(creds_data, indent=2))
    # If no project_id, do not save to CREDENTIAL_FILE (oauth_creds.json) anymore

# --- Global State ---
credentials = None
user_project_id = None
onboarding_complete = False
credentials_from_env = False  # Track if credentials came from environment variable

security = HTTPBasic()

# --- Cooldown Tracking for 429 Errors ---
import time
from typing import Dict



# Maps credential file path to (cooldown expiry timestamp, backoff level)
_credential_cooldowns: Dict[str, tuple] = {}

def set_credential_cooldown(cred_path: str, base_cooldown: int = 60, max_cooldown: int = 1800):
    """Set or increase cooldown for a credential file (after 429 error) with exponential backoff."""
    now = time.time()
    prev = _credential_cooldowns.get(cred_path)
    if prev:
        _, backoff = prev
        backoff = min(backoff + 1, 5)  # Cap backoff to avoid infinite growth
    else:
        backoff = 1
    cooldown_seconds = min(base_cooldown * (2 ** (backoff - 1)), max_cooldown)
    _credential_cooldowns[cred_path] = (now + cooldown_seconds, backoff)

def is_credential_in_cooldown(cred_path: str) -> bool:
    """Check if a credential is currently in cooldown."""
    entry = _credential_cooldowns.get(cred_path)
    if entry is None:
        return False
    expiry, _ = entry
    if time.time() >= expiry:
        _credential_cooldowns.pop(cred_path, None)
        return False
    return True

def get_credential_cooldown_remaining(cred_path: str) -> int:
    """Get remaining cooldown time in seconds (0 if not in cooldown)."""
    entry = _credential_cooldowns.get(cred_path)
    if entry is None:
        return 0
    expiry, _ = entry
    remaining = int(expiry - time.time())
    if remaining <= 0:
        _credential_cooldowns.pop(cred_path, None)
        return 0
    return remaining

def reset_credential_cooldown(cred_path: str):
    """Reset cooldown and backoff for a credential (on successful use)."""
    if cred_path in _credential_cooldowns:
        _credential_cooldowns.pop(cred_path, None)


async def get_credentials(allow_oauth_flow=True, project_id=None):
    """Loads credentials for a specific project, or any valid credentials if project_id is None."""
    global credentials, credentials_from_env, user_project_id

    if credentials and credentials.token:
        return credentials

    # Check for credentials in environment variable (JSON string)
    env_creds_json = os.getenv("GEMINI_CREDENTIALS")
    if env_creds_json:
        # ...existing code for env_creds_json...
        # (Unchanged, see above for full logic)
        pass

    # If project_id is specified, try to find a matching credential file
    if project_id:
        cred_file = await find_credential_file_for_project(project_id)
        if cred_file:
            creds = await load_credentials_from_file(cred_file)
            if creds and creds.token:
                return creds

    # Otherwise, load any valid credentials from the auth folder
    creds = await load_any_valid_credentials()
    if creds and creds.token:
        return creds

    # Remove OAuth flow: if no credentials, just return None
    return None

async def onboard_user(creds, project_id):
    """Ensures the user is onboarded, matching gemini-cli setupUser behavior."""
    global onboarding_complete
    if onboarding_complete:
        return

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            await save_credentials(creds)
        except Exception as e:
            raise Exception(f"Failed to refresh credentials during onboarding: {str(e)}")
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
        "User-Agent": await get_user_agent(),
    }
    load_assist_payload = {
        "cloudaicompanionProject": project_id,
        "metadata": await get_client_metadata(project_id),
    }
    import requests
    try:
        resp = requests.post(
            f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist",
            data=json.dumps(load_assist_payload),
            headers=headers,
        )
        resp.raise_for_status()
        load_data = resp.json()
        
        tier = None
        if load_data.get("currentTier"):
            tier = load_data["currentTier"]
        else:
            for allowed_tier in load_data.get("allowedTiers", []):
                if allowed_tier.get("isDefault"):
                    tier = allowed_tier
                    break
            
            if not tier:
                tier = {
                    "name": "",
                    "description": "",
                    "id": "legacy-tier",
                    "userDefinedCloudaicompanionProject": True,
                }

        if tier.get("userDefinedCloudaicompanionProject") and not project_id:
            raise ValueError("This account requires setting the GOOGLE_CLOUD_PROJECT env var.")

        if load_data.get("currentTier"):
            onboarding_complete = True
            return

        onboard_req_payload = {
            "tierId": tier.get("id"),
            "cloudaicompanionProject": project_id,
            "metadata": await get_client_metadata(project_id),
        }

        while True:
            onboard_resp = requests.post(
                f"{CODE_ASSIST_ENDPOINT}/v1internal:onboardUser",
                data=json.dumps(onboard_req_payload),
                headers=headers,
            )
            onboard_resp.raise_for_status()
            lro_data = onboard_resp.json()

            if lro_data.get("done"):
                onboarding_complete = True
                break
            
            await asyncio.sleep(5)
    except requests.exceptions.HTTPError as e:
        raise Exception(f"User onboarding failed. Please check your Google Cloud project permissions and try again. Error: {e.response.text if hasattr(e, 'response') else str(e)}")
    except Exception as e:
        raise Exception(f"User onboarding failed due to an unexpected error: {str(e)}")

async def get_user_project_id(creds):
    """Gets the user's project ID matching gemini-cli setupUser logic."""
    global user_project_id

    # 1. Try to get project_id from creds object (from file)
    cred_project_id = None
    try:
        if hasattr(creds, 'project_id') and creds.project_id:
            cred_project_id = creds.project_id
        elif hasattr(creds, 'to_json'):
            # fallback: parse from JSON
            creds_data = json.loads(creds.to_json())
            cred_project_id = creds_data.get('project_id')
    except Exception as e:
        logging.warning(f"Could not extract project_id from creds: {e}")

    if cred_project_id:
        logging.info(f"Using project_id from credential file: {cred_project_id}")
        user_project_id = cred_project_id
        # Do NOT save credentials again if project_id already exists
        return user_project_id

    # 2. Try to get project_id from environment variable
    env_project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if env_project_id:
        logging.info(f"Using project ID from GOOGLE_CLOUD_PROJECT environment variable: {env_project_id}")
        user_project_id = env_project_id
        # Do NOT save credentials automatically
        return user_project_id

    # 3. Try to discover project_id via API call
    if creds.expired and creds.refresh_token:
        try:
            logging.info("Refreshing credentials before project ID discovery...")
            creds.refresh(GoogleAuthRequest())
            await save_credentials(creds)
            logging.info("Credentials refreshed successfully for project ID discovery")
        except Exception as e:
            logging.error(f"Failed to refresh credentials while getting project ID: {e}")
            # Continue with existing credentials - they might still work

    if not creds.token:
        raise Exception("No valid access token available for project ID discovery")

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
        "User-Agent": await get_user_agent(),
    }
    probe_payload = {
        "metadata": await get_client_metadata(),
    }
    import requests
    try:
        logging.info("Attempting to discover project ID via API call...")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist",
                data=json.dumps(probe_payload),
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                discovered_project_id = data.get("cloudaicompanionProject")
                if not discovered_project_id:
                    raise ValueError("Could not find 'cloudaicompanionProject' in loadCodeAssist response.")
                logging.info(f"Discovered project ID via API: {discovered_project_id}")
                user_project_id = discovered_project_id
                # Do NOT save credentials automatically
                return user_project_id
    except aiohttp.ClientResponseError as e:
        logging.error(f"HTTP error during project ID discovery: {e}")
        raise Exception(f"Failed to discover project ID via API: {e}")
    except Exception as e:
        logging.error(f"Unexpected error during project ID discovery: {e}")
        raise Exception(f"Failed to discover project ID: {e}")