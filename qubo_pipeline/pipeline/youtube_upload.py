"""
youtube_upload.py

Uploads a single video file to YouTube as a PRIVATE, NOT-made-for-kids video,
and adds it to a playlist (created automatically if it doesn't exist yet).
Called by Run_Qubo_Pipeline_YouTube.bat after the stacked video is created,
and by concat-and-upload.bat after concatenating a folder of clips.

Usage:
    python youtube_upload.py "<path_to_video>" "<title>" ["<playlist_name>"] ["<privacy>"]

    playlist_name is optional and defaults to "Dashcam" (kept for backward
    compatibility with the original dashcam pipeline). Pass a different
    name to file the upload into a different playlist - it will be
    created automatically if it doesn't exist yet.

    privacy is optional and defaults to "private". Accepted values:
    "private", "public", "unlisted". To pass privacy without overriding
    the playlist, pass the playlist name explicitly even if it's just
    the default, e.g.: ... "Dashcam" "public"

Sign-in behavior:
    A browser window opens asking you to log into the Google account that
    owns the destination YouTube channel, and approve access. A token is
    then saved (token.json) next to this script so future runs don't
    prompt again - UNLESS the project's OAuth consent screen is still in
    "Testing" status, in which case Google expires the sign-in after 7
    days. If that happens, this script detects it automatically and
    re-opens the browser for a quick one-click re-approval - no manual
    deletion of token.json needed, just click through the prompt when it
    appears.

Requirements (one-time setup):
    pip install --upgrade google-api-python-client google-auth-oauthlib google-auth-httplib2
    A client_secret.json file (downloaded from Google Cloud Console) must
    be placed in a "credentials" subfolder next to this script, i.e.:
        <this folder>\\credentials\\client_secret.json
    token.json will be created automatically in that same folder after
    your first successful sign-in - no need to create it yourself.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # one level up from pipeline/
CREDENTIALS_DIR = os.path.join(PROJECT_ROOT, "credentials")
CLIENT_SECRETS_FILE = os.path.join(CREDENTIALS_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "token.json")
# youtube.upload alone is not enough to create/manage playlists.
# youtube.force-ssl covers upload + playlists.insert + playlistItems.insert.
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
DEFAULT_PLAYLIST_NAME = "Dashcam"
# The normal pipeline uploads each video to every playlist in this tuple.
# Keep DEFAULT_PLAYLIST_NAME for the standalone uploader's CLI compatibility.
DEFAULT_PLAYLIST_NAMES = (DEFAULT_PLAYLIST_NAME,)
DEFAULT_PRIVACY = "private"
DEFAULT_PLAYLIST_PRIVACY = "private"
DEFAULT_MADE_FOR_KIDS = False
PRIVACY_OPTIONS = {"private", "public", "unlisted"}


def fail(message, code=1):
    # Single, clear, prefixed error line so the calling .bat can show it
    # plainly and the user can tell pipeline errors apart from upload errors.
    print(f"[YOUTUBE_UPLOAD_ERROR] {message}")
    sys.exit(code)


def get_authenticated_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        fail(
            "Required Python packages are missing. Run this once:\n"
            "  pip install --upgrade google-api-python-client google-auth-oauthlib google-auth-httplib2\n"
            f"  (import error: {e})"
        )

    if not os.path.exists(CLIENT_SECRETS_FILE):
        fail(
            f"client_secret.json not found at: {CLIENT_SECRETS_FILE}\n"
            "Download it from Google Cloud Console (APIs & Services > Credentials)\n"
            f"and place it in: {CREDENTIALS_DIR}"
        )

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            # Corrupt/unreadable token file - treat as no saved token at all
            # and fall through to a fresh sign-in below, rather than failing.
            print("[INFO] Saved token.json could not be read - will sign in again.")
            creds = None

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except Exception as e:
                # This is the expected case once the weekly Testing-mode
                # token expires (refresh_token itself stops working, not
                # just the access token). Don't fail the run - just fall
                # through to a fresh browser sign-in below.
                print(f"[INFO] Saved sign-in has expired ({e}). Opening browser to sign in again...")

        if not refreshed:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                fail(
                    f"Google sign-in failed: {e}\n"
                    "If a browser window did not open, check that a default browser is set on this machine."
                )

        try:
            os.makedirs(CREDENTIALS_DIR, exist_ok=True)
            with open(TOKEN_FILE, "w") as token_out:
                token_out.write(creds.to_json())
        except Exception as e:
            fail(f"Signed in successfully but could not save token.json: {e}")

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        fail(f"Failed to initialize YouTube API client: {e}")


def get_or_create_playlist(youtube, playlist_name):
    from googleapiclient.errors import HttpError

    # Look for an existing playlist with this exact name on the channel.
    try:
        request = youtube.playlists().list(part="snippet", mine=True, maxResults=50)
        while request is not None:
            response = request.execute()
            for item in response.get("items", []):
                if item["snippet"]["title"] == playlist_name:
                    return item["id"]
            request = youtube.playlists().list_next(request, response)
    except HttpError as e:
        fail(f"Could not check existing playlists: {e}")

    # Not found - create it.
    body = {
        "snippet": {
            "title": playlist_name,
            "description": "",
        },
        "status": {
            "privacyStatus": DEFAULT_PLAYLIST_PRIVACY,
        },
    }
    try:
        response = youtube.playlists().insert(part="snippet,status", body=body).execute()
    except HttpError as e:
        fail(f"Could not create '{playlist_name}' playlist: {e}")

    return response["id"]


def add_video_to_playlist(youtube, playlist_id, video_id, playlist_name):
    from googleapiclient.errors import HttpError

    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        }
    }
    try:
        youtube.playlistItems().insert(part="snippet", body=body).execute()
    except HttpError as e:
        fail(f"Video uploaded successfully, but could not add it to the '{playlist_name}' playlist: {e}")


def upload_video(youtube, file_path, title, privacy):
    import random
    import time
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    if not os.path.exists(file_path):
        fail(f"Video file not found: {file_path}")

    body = {
        "snippet": {
            "title": title,
            "description": "",
            "categoryId": "22",  # People & Blogs (default category)
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": DEFAULT_MADE_FOR_KIDS,
        },
    }

    # chunksize=4MB instead of -1 (full file at once).
    # With -1, a dropped connection wastes the entire upload and cannot resume.
    # With 4MB chunks, a retry picks up from the last successful chunk.
    media = MediaFileUpload(file_path, chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")

    try:
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    except HttpError as e:
        fail(f"YouTube API rejected the upload request: {e}")

    # Errno 10054 (connection forcibly closed) and similar transient network
    # errors are retriable. HttpError 5xx from YouTube's side are also retriable.
    RETRIABLE_NETWORK_ERRORS = (IOError, OSError, ConnectionResetError, ConnectionAbortedError)
    RETRIABLE_HTTP_CODES = {500, 502, 503, 504}
    MAX_RETRIES = 10

    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"Uploading... {int(status.progress() * 100)}%")
            retry = 0  # reset backoff counter on any successful chunk
        except HttpError as e:
            if e.resp.status in RETRIABLE_HTTP_CODES:
                error_msg = f"YouTube returned HTTP {e.resp.status}"
            else:
                fail(f"Upload failed partway through: {e}")
        except RETRIABLE_NETWORK_ERRORS as e:
            error_msg = f"Network error: {e}"
        except Exception as e:
            fail(f"Unexpected non-retriable error during upload: {e}")
        else:
            continue  # chunk succeeded, no error handling needed

        # Retriable error path
        retry += 1
        if retry > MAX_RETRIES:
            fail(f"Unexpected error during upload: {error_msg} (gave up after {MAX_RETRIES} retries)")
        wait = min(2 ** retry + random.random(), 60)  # exponential backoff, capped at 60s
        print(f"[WARN] {error_msg} — retrying in {wait:.1f}s... (attempt {retry}/{MAX_RETRIES})")
        time.sleep(wait)

    if not response or "id" not in response:
        fail("Upload finished but YouTube did not return a video ID. Upload may not have completed.")

    video_id = response["id"]
    print(f"[YOUTUBE_UPLOAD_OK] https://youtu.be/{video_id}")
    return video_id


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4, 5):
        fail('Usage: python youtube_upload.py "<path_to_video>" "<title>" ["<playlist_name>"] ["<privacy>"]')

    video_path = sys.argv[1]
    video_title = sys.argv[2]
    playlist_name = sys.argv[3] if len(sys.argv) >= 4 and sys.argv[3].strip() else DEFAULT_PLAYLIST_NAME
    privacy = sys.argv[4] if len(sys.argv) == 5 and sys.argv[4].strip() else DEFAULT_PRIVACY

    if privacy not in PRIVACY_OPTIONS:
        fail(f'Invalid privacy value "{privacy}". Must be one of: {", ".join(sorted(PRIVACY_OPTIONS))}')

    yt = get_authenticated_service()
    uploaded_video_id = upload_video(yt, video_path, video_title, privacy)

    print(f"Adding video to '{playlist_name}' playlist...")
    pl_id = get_or_create_playlist(yt, playlist_name)
    add_video_to_playlist(yt, pl_id, uploaded_video_id, playlist_name)
    print(f"[YOUTUBE_PLAYLIST_OK] Added to '{playlist_name}' playlist.")
