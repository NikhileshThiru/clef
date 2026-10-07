"""One-time Gmail login. Run: uv run python -m clefd.gmail_auth

Opens a local callback on port 8765. From the Mac, first run
    ssh -L 8765:localhost:8765 nikhilesh@<laptop>
so the browser's redirect back to localhost:8765 reaches the laptop.
"""
import os
import time

from google_auth_oauthlib.flow import InstalledAppFlow

from .ingest.mail import CLIENT_FILE, LOGIN_FILE, SCOPES, TOKEN_FILE


def main() -> None:
    if not CLIENT_FILE.exists():
        raise SystemExit(f"Missing {CLIENT_FILE}. Download the Desktop OAuth client JSON from Google Cloud and put it there.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(
        host="localhost", port=8765, open_browser=False,
        authorization_prompt_message="\nOpen this URL in a browser and approve read-only Gmail access:\n\n{url}\n",
        success_message="Clef is connected to Gmail. You can close this tab.",
        access_type="offline", prompt="consent",
    )
    TOKEN_FILE.write_text(creds.to_json())
    os.chmod(TOKEN_FILE, 0o600)
    LOGIN_FILE.write_text(str(time.time()))
    print(f"\nSaved {TOKEN_FILE}. clefd picks it up within 30 s, no restart needed.")


if __name__ == "__main__":
    main()
