import os
import re
import json
# Load .env file from project root or environment (optional in Qinglong / Docker)
try:
    from dotenv import load_dotenv
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dotenv_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
    else:
        load_dotenv()
except ImportError:
    pass


class Config:
    # --- 1. Douban Auth ---
    # Priority: DOUBAN_AUTH (single variable) or DOUBAN_COOKIE / DOUBAN_USER_ID / DOUBAN_USERNAME / DOUBAN_PASSWORD
    DOUBAN_AUTH = os.getenv("DOUBAN_AUTH", "").strip()
    DOUBAN_COOKIE = os.getenv("DOUBAN_COOKIE", "").strip()
    DOUBAN_USER_ID = os.getenv("DOUBAN_USER_ID", "").strip()
    DOUBAN_USERNAME = os.getenv("DOUBAN_USERNAME", "").strip()
    DOUBAN_PASSWORD = os.getenv("DOUBAN_PASSWORD", "").strip()

    if DOUBAN_AUTH:
        # Check if it's cookie string (contains dbcl2, ck, bid or semicolons)
        if "dbcl2" in DOUBAN_AUTH or "ck=" in DOUBAN_AUTH or "bid=" in DOUBAN_AUTH or ";" in DOUBAN_AUTH:
            DOUBAN_COOKIE = DOUBAN_AUTH
            m = re.search(r'dbcl2=[\"\']?(\d+):', DOUBAN_COOKIE)
            if m:
                DOUBAN_USER_ID = m.group(1)
        # Check if it's username:password format
        elif ":" in DOUBAN_AUTH and not DOUBAN_AUTH.startswith("http"):
            parts = DOUBAN_AUTH.split(":", 1)
            DOUBAN_USERNAME = parts[0].strip()
            DOUBAN_PASSWORD = parts[1].strip()

    # Automatically extract Douban User ID from cookie dbcl2 if not explicitly set
    if DOUBAN_COOKIE and not DOUBAN_USER_ID:
        m = re.search(r'dbcl2=[\"\']?(\d+):', DOUBAN_COOKIE)
        if m:
            DOUBAN_USER_ID = m.group(1)

    # --- 2. Trakt Auth ---
    # Priority: TRAKT_AUTH (single variable) or separate TRAKT_CLIENT_ID / TRAKT_ACCESS_TOKEN
    TRAKT_AUTH = os.getenv("TRAKT_AUTH", "").strip()
    TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID", "").strip()
    TRAKT_ACCESS_TOKEN = os.getenv("TRAKT_ACCESS_TOKEN", "").strip()
    TRAKT_CLIENT_SECRET = os.getenv("TRAKT_CLIENT_SECRET", "").strip()
    TRAKT_REFRESH_TOKEN = os.getenv("TRAKT_REFRESH_TOKEN", "").strip()

    if TRAKT_AUTH:
        # Format A: client_id#access_token (optional #refresh_token#client_secret)
        if "#" in TRAKT_AUTH:
            parts = [p.strip() for p in TRAKT_AUTH.split("#")]
            if len(parts) >= 2:
                TRAKT_CLIENT_ID = parts[0]
                TRAKT_ACCESS_TOKEN = parts[1]
            if len(parts) >= 3:
                TRAKT_REFRESH_TOKEN = parts[2]
            if len(parts) >= 4:
                TRAKT_CLIENT_SECRET = parts[3]
        # Format B: Console F12 output: TRAKT_CLIENT_ID=... TRAKT_ACCESS_TOKEN=...
        elif "TRAKT_CLIENT_ID" in TRAKT_AUTH or "TRAKT_ACCESS_TOKEN" in TRAKT_AUTH:
            m_id = re.search(r'TRAKT_CLIENT_ID=([^\s;]+)', TRAKT_AUTH)
            m_tok = re.search(r'TRAKT_ACCESS_TOKEN=([^\s;]+)', TRAKT_AUTH)
            if m_id:
                TRAKT_CLIENT_ID = m_id.group(1).strip()
            if m_tok:
                TRAKT_ACCESS_TOKEN = m_tok.group(1).strip()
        # Format C: JSON object
        elif TRAKT_AUTH.startswith("{") and TRAKT_AUTH.endswith("}"):
            try:
                d = json.loads(TRAKT_AUTH)
                TRAKT_CLIENT_ID = d.get("client_id", TRAKT_CLIENT_ID)
                TRAKT_ACCESS_TOKEN = d.get("access_token", TRAKT_ACCESS_TOKEN)
                TRAKT_REFRESH_TOKEN = d.get("refresh_token", TRAKT_REFRESH_TOKEN)
                TRAKT_CLIENT_SECRET = d.get("client_secret", TRAKT_CLIENT_SECRET)
            except Exception:
                pass
        # Format D: client_id:client_secret (for OAuth flow)
        elif ":" in TRAKT_AUTH and len(TRAKT_AUTH.split(":")) == 2:
            parts = TRAKT_AUTH.split(":")
            TRAKT_CLIENT_ID = parts[0].strip()
            TRAKT_CLIENT_SECRET = parts[1].strip()

    # --- 3. Optional Settings ---
    SYNC_DELAY_MIN = float(os.getenv("SYNC_DELAY_MIN", "3.0"))
    SYNC_DELAY_MAX = float(os.getenv("SYNC_DELAY_MAX", "6.0"))

    @classmethod
    def load_cached_credentials(cls, db):
        """Load dynamically refreshed credentials from SQLite cache if available."""
        try:
            # Trakt
            t_cred = db.get_platform_credential("trakt")
            if t_cred and isinstance(t_cred, dict):
                # Only use cached token if env var didn't explicitly override it with a new one
                if not cls.TRAKT_ACCESS_TOKEN and t_cred.get("access_token"):
                    cls.TRAKT_ACCESS_TOKEN = t_cred["access_token"]
                if t_cred.get("refresh_token"):
                    cls.TRAKT_REFRESH_TOKEN = t_cred["refresh_token"]
                if t_cred.get("client_id") and not cls.TRAKT_CLIENT_ID:
                    cls.TRAKT_CLIENT_ID = t_cred["client_id"]
                if t_cred.get("client_secret") and not cls.TRAKT_CLIENT_SECRET:
                    cls.TRAKT_CLIENT_SECRET = t_cred["client_secret"]

            # Douban
            d_cred = db.get_platform_credential("douban")
            if d_cred and isinstance(d_cred, dict):
                if not cls.DOUBAN_COOKIE and d_cred.get("cookie"):
                    cls.DOUBAN_COOKIE = d_cred["cookie"]
                    m = re.search(r'dbcl2=[\"\']?(\d+):', cls.DOUBAN_COOKIE)
                    if m and not cls.DOUBAN_USER_ID:
                        cls.DOUBAN_USER_ID = m.group(1)
        except Exception:
            pass

    @classmethod
    def validate_trakt(cls) -> bool:
        return bool(cls.TRAKT_CLIENT_ID and (cls.TRAKT_ACCESS_TOKEN or cls.TRAKT_REFRESH_TOKEN or cls.TRAKT_CLIENT_SECRET))

    @classmethod
    def validate_douban(cls) -> bool:
        return bool(
            (cls.DOUBAN_USER_ID and cls.DOUBAN_COOKIE)
            or (cls.DOUBAN_USERNAME and cls.DOUBAN_PASSWORD)
            or cls.DOUBAN_COOKIE
        )
