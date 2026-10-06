"""
Orchestrates copying logs from another Geocaching user.
Fetches the target user's logged caches for a given date,
and submits them as Found logs for the active account.
"""

import re
import time
from datetime import datetime
from typing import List, Callable, Optional, Dict, Any
from core.client import GeocachingClient
from core.safety import SafetyManager


class UserLogCopier:
    """Manages fetching and copying logs from a target user account."""

    def __init__(self, client: GeocachingClient, safety: SafetyManager):
        self.client = client
        self.safety = safety

    def fetch_user_logged_caches(
        self,
        target_username: str,
        target_password: Optional[str],
        target_date_str: str,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> List[str]:
        """
        Fetch all GC codes logged by target user on the specified date (YYYY-MM-DD).
        If target_password is provided, uses browser session to scroll and load logs.
        """
        def log(msg: str, level: str = "info"):
            if on_log:
                on_log(msg, level)
            else:
                print(f"[{level.upper()}] {msg}")

        log(f"Scanning logs for user '{target_username}' on {target_date_str}...", "info")

        # Flexible date normalization (supports YYYY-MM-DD, YYYY-M-D, D.M.YYYY, etc.)
        normalized_date = target_date_str.strip()
        possible_date_formats = [normalized_date]
        try:
            clean = normalized_date.replace("/", "-").replace(".", "-").strip()
            parts = [int(p) for p in clean.split("-") if p.isdigit()]
            if len(parts) == 3:
                if parts[0] > 1000:
                    y, m, d = parts[0], parts[1], parts[2]
                else:
                    d, m, y = parts[0], parts[1], parts[2]
                dt = datetime(y, m, d)
                normalized_date = dt.strftime("%Y-%m-%d")
                possible_date_formats = [
                    f"{d}.{m}.{y}",
                    f"{d}. {m}. {y}",
                    f"{d:02d}.{m:02d}.{y}",
                    f"{d:02d}. {m:02d}. {y}",
                    f"{m}/{d}/{y}",
                    f"{m:02d}/{d:02d}/{y}",
                    normalized_date
                ]
        except Exception:
            pass

        log(f"Scanning logs for user '{target_username}' on {normalized_date}...", "info")

        # If password is provided, try direct HTTP first, then fallback to Playwright
        if target_password:
            http_result = self._fetch_via_authenticated_http(target_username, target_password, normalized_date, possible_date_formats, log)
            if http_result is not None:
                return http_result
            return self._fetch_via_browser(target_username, target_password, possible_date_formats, log)
        else:
            return self._fetch_via_public_api(target_username, normalized_date, log)

    def _fetch_via_authenticated_http(
        self,
        username: str,
        password: str,
        target_date_iso: str,
        possible_date_formats: List[str],
        log: Callable[[str, str], None]
    ) -> Optional[List[str]]:
        """Fast direct HTTP log reader using target user's session."""
        try:
            target_client = GeocachingClient()
            success, _ = target_client.switch_account(username, password)
            if not success:
                return None

            log("Fetching user log table via direct HTTP...", "info")
            resp = target_client.session.get(f"{target_client.BASE_URL}/my/logs.aspx?s=1&lt=2", timeout=12)
            if resp.status_code != 200 or "signin" in resp.url.lower():
                return None

            gc_codes = set()
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
            for row in rows:
                tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(tds) >= 4:
                    date_text = re.sub(r'<[^>]+>', '', tds[2]).strip()
                    gc_match = re.search(r'/geocache/(GC[A-Z0-9]+)', tds[3], re.IGNORECASE)
                    if gc_match:
                        gc = gc_match.group(1).upper()
                        parts = [int(p) for p in re.findall(r'\d+', date_text)]
                        row_iso = None
                        if len(parts) == 3:
                            if parts[0] > 1000:
                                row_iso = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
                            else:
                                row_iso_m = f"{parts[2]:04d}-{parts[0]:02d}-{parts[1]:02d}"
                                row_iso_d = f"{parts[2]:04d}-{parts[1]:02d}-{parts[0]:02d}"
                                if row_iso_m == target_date_iso:
                                    row_iso = row_iso_m
                                elif row_iso_d == target_date_iso:
                                    row_iso = row_iso_d

                        if row_iso == target_date_iso or any(df == date_text or df in date_text for df in possible_date_formats):
                            gc_codes.add(gc)

            found_list = sorted(list(gc_codes))
            log(f"Direct HTTP scan finished: found {len(found_list)} unique GC codes.", "success" if found_list else "info")
            return found_list
        except Exception:
            return None

    def _fetch_via_browser(
        self,
        username: str,
        password: str,
        possible_date_formats: List[str],
        log: Callable[[str, str], None]
    ) -> List[str]:
        """Fetch logs by logging in and scrolling /my/logs.aspx using Playwright."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("Playwright not available for browser scanning. Please install playwright.", "error")
            return []

        gc_codes = set()
        log(f"Logging into '{username}' to read log history...", "info")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
                page = context.new_page()

                # Sign in
                page.goto("https://www.geocaching.com/account/signin", timeout=30000)
                page.wait_for_load_state("domcontentloaded")

                # Remove Cookiebot overlay directly to prevent pointer-events blocking
                try:
                    page.evaluate("""() => {
                        let underlay = document.getElementById("CybotCookiebotDialogBodyUnderlay");
                        if (underlay) underlay.remove();
                        let dialog = document.getElementById("CybotCookiebotDialog");
                        if (dialog) dialog.remove();
                    }""")
                except Exception:
                    pass

                try:
                    page.wait_for_selector("#UsernameOrEmail", timeout=8000)
                    page.fill("#UsernameOrEmail", username)
                    page.fill("#Password", password)
                    page.click("#SignIn")
                except Exception as e:
                    log(f"Error filling login form: {e}", "error")
                    browser.close()
                    return []

                # Check if login redirected or failed
                try:
                    page.wait_for_url(lambda u: "signin" not in u.lower(), timeout=12000)
                except Exception:
                    err_elem = page.query_selector(".signup-validation-error, .field-validation-error, .validation-summary-errors, .alert-error")
                    if err_elem:
                        err_text = err_elem.inner_text().strip()
                        log(f"Login failed: {err_text}", "error")
                        browser.close()
                        return []
                    log("Login wait timed out or verification required.", "warning")

                # Navigate to my logs
                log("Scanning user log table...", "info")
                page.goto("https://www.geocaching.com/my/logs.aspx?s=1&lt=2", timeout=35000)
                page.wait_for_load_state("domcontentloaded")

                try:
                    page.wait_for_selector("table", timeout=12000)
                except Exception:
                    if "signin" in page.url.lower():
                        log(f"Could not access log page. Credentials for '{username}' might be incorrect.", "error")
                    else:
                        log("No log table found on user account.", "warning")
                    browser.close()
                    return []

                last_count = 0
                scroll_attempts = 0
                max_scrolls = 40
                no_new_rows = 0

                while scroll_attempts < max_scrolls:
                    rows_data = page.evaluate(r'''() => {
                        let rows = document.querySelectorAll("table tbody tr");
                        let data = [];
                        for(let r of rows) {
                            let tds = r.querySelectorAll("td");
                            if(tds.length >= 4) {
                                let dateText = tds[2].innerText.trim();
                                let cacheLink = tds[3].querySelector('a[href*="/geocache/"]');
                                if(cacheLink) {
                                    let m = cacheLink.getAttribute("href").match(/\/geocache\/(GC[A-Z0-9]+)/i);
                                    if(m) {
                                        data.push({dateText: dateText, gccode: m[1].toUpperCase()});
                                    }
                                }
                            }
                        }
                        return data;
                    }''')

                    current_matches = set()
                    for r in rows_data:
                        d_text = r['dateText']
                        if any(df == d_text or df in d_text for df in possible_date_formats):
                            current_matches.add(r['gccode'])
                        else:
                            parts = [int(p) for p in re.findall(r'\d+', d_text)]
                            if len(parts) == 3:
                                row_iso_m = f"{parts[2]:04d}-{parts[0]:02d}-{parts[1]:02d}"
                                row_iso_d = f"{parts[2]:04d}-{parts[1]:02d}-{parts[0]:02d}"
                                if any(df == row_iso_m or df == row_iso_d for df in possible_date_formats):
                                    current_matches.add(r['gccode'])

                    new_finds = current_matches - gc_codes
                    gc_codes.update(current_matches)

                    if len(rows_data) > last_count:
                        if len(gc_codes) > 0 and len(new_finds) == 0:
                            log("Target date passed. Finishing scan.", "info")
                            break

                        last_count = len(rows_data)
                        no_new_rows = 0
                        page.keyboard.press("End")
                        time.sleep(1.5)
                        scroll_attempts += 1
                    else:
                        no_new_rows += 1
                        if no_new_rows >= 3:
                            break
                        page.keyboard.press("PageDown")
                        time.sleep(1.5)

                browser.close()

            found_list = sorted(list(gc_codes))
            log(f"Scan finished: found {len(found_list)} unique GC codes.", "success")
            return found_list
        except Exception as e:
            log(f"Browser scan error: {str(e)}", "error")
            return []

    def _fetch_via_public_api(
        self,
        username: str,
        target_date_str: str,
        log: Callable[[str, str], None]
    ) -> List[str]:
        """Fetch logs for target user on specific date using Geocaching search API."""
        try:
            url = f"{self.client.API_PROXY_URL}/web/search/v2"
            bearer = self.client.get_oauth_bearer_token()
            headers = {"Accept": "application/json"}
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"

            skip = 0
            take = 200
            matched_codes: List[str] = []
            max_pages = 50  # Scan up to 10,000 logs (handles even massive 500+ powertrail days)

            log(f"Searching caches found by '{username}' on {target_date_str}...", "info")

            for page_num in range(1, max_pages + 1):
                params = {
                    "fb": username,
                    "sort": "foundDate",
                    "asc": "false",
                    "sa": "1",  # Include archived caches (Show Archived)
                    "skip": str(skip),
                    "take": str(take),
                    "app": "cgeo"
                }

                resp = self.client.session.get(url, headers=headers, params=params, timeout=15)
                if resp.status_code != 200:
                    log(f"Search API returned HTTP {resp.status_code}", "warning")
                    break

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    break

                passed_target = False
                for item in results:
                    lfd = item.get("lastFoundDate")
                    if not lfd:
                        continue
                    date_part = lfd.split("T")[0]
                    code = item.get("code")
                    if date_part == target_date_str:
                        if code and code not in matched_codes:
                            matched_codes.append(code)
                    elif date_part < target_date_str:
                        passed_target = True
                        break

                if passed_target:
                    break

                if len(matched_codes) > 0:
                    log(f"Scanned {skip + len(results)} logs... found {len(matched_codes)} cache(s) on target date so far.", "info")

                skip += take

            log(f"Found {len(matched_codes)} cache(s) logged by '{username}' on {target_date_str}.", "info")
            return matched_codes
        except Exception as e:
            log(f"Search API error: {str(e)}", "warning")
        return []
