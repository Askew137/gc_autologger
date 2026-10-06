"""
Direct HTTP client for Geocaching.com.
Reverse-engineered from c:geo (GCLogAPI, GCAuthAPI, GCParser, GCLogin).
Communicates directly via JSON, REST, and tRPC batch APIs with zero browser overhead.
Supports session persistence, auto-relogin, and Playwright fallback for initial CAPTCHA.
"""

import os
import re
import json
import time
import html
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

from core.converter import gc_code_to_cache_id


class GeocachingClient:
    """High-performance direct HTTP client for Geocaching.com."""

    BASE_URL = "https://www.geocaching.com"
    API_PROXY_URL = "https://www.geocaching.com/api/proxy"
    SIGNIN_URL = "https://www.geocaching.com/account/signin"
    OAUTH_TOKEN_URL = "https://www.geocaching.com/account/oauth/token"
    CSRF_TOKEN_URL = "https://www.geocaching.com/api/auth/csrf"
    USER_COORDS_URL = "https://www.geocaching.com/seek/geocache.usercoordinate"
    TRPC_CREATE_LOG_URL = "https://www.geocaching.com/api/live/v1/trpc/web.logs.createGeocacheLog"
    TRPC_UPDATE_LOG_URL = "https://www.geocaching.com/api/live/v1/trpc/web.logs.updateGeocacheLog"
    TRPC_DELETE_LOG_URL = "https://www.geocaching.com/api/live/v1/trpc/web.logs.deleteGeocacheLog"

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(self, cookies_file_path: Optional[str] = None):
        if not requests:
            raise RuntimeError("The 'requests' package is required. Run 'pip install requests'.")

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,cs;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        })

        if not cookies_file_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cookies_file_path = os.path.join(base_dir, "session_cookies.json")
        self.cookies_file_path = cookies_file_path

        self.verification_token: Optional[str] = None
        self.oauth_bearer_token: Optional[str] = None
        self.oauth_token_expiry: float = 0
        self.csrf_token: Optional[str] = None
        self.logged_in_username: Optional[str] = None
        self.current_credentials: Optional[Tuple[str, str]] = None
        self._cache_details_cache: Dict[str, Dict[str, Any]] = {}

        self.load_cookies()

    # --------------------------------------------------------------------------
    # Session & Cookie Persistence
    # --------------------------------------------------------------------------

    def save_cookies(self, username: Optional[str] = None) -> None:
        """Save active session cookies to disk per account."""
        user = username or self.logged_in_username
        if not user:
            return
        try:
            data = {}
            if os.path.exists(self.cookies_file_path):
                try:
                    with open(self.cookies_file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            if not isinstance(data, dict):
                data = {}

            cookies_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
            accounts = data.get("accounts", {})
            if not isinstance(accounts, dict):
                accounts = {}

            accounts[user] = {
                "cookies": cookies_dict,
                "saved_at": time.time()
            }

            data["accounts"] = accounts
            data["active_username"] = user
            data["cookies"] = cookies_dict
            data["username"] = user
            data["saved_at"] = time.time()

            with open(self.cookies_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save cookies: {e}")

    def load_cookies(self, username: Optional[str] = None) -> bool:
        """Load session cookies from disk for specified account or active account."""
        if not os.path.exists(self.cookies_file_path):
            return False
        try:
            with open(self.cookies_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False

            accounts = data.get("accounts", {})
            target_user = username
            cookies_dict = None

            if target_user:
                # Look for matching account (case-insensitive)
                for acc_name, acc_data in accounts.items():
                    if acc_name.lower() == target_user.lower():
                        cookies_dict = acc_data.get("cookies", {})
                        target_user = acc_name
                        break
                # Fallback to top-level if matching
                if not cookies_dict and data.get("username", "").lower() == target_user.lower():
                    cookies_dict = data.get("cookies", {})
            else:
                # No username specified: use active_username or top-level username
                active_user = data.get("active_username") or data.get("username")
                if active_user and isinstance(accounts, dict) and active_user in accounts:
                    cookies_dict = accounts[active_user].get("cookies", {})
                    target_user = active_user
                elif "cookies" in data:
                    cookies_dict = data.get("cookies", {})
                    target_user = data.get("username")

            if cookies_dict:
                self.session.cookies.clear()
                self.session.cookies.update(requests.utils.cookiejar_from_dict(cookies_dict))
                self.logged_in_username = target_user
                return True
            return False
        except Exception:
            return False

    def clear_cookies(self, username: Optional[str] = None) -> None:
        """Clear cookies for a specific user or completely."""
        self.clear_session()
        if not os.path.exists(self.cookies_file_path):
            return
        try:
            with open(self.cookies_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return

            if username:
                accounts = data.get("accounts", {})
                for k in list(accounts.keys()):
                    if k.lower() == username.lower():
                        del accounts[k]
                data["accounts"] = accounts
                if data.get("active_username", "").lower() == username.lower():
                    data["active_username"] = None
                if data.get("username", "").lower() == username.lower():
                    data["cookies"] = {}
                    data["username"] = None
                with open(self.cookies_file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            else:
                os.remove(self.cookies_file_path)
        except Exception:
            pass

    def clear_session(self) -> None:
        """Clear active in-memory session tokens and cookies."""
        self.session.cookies.clear()
        self.verification_token = None
        self.oauth_bearer_token = None
        self.csrf_token = None
        self.logged_in_username = None
        self.current_credentials = None
        self._cache_details_cache.clear()

    def switch_account(self, username: str, password: Optional[str] = None) -> Tuple[bool, str]:
        """
        Switch active session to target username.
        Tries saved cookies for that account first. If not valid and password is provided,
        attempts fresh login.
        """
        self.clear_session()
        # 1. Try loading cached cookies for this account
        if self.load_cookies(username):
            if self.is_logged_in():
                return True, f"Session restored from saved cookies for '{username}'."
            else:
                self.clear_session()

        # 2. If not logged in and password provided, perform login
        if password:
            return self.login(username, password)

        return False, f"No active session for '{username}' and no password configured."

    # --------------------------------------------------------------------------
    # Authentication & Connection Testing
    # --------------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        """Test whether current session is authenticated by fetching user profile."""
        try:
            resp = self.session.get(f"{self.BASE_URL}/account/settings/homelocation", timeout=10, allow_redirects=False)
            if resp.status_code == 200 and "signin" not in resp.url.lower():
                # Extract username if not yet known
                if not self.logged_in_username:
                    match = re.search(r'"username":\s*"([^"]+)"', resp.text)
                    if match:
                        self.logged_in_username = match.group(1)
                return True
            return False
        except Exception:
            return False

    def login(self, username: str, password: str, force_browser_fallback: bool = False) -> Tuple[bool, str]:
        """
        Authenticate with Geocaching.com.
        Tries fast direct HTTP login first. If a CAPTCHA is encountered,
        falls back to Playwright to capture cookies.
        """
        if not username or not password:
            return False, "Username and password are required to login."

        # Clear in-memory cookies to ensure /account/signin serves the unauthenticated form
        self.clear_session()
        self.current_credentials = (username, password)

        if not force_browser_fallback:
            success, msg = self._login_http(username, password)
            if success:
                self.logged_in_username = username
                self.save_cookies(username)
                return True, "Login successful via direct HTTP."
            if "captcha" not in msg.lower() and "unauthorized" not in msg.lower() and "token" not in msg.lower():
                # If error wasn't a bot challenge, report it directly
                return False, msg

        # Fallback to Playwright if HTTP login encountered bot challenge
        success, msg = self._login_playwright(username, password)
        if success:
            self.logged_in_username = username
            self.save_cookies(username)
            return True, "Login successful via browser session capture."
        return False, msg

    def _login_http(self, username: str, password: str) -> Tuple[bool, str]:
        """Perform direct HTTP POST login replicating c:geo GCLogin.java."""
        try:
            # 1. Fetch sign-in page to extract __RequestVerificationToken
            resp = self.session.get(self.SIGNIN_URL, timeout=12)
            if resp.status_code != 200:
                return False, f"Cannot load sign-in page (HTTP {resp.status_code})"

            token = self._extract_request_verification_token(resp.text)
            if not token:
                return False, "Could not find __RequestVerificationToken on signin page."

            # 2. Check for captcha
            if "g-recaptcha" in resp.text or "cf-turnstile" in resp.text:
                return False, "CAPTCHA detected on sign-in page."

            # 3. Post credentials
            payload = {
                "UsernameOrEmail": username,
                "Password": password,
                "__RequestVerificationToken": token
            }
            headers = {
                "Referer": self.SIGNIN_URL,
                "Origin": self.BASE_URL
            }

            post_resp = self.session.post(self.SIGNIN_URL, data=payload, headers=headers, timeout=15, allow_redirects=True)

            if "signup-validation-error" in post_resp.text:
                return False, "Invalid username or password."
            if "g-recaptcha" in post_resp.text or "cf-turnstile" in post_resp.text:
                return False, "CAPTCHA verification required."

            # Verify session
            if self.is_logged_in():
                return True, "Success"

            return False, "Login failed: Session cookie was not established."
        except Exception as e:
            return False, f"Network error during HTTP login: {str(e)}"

    def _login_playwright(self, username: str, password: str) -> Tuple[bool, str]:
        """Playwright fallback to solve login / Cloudflare challenges."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False, "Playwright is not installed for fallback login."

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)  # Visible window if CAPTCHA needed
                context = browser.new_context(user_agent=self.DEFAULT_USER_AGENT)
                page = context.new_page()

                page.goto(self.SIGNIN_URL, timeout=25000)
                page.wait_for_load_state("domcontentloaded")

                # Handle GDPR
                try:
                    gdpr = page.query_selector('#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll')
                    if gdpr:
                        gdpr.click()
                except Exception:
                    pass

                page.fill("#UsernameOrEmail", username)
                page.fill("#Password", password)
                page.click("#SignIn")

                # Wait for navigation away from signin
                try:
                    page.wait_for_url(lambda u: "signin" not in u.lower(), timeout=30000)
                except Exception:
                    # User might need a moment to solve captcha manually
                    time.sleep(5)

                # Check for explicit error messages if still on signin page
                if "signin" in page.url.lower():
                    err = page.query_selector('.alert-error') or page.query_selector('#signup-validation-error')
                    if err:
                        err_text = err.inner_text().strip()
                        if err_text:
                            browser.close()
                            return False, err_text

                # Extract all cookies into requests session
                cookies = context.cookies()
                for c in cookies:
                    self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".geocaching.com"))

                browser.close()

            if self.is_logged_in():
                return True, "Success"
            return False, "Playwright completed but session is not valid."
        except Exception as e:
            return False, f"Playwright error: {str(e)}"

    def ensure_valid_session(self) -> bool:
        """Check session and re-login if credentials are saved."""
        if self.is_logged_in():
            return True
        if self.current_credentials:
            u, p = self.current_credentials
            success, _ = self.login(u, p)
            return success
        return False

    # --------------------------------------------------------------------------
    # Token Acquisition (Verification, OAuth Bearer, CSRF)
    # --------------------------------------------------------------------------

    def get_verification_token(self, force_refresh: bool = False, sample_gccode: Optional[str] = None) -> Optional[str]:
        """
        Get ASP.NET __RequestVerificationToken.
        Reused across requests as it remains valid for the whole login session.
        Fetches the token directly from a geocache listing page (replicates c:geo GCParser.java).
        """
        if self.verification_token and not force_refresh:
            return self.verification_token

        target_gccode = sample_gccode or "GC24Q85"

        # 1. Primary: fetch from cache page (c:geo GCParser.java:getRequestVerificationToken)
        try:
            resp = self.session.get(f"{self.BASE_URL}/geocache/{target_gccode}", timeout=10)
            if resp.status_code == 200:
                token = self._extract_request_verification_token(resp.text)
                if token:
                    self.verification_token = token
                    return token
        except Exception:
            pass

        # 2. Secondary: fetch from classic cache details page
        try:
            resp = self.session.get(f"{self.BASE_URL}/seek/cache_details.aspx?wp={target_gccode}", timeout=10)
            if resp.status_code == 200:
                token = self._extract_request_verification_token(resp.text)
                if token:
                    self.verification_token = token
                    return token
        except Exception:
            pass

        # 3. Fallback: try /play
        try:
            resp = self.session.get(f"{self.BASE_URL}/play", timeout=10)
            token = self._extract_request_verification_token(resp.text)
            if token:
                self.verification_token = token
                return token
        except Exception:
            pass

        return None

    def get_oauth_bearer_token(self, force_refresh: bool = False) -> Optional[str]:
        """
        Fetch OAuth Bearer token from /account/oauth/token as done by c:geo GCAuthAPI.java.
        """
        if self.oauth_bearer_token and time.time() < self.oauth_token_expiry and not force_refresh:
            return self.oauth_bearer_token

        try:
            resp = self.session.get(self.OAUTH_TOKEN_URL, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3600))
                if token:
                    self.oauth_bearer_token = token
                    self.oauth_token_expiry = time.time() + (expires_in * 0.8)
                    return token
        except Exception:
            pass
        return None

    def get_csrf_token(self, force_refresh: bool = False) -> Optional[str]:
        """
        Fetch CSRF token from /api/auth/csrf as done by c:geo GCLogAPI.java.
        """
        if self.csrf_token and not force_refresh:
            return self.csrf_token

        bearer = self.get_oauth_bearer_token()
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        try:
            resp = self.session.get(self.CSRF_TOKEN_URL, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("csrfToken")
                if token:
                    self.csrf_token = token
                    return token
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_request_verification_token(html: str) -> Optional[str]:
        pattern = re.compile(r'name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']', re.IGNORECASE)
        match = pattern.search(html)
        if match:
            return match.group(1)
        pattern2 = re.compile(r'value=["\']([^"\']+)["\'][^>]*name=["\']__RequestVerificationToken["\']', re.IGNORECASE)
        match2 = pattern2.search(html)
        if match2:
            return match2.group(1)
        return None

    # --------------------------------------------------------------------------
    # Coordinates API (Direct HTTP upload & check)
    # --------------------------------------------------------------------------

    def upload_coordinates(self, gc_code: str, lat: float, lon: float) -> Tuple[bool, str]:
        """
        Upload corrected coordinates for a cache to Geocaching.com.
        Replicates c:geo GCParser.java:editModifiedCoordinates.
        """
        cache_id = gc_code_to_cache_id(gc_code)
        if not cache_id:
            return False, f"Invalid GC code: {gc_code}"

        token = self.get_verification_token(sample_gccode=gc_code)
        if not token:
            return False, "Failed to acquire __RequestVerificationToken"

        headers = {
            "__RequestVerificationToken": token,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/geocache/{gc_code}"
        }

        payload = {
            "cacheId": cache_id,
            "lat": float(lat),
            "lng": float(lon)
        }

        try:
            resp = self.session.post(self.USER_COORDS_URL, headers=headers, json=payload, timeout=15)

            if resp.status_code in [200, 204]:
                return True, f"Coordinates updated ({lat:.5f}, {lon:.5f})"

            if resp.status_code in [401, 403]:
                # Try re-authenticating and retry once
                if self.ensure_valid_session():
                    refreshed_token = self.get_verification_token(force_refresh=True, sample_gccode=gc_code)
                    if refreshed_token:
                        headers["__RequestVerificationToken"] = refreshed_token
                        resp2 = self.session.post(self.USER_COORDS_URL, headers=headers, json=payload, timeout=15)
                        if resp2.status_code in [200, 204]:
                            return True, f"Coordinates updated ({lat:.5f}, {lon:.5f})"

            return False, f"Server returned HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def has_modified_coordinates(self, gc_code: str) -> Optional[bool]:
        """
        Check if a cache already has user-modified coordinates on Geocaching.com.
        Returns True if already modified, False if unmodified, or None if unknown.
        """
        try:
            resp = self.session.get(f"{self.BASE_URL}/geocache/{gc_code}", timeout=10)
            if resp.status_code == 200:
                html = resp.text
                # Cache verification token if present
                token = self._extract_request_verification_token(html)
                if token:
                    self.verification_token = token

                # Groundspeak embeds window.userDefinedCoords with isUserDefined flag:
                # window.userDefinedCoords = {"status":"success","data":{"isUserDefined":true,...}}
                match = re.search(r'"isUserDefined"\s*:\s*(true|false)', html, re.IGNORECASE)
                if match:
                    return match.group(1).lower() == "true"

                return False
        except Exception:
            pass
        return None

    # --------------------------------------------------------------------------
    # Cache Logging API (tRPC batch endpoints as in c:geo GCLogAPI.java)
    # --------------------------------------------------------------------------

    def create_log(
        self,
        gc_code: str,
        log_type_id: int,
        date_iso: str,
        text: str
    ) -> Tuple[bool, str]:
        """
        Post a new log entry via tRPC batch endpoint.
        Replicates c:geo GCLogAPI.java:createLog.
        """
        bearer = self.get_oauth_bearer_token()
        csrf = self.get_csrf_token()

        if not csrf:
            return False, "Failed to retrieve CSRF token."

        headers = {
            "CSRF-Token": csrf,
            "Content-Type": "application/json",
            "Referer": f"{self.BASE_URL}/live/geocache/{gc_code}/log"
        }
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        # Standard tRPC batch body format
        body = {
            "0": {
                "referenceCode": gc_code,
                "body": {
                    "images": [],
                    "logDate": date_iso,
                    "logText": text,
                    "logType": log_type_id,
                    "trackables": [],
                    "geocacheReferenceCode": "",
                    "usedFavoritePoint": False
                }
            }
        }

        url = f"{self.TRPC_CREATE_LOG_URL}?batch=1"

        try:
            resp = self.session.post(url, headers=headers, json=body, timeout=15)
            if resp.status_code in [200, 201]:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    item = data[0]
                    if "error" in item:
                        err_msg = item["error"].get("json", {}).get("message", "Unknown API error")
                        return False, f"API error: {err_msg}"
                    result = item.get("result", {}).get("data", {})
                    log_code = result.get("logReferenceCode", "")
                    return True, f"Logged successfully ({log_code})"
                return True, "Logged successfully"

            if resp.status_code in [401, 403]:
                if self.ensure_valid_session():
                    # Retry once with refreshed tokens
                    headers["CSRF-Token"] = self.get_csrf_token(force_refresh=True) or ""
                    headers["Authorization"] = f"Bearer {self.get_oauth_bearer_token(force_refresh=True)}"
                    resp2 = self.session.post(url, headers=headers, json=body, timeout=15)
                    if resp2.status_code in [200, 201]:
                        data2 = resp2.json()
                        if isinstance(data2, list) and len(data2) > 0 and "error" not in data2[0]:
                            return True, "Logged successfully after re-auth"

            return False, f"Server returned HTTP {resp.status_code}: {resp.text[:120]}"
        except Exception as e:
            return False, f"Error posting log: {str(e)}"

    def get_cache_details(self, gc_code: str) -> Optional[Dict[str, Any]]:
        """
        Fetch basic cache metadata (found status, cache type, etc.) with in-memory caching.
        """
        code = gc_code.strip().upper()
        if code in self._cache_details_cache:
            return self._cache_details_cache[code]

        try:
            bearer = self.get_oauth_bearer_token()
            headers = {"Accept": "application/json"}
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"
            api_url = f"{self.API_PROXY_URL}/web/v1/geocache/{code}"
            resp = self.session.get(api_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    self._cache_details_cache[code] = data
                    return data
        except Exception:
            pass
        return None

    def is_cache_found(self, gc_code: str) -> Tuple[bool, Optional[str]]:
        """
        Check if the authenticated user has already logged this cache as 'Found it'.
        Returns (True, "YYYY-MM-DD") if found, or (False, None) if not found.
        """
        data = self.get_cache_details(gc_code)
        if data:
            found_dt = data.get("callerSpecific", {}).get("found")
            if found_dt:
                return True, str(found_dt).split("T")[0]
        return False, None

    def is_virtual_or_earth_cache(self, gc_code: str) -> bool:
        """
        Check if cache is a Virtual Cache (type 4) or EarthCache (type 137).
        """
        data = self.get_cache_details(gc_code)
        if not data:
            return False
        type_id = data.get("geocacheType")
        type_name = str(data.get("typeName", "")).lower()
        if type_id in [4, 137]:
            return True
        if any(k in type_name for k in ["virtual", "earthcache", "earth cache"]):
            return True
        return False

    def get_ignored_cache_codes(self) -> set:
        """
        Fetch all GC codes currently on the user's Ignore List in a single fast request.
        Extracts geocache codes from /plan/lists/ignored.
        """
        try:
            resp = self.session.get(f"{self.BASE_URL}/plan/lists/ignored", timeout=12)
            if resp.status_code == 200:
                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text)
                if match:
                    data = json.loads(match.group(1))
                    page_props = data.get("props", {}).get("pageProps", {})
                    geocaches = page_props.get("geocaches", {}).get("data", [])
                    return {g.get("referenceCode").upper() for g in geocaches if g.get("referenceCode")}
        except Exception:
            pass
        return set()

    def ignore_cache(self, gc_code: str) -> Tuple[bool, str]:
        """
        Add cache to ignore list.
        Replicates c:geo GCParser.java:ignoreCache.
        """
        token = self.get_verification_token()
        try:
            listing_resp = self.session.get(f"{self.BASE_URL}/geocache/{gc_code}", timeout=12)
            if listing_resp.status_code != 200:
                return False, f"Could not open cache page ({listing_resp.status_code})"

            # Find ignore link or GUID from listing HTML
            guid = None
            wpt_type_id = "2"

            # 1. Match ignore link (handles &amp; and &)
            guid_match = re.search(r'bookmarks/ignore\.aspx\?guid=([a-f0-9\-]+)(?:&(?:amp;)?WptTypeID=(\d+))?', listing_resp.text, re.IGNORECASE)
            if guid_match:
                guid = guid_match.group(1)
                if guid_match.group(2):
                    wpt_type_id = guid_match.group(2)
            else:
                # 2. Match data-geocache-guid
                root_guid = re.search(r'data-geocache-guid="([a-f0-9\-]+)"', listing_resp.text)
                if root_guid:
                    guid = root_guid.group(1)
                    wpt_match = re.search(r'data-wpt-type-id="(\d+)"', listing_resp.text)
                    if wpt_match:
                        wpt_type_id = wpt_match.group(1)
                else:
                    # 3. Match bookmark mark link
                    mark_match = re.search(r'bookmarks/mark\.aspx\?guid=([a-f0-9\-]+)(?:&(?:amp;)?WptTypeID=(\d+))?', listing_resp.text, re.IGNORECASE)
                    if mark_match:
                        guid = mark_match.group(1)
                        if mark_match.group(2):
                            wpt_type_id = mark_match.group(2)

            if not guid:
                if "premium-feature" in listing_resp.text and "Ignore" in listing_resp.text:
                    return False, "Ignore list is a Premium Member feature."
                return False, "Ignore link not found on cache page."

            ignore_url = f"{self.BASE_URL}/bookmarks/ignore.aspx?guid={guid}&WptTypeID={wpt_type_id}"
            page_resp = self.session.get(ignore_url, timeout=12)
            if page_resp.status_code != 200:
                return False, f"Could not open ignore confirmation page ({page_resp.status_code})"

            # Extract viewstates and form inputs
            vs_match = re.search(r'id="__VIEWSTATE"\s+value="([^"]+)"', page_resp.text)
            vsg_match = re.search(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]+)"', page_resp.text)
            ev_match = re.search(r'id="__EVENTVALIDATION"\s+value="([^"]+)"', page_resp.text)
            btn_yes_match = re.search(r'name="ctl00\$ContentBody\$btnYes"\s+value="([^"]+)"', page_resp.text)

            btn_value = html.unescape(btn_yes_match.group(1)) if btn_yes_match else "Yes. Ignore it."

            # Check if cache is already ignored:
            # When already ignored, btnYes is a toggle to restore/un-ignore ("Ano, chci to obnovit" / "Yes, un-ignore it")
            # and the page heading states "je zařazen do seznam..." rather than "přidat ... do seznamu".
            is_unignore_button = any(w in btn_value.lower() for w in ["obnovit", "un-ignore", "unignore", "restore"])
            is_already_listed = (
                "je zařazen do" in page_resp.text.lower()
                or "currently on your ignore list" in page_resp.text.lower()
            )

            if is_unignore_button or is_already_listed:
                return True, "Already on Ignore list"

            v_token = self._extract_request_verification_token(page_resp.text) or token or ""

            post_data = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": vs_match.group(1) if vs_match else "",
                "__VIEWSTATEGENERATOR": vsg_match.group(1) if vsg_match else "",
                "__EVENTVALIDATION": ev_match.group(1) if ev_match else "",
                "__RequestVerificationToken": v_token,
                "ctl00$ContentBody$btnYes": btn_value,
                "returnUrl": ""
            }

            headers = {
                "Referer": ignore_url,
                "Origin": self.BASE_URL
            }

            confirm_resp = self.session.post(ignore_url, data=post_data, headers=headers, timeout=12)
            if confirm_resp.status_code == 200:
                return True, "Added to Ignore list"
            return False, f"Ignore failed (HTTP {confirm_resp.status_code})"
        except Exception as e:
            return False, f"Error ignoring cache: {str(e)}"
