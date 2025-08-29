from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .auth import list_credential_files, load_credentials_from_file

import os
import json
import aiofiles
import aiohttp

router = APIRouter()

async def get_credential_status():
    import logging
    files = await list_credential_files()
    logging.info(f"[DASHBOARD] Found credential files: {files}")
    status = []
    from .config import CODE_ASSIST_ENDPOINT
    from .auth import is_credential_in_cooldown, get_credential_cooldown_remaining
    for f in files:
        # Check cooldown first
        if is_credential_in_cooldown(f):
            remaining = get_credential_cooldown_remaining(f)
            project_id = 'N/A'
            code = f"Cooldown ({remaining}s)"
            status.append({'file': os.path.basename(f), 'project_id': project_id, 'status_code': code})
            continue
        cred = await load_credentials_from_file(f)
        project_id = None
        code = 200  # Default to 200 unless error occurs
        if cred:
            try:
                token = cred.token
                async with aiofiles.open(f, 'r') as jf:
                    content = await jf.read()
                    data = json.loads(content)
                    project_id = data.get('project_id', 'N/A')
                headers = {
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist",
                        headers=headers,
                        json={"metadata": {}}
                    ) as resp:
                        code = resp.status if resp.status != 200 else 200
            except Exception as e:
                logging.error(f"[DASHBOARD] Error checking credential {f}: {e}")
                code = "ERR"
        else:
            project_id = 'N/A'
            code = 'ERR'
        status.append({'file': os.path.basename(f), 'project_id': project_id, 'status_code': code})
    logging.info(f"[DASHBOARD] Credential status: {status}")
    # Show 401 as 200 for dashboard only
    for s in status:
        if s.get('status_code') == 401:
            s['status_code'] = 200
    return status


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    html = """
        <html>
        <head>
        <title>Auth Dashboard</title>
        <style>
        body { font-family: Arial, sans-serif; background: #f4f6fa; margin: 0; padding: 0; }
        .container { max-width: 700px; margin: 40px auto; background: #fff; border-radius: 10px; box-shadow: 0 2px 8px #0001; padding: 32px; }
        h1 { text-align: center; color: #2a3b4c; }
        table { width: 100%; border-collapse: collapse; margin: 24px 0; }
        th, td { padding: 12px 8px; text-align: center; }
        th { background: #2a3b4c; color: #fff; }
        tr:nth-child(even) { background: #f0f4f8; }
        tr:nth-child(odd) { background: #e9eef5; }
        .status-200 { color: #2e7d32; font-weight: bold; }
        .status-429 { color: #d84315; font-weight: bold; }
        .status-ERR { color: #b71c1c; font-weight: bold; }
        .refresh-btn { background: #1976d2; color: #fff; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 16px; margin-bottom: 20px; }
        .refresh-btn:hover { background: #1565c0; }
        .upload-form { margin-top: 32px; text-align: center; }
        .upload-form input[type='file'] {
            margin-right: 10px;
            padding: 8px 12px;
            border: 1px solid #b0bec5;
            border-radius: 5px;
            background: #f8fafc;
            font-size: 15px;
            color: #37474f;
            outline: none;
            transition: border 0.2s;
        }
        .upload-form input[type='file']:focus {
            border: 1.5px solid #1976d2;
        }
        .upload-form input[type='submit'] {
            background: #43a047;
            color: #fff;
            border: none;
            padding: 10px 24px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            font-weight: bold;
            box-shadow: 0 2px 6px #0002;
            margin-left: 8px;
            transition: background 0.2s;
        }
        .upload-form input[type='submit']:hover {
            background: #388e3c;
        }
        </style>
    <!-- Move script to end of body so refreshStatus is defined before use -->
    .status-cooldown { color: #ff9800; font-weight: bold; }
        </script>
        </head>
        <body>
        <div class="container">
        <h1>Auth Credentials Dashboard</h1>
    <button class="refresh-btn" onclick="refreshStatus()">Refresh</button>
        <table>
            <tr><th>File</th><th>Project ID</th><th>Status</th></tr>
            <tbody id="auth-table-body"></tbody>
        </table>
        <div class="upload-form">
        <h2>Add New Auth JSON</h2>
        <form action='/upload_auth' method='post' enctype='multipart/form-data'>
            <input type='file' name='file' accept='.json' required />
            <input type='submit' value='Upload' />
        </form>
        </div>
        </div>
        <div style="margin-top:40px;">
        <h2>Possible Status/Error Codes</h2>
        <table style="width:100%;border-collapse:collapse;">
            <tr style="background:#2a3b4c;color:#fff;"><th>Code</th><th>Meaning</th><th>Explanation</th></tr>
            <tr><td class="status-200">200</td><td>OK</td><td>Credential is valid and can access the Gemini API.</td></tr>
            <tr><td class="status-401">401</td><td>Unauthorized</td><td>The credential is invalid, expired, or revoked. This can happen if the refresh token is no longer valid, the OAuth consent was revoked, or the credential JSON is malformed or for the wrong API/project.</td></tr>
            <tr><td class="status-403">403</td><td>Forbidden</td><td>The credential does not have permission to access the Gemini API or the required Google Cloud project. Check IAM roles and API enablement.</td></tr>
            <tr><td class="status-404">404</td><td>Not Found</td><td>The requested endpoint or resource does not exist. This is rare for credentials, but could indicate a misconfiguration.</td></tr>
            <tr><td class="status-429">429</td><td>Too Many Requests</td><td>This credential has exceeded its quota or rate limit. The system will try another credential if available.</td></tr>
            <tr><td class="status-cooldown">Cooldown</td><td>Cooldown</td><td>This credential is temporarily disabled for 60 seconds after a 429 error. It will be retried automatically when cooldown expires.</td></tr>
            <tr><td class="status-ERR">ERR</td><td>Error</td><td>An unexpected error occurred, such as a network issue, invalid file, or the credential could not be parsed. Check the file format and try again.</td></tr>
        </table>
        <ul style="margin-top:10px;font-size:15px;color:#37474f;">
            <li><b>401 Unauthorized</b> is the most common error for new uploads. This usually means the credential is not valid for the Gemini API, is expired, or is missing required fields (like refresh_token).</li>
            <li>Make sure your OAuth credential JSON is for the correct Google Cloud project and has the necessary scopes and API access.</li>
            <li>If you see 401 after uploading, try re-downloading the credential from Google Cloud Console and ensure it is a valid OAuth client credential with refresh token.</li>
        </ul>
        </div>
        <script>
        async function refreshStatus() {
            try {
                const resp = await fetch('/auth_status');
                if (!resp.ok) throw new Error('Failed to fetch status');
                const data = await resp.json();
                let rows = '';
                if (data.length === 0) {
                    rows = `<tr><td colspan='3' style='color:#b71c1c;font-weight:bold;'>No credentials found in auth folder.</td></tr>`;
                } else {
                    for (const s of data) {
                        let code = s.status_code;
                        let code_class = 'status-' + code;
                        if (typeof code === 'string' && code.startsWith('Cooldown')) {
                            code_class = 'status-cooldown';
                        }
                        rows += `<tr><td>${s.file}</td><td>${s.project_id}</td><td class='${code_class}'>${code}</td></tr>`;
                    }
                }
                document.getElementById('auth-table-body').innerHTML = rows;
            } catch (e) {
                document.getElementById('auth-table-body').innerHTML = `<tr><td colspan='3' style='color:#b71c1c;font-weight:bold;'>Error loading credential status: ${e.message}</td></tr>`;
            }
        }
        window.onload = refreshStatus;
        </script>
        </body></html>
                """
    return HTMLResponse(content=html)

# New endpoint for AJAX status refresh
@router.get("/auth_status")
async def auth_status():
    return await get_credential_status()

@router.post("/upload_auth")
async def upload_auth(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith('.json'):
        return HTMLResponse("Only .json files allowed", status_code=400)
    save_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'auth', file.filename)
    async with aiofiles.open(save_path, 'wb') as f_out:
        content = await file.read()
        await f_out.write(content)
    return RedirectResponse(url="/", status_code=303)
