from playwright.sync_api import sync_playwright
import datetime
import time
import os
import json
import re

def main():
    print("AutoLogger started")
    Username, Password, GCCodes, templates, ShowScreen = readConfig()

    mode_options = [
        "FOUND",
        "DNF",
        "NOTE",
        "NEEDS_OWNER_ATTENTION",
        "NEEDS_REVIEWER_ATTENTION",
        "IGNORE",
        "EDIT_FOUND_LOGS",
        "DELETE_FOUND_LOGS"
    ]
    print("\nSelect mode:")
    for i, option in enumerate(mode_options, 1):
        print(f"{i}. {option}")
    
    while True:
        try:
            choice = int(input("Enter mode number: ").strip())
            if 1 <= choice <= len(mode_options):
                Mode = mode_options[choice - 1]
                break
            else:
                print("Invalid choice. Enter a number from the list.")
        except ValueError:
            print("Invalid input. Enter a number.")

    Date = None
    LogText = ""
    EditLogType = ""
    EditDate = ""
    EditLogText = ""
    if Mode not in ["IGNORE", "DELETE_FOUND_LOGS", "EDIT_FOUND_LOGS"]:
        Date = input("Enter date [YYYY-MM-DD] (or press enter to insert today): ").strip()
        if not Date:
            Date = datetime.date.today().strftime("%Y-%m-%d")
            
        print("\nSelect log text:")
        print("1. [New log]")
        for i, t in enumerate(templates, 2):
            words = t.split()
            if len(words) <= 6:
                preview = " ".join(words)
            else:
                preview = " ".join(words[:5]) + " (***) " + words[-1]
            print(f"{i}. {preview}")
            
        while True:
            try:
                choice = int(input("Enter text number: ").strip())
                if choice == 1:
                    LogText = input("Enter new log text: ").strip()
                    break
                elif 2 <= choice <= len(templates) + 1:
                    LogText = templates[choice - 2]
                    break
                else:
                    print("Invalid choice. Enter a number from the list.")
            except ValueError:
                print("Invalid input. Enter a number.")

    elif Mode == "EDIT_FOUND_LOGS":
        EditLogType = input("Enter new log type (e.g. FOUND) (Press enter not to edit log type): ").strip().upper()
        EditDate = input("Enter new date [YYYY-MM-DD] (Press enter not to edit date): ").strip()
        
        print("\nSelect new log text (Press enter not to edit log text):")
        print("1. [New log]")
        for i, t in enumerate(templates, 2):
            words = t.split()
            if len(words) <= 6:
                preview = " ".join(words)
            else:
                preview = " ".join(words[:5]) + " (***) " + words[-1]
            print(f"{i}. {preview}")
            
        while True:
            choice_str = input("Enter text number (or press enter to skip): ").strip()
            if not choice_str:
                break
            try:
                choice = int(choice_str)
                if choice == 1:
                    EditLogText = input("Enter new log text: ").strip()
                    break
                elif 2 <= choice <= len(templates) + 1:
                    EditLogText = templates[choice - 2]
                    break
                else:
                    print("Invalid choice. Enter a number from the list.")
            except ValueError:
                print("Invalid input. Enter a number.")

    if Mode == "NEEDS_REVIEWER_ATTENTION":
        print("\n\033[93m⚠️ WARNING: You selected bulk logging 'Needs reviewer attention'.\033[0m")
        print("\033[93mThis will alert reviewers to ALL specified caches.\033[0m")
        while True:
            response = input("Do you really want to continue? [y/N]: ").strip().lower()
            if response == 'y':
                break
            elif response in ['n', '']:
                print("Operation cancelled by user.")
                return
            else:
                print("Please enter 'y' for yes or 'n' for no.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not ShowScreen)
        context = browser.new_context()
        page = context.new_page()

        # Log in
        CheckForGDPR(page)
        page.wait_for_load_state()
        Login(page, Username, Password)

        # Log caches
        Language = CheckLanguage(page)
        print(f"Detected language: {Language}")

        print(f"Total caches to process: {len(GCCodes)}")

        if Mode in ["LOG", "FOUND", "DNF", "NOTE", "NEEDS_OWNER_ATTENTION", "NEEDS_REVIEWER_ATTENTION"]:
            LogCaches(page, GCCodes, LogText, Date, Language, Mode)
        elif Mode == "IGNORE":
            PutToIgnoreList(page, GCCodes, LogText, Date, Language)
        elif Mode == "EDIT_FOUND_LOGS":
            EditFoundLogs(page, GCCodes, EditLogType, EditDate, EditLogText, Language)
        elif Mode == "DELETE_FOUND_LOGS":
            DeleteFoundLogs(page, GCCodes, Language)


def PutToIgnoreList(page, GCCodes, LogText, Date, Language):
    for GCCode in GCCodes:
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                response = page.goto("https://www.geocaching.com/geocache/" + GCCode, timeout=10000)
                if response is not None and response.status == 200:
                    success = True
                    break
            except Exception:
                pass
            time.sleep(2)

        if not success:
            print(f"❌ Failed to load page for {GCCode}")
            continue
        page.wait_for_load_state()
        CheckForGDPR(page)
        page.wait_for_load_state()

        Element = "#ctl00_ContentBody_GeoNav_uxIgnoreBtn > a"
        button = page.locator(Element)
        button.click()
        page.wait_for_load_state()

        try:
            if Language == "EN":
                button = page.wait_for_selector("text='Yes. Ignore it.'", timeout=500)
            else:
                button = page.wait_for_selector("text='Ano. Ignoruj to.'", timeout=500)
            print(f"Ignoring {GCCode}")
            button.click()
        except:
            print("Already ignored " + GCCode)
            continue
        page.wait_for_load_state()


def LogCaches(page, GCCodes, LogText, Date, Language, Mode="LOG"):
    for GCCode in GCCodes:
        # Attempt to load the page with retries
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                response = page.goto(f"https://www.geocaching.com/live/geocache/{GCCode}/log", timeout=10000)
                if response is not None and response.status == 200:
                    success = True
                    break
                else:
                    print(f"⚠️ Attempt {attempt + 1}: page {GCCode} unavailable (status {response.status if response else 'none'})")
            except Exception as e:
                print(f"⚠️ Attempt {attempt + 1} failed while loading {GCCode}: {e}")
            time.sleep(2)

        if not success:
            print(f"❌ Failed to load page for {GCCode} even after {max_retries} attempts")
            continue

        page.wait_for_load_state()
        CheckForGDPR(page)
        page.wait_for_load_state()

        # Open log type selection
        try:
            log_type_dropdown = page.locator('//label[contains(., "Typ logu")]/div/div/div[2]')
            log_type_dropdown.click()
            time.sleep(0.5)
        except Exception as e:
            print(f"❌ Failed to click dropdown for {GCCode}: {e}")
            continue

        # Wait for the selected log type button
        log_type_texts = {
            "FOUND": {"EN": "Found it", "CZ": "Nalezeno"},
            "DNF": {"EN": "Didn\'t find it", "CZ": "Nenalezeno"},
            "NOTE": {"EN": "Write note", "CZ": "Poznámka"},
            "NEEDS_OWNER_ATTENTION": {"EN": "Needs owner attention", "CZ": "Vyžadována pozornost vlastníka keše"},
            "NEEDS_REVIEWER_ATTENTION": {"EN": "Needs reviewer attention", "CZ": "Vyžaduje pozornost reviewera"}
        }
        target_text = log_type_texts.get(Mode, log_type_texts["FOUND"])[Language]

        max_wait = 3
        for i in range(max_wait):
            try:
                if page.locator(f'text="{target_text}"').is_visible():
                    break
            except:
                pass
            time.sleep(1)
        else:
            print(f"❌ Log type button ({target_text}) did not appear even after {max_wait}s – skipping {GCCode}")
            continue

        # Cache name
        try:
            CacheName = page.locator(
                '#__next > div > div.page-container.flex.flex-col.flex-grow.items-center > main > div > div.content-container > div > section > h2 > a'
            ).inner_text()
        except:
            CacheName = "(not found)"

        # Click on log type
        try:
            page.locator(f'text="{target_text}"').click()
        except:
            print(f"⚠️ Cannot click log type ({target_text}) for {GCCode} – {CacheName}")
            continue

        page.wait_for_load_state()
        time.sleep(0.5)

        # Fill log text
        try:
            text_field = page.locator('//*[@id="gc-md-editor_md"]')
            text_field.fill(LogText)
        except:
            print(f"⚠️ Failed to fill log text for {GCCode}")
            continue
            
        # Set date
        try:
            Year, Month, Day = Date.split("-")
            page.locator('//*[@id="log-date"]' ).click()
            time.sleep(0.5)

            page.fill(
                "body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-months > div > div > div > input",
                Year,
            )

            months = (
                ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
                if Language == "EN" else
                ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                 "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
            )
            month = months[int(Month) - 1]
            page.select_option(
                "body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-months > div > div > select",
                value=month
            )

            firstField = 0
            while True:
                firstField += 1
                Element = f"body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-innerContainer > div > div.flatpickr-days > div > span:nth-child({firstField})"
                if page.locator(Element).inner_text() == "1":
                    break

            Element = f"body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-innerContainer > div > div.flatpickr-days > div > span:nth-child({firstField + int(Day) - 1})"
            page.locator(Element).click()
        except Exception as e:
            print(f"⚠️ Error setting date for {GCCode}: {e}")
            continue

        # Submit log
        try:
            submit_selector = (
                "#__next > div > div.flex.flex-col.flex-grow.items-center.page-container > main > div > "
                "div.content-container > div > form > div.mt-5.mb-6.mx-0.flex.flex-col-reverse.gap-3."
                "md\\:flex-row.md\\:justify-end > div.post-button-container.flex.items-center."
                "justify-center.md\\:flex-row > button"
            )
            submit_button = page.locator(submit_selector)
            submit_button.click()
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ Error submitting log for {GCCode}: {e}")
            continue

        try:
            page.wait_for_load_state()
        except:
            pass


        print(f"✅ Logged {GCCode} – {CacheName}")
        time.sleep(1)


def EditFoundLogs(page, GCCodes, EditLogType, EditDate, EditLogText, Language):
    for GCCode in GCCodes:
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                response = page.goto("https://www.geocaching.com/geocache/" + GCCode, timeout=10000)
                if response is not None and response.status == 200:
                    success = True
                    break
            except Exception:
                pass
            time.sleep(2)

        if not success:
            print(f"❌ Failed to load page for {GCCode}")
            continue

        page.wait_for_load_state()
        CheckForGDPR(page)
        page.wait_for_load_state()

        # Find the "View Log" link
        try:
            if Language == "EN":
                view_log_selector = "a[title='View Log'], a[title='View log']"
            else:
                view_log_selector = "a[title='Zobrazit log'], a[title='Zobrazit Log']"
            
            view_log_link = page.locator(view_log_selector).first
            if view_log_link.is_visible(timeout=3000):
                view_log_link.click()
            else:
                print(f"⚠️ 'View log' link not found for {GCCode}. Log might not exist.")
                continue
        except Exception as e:
            print(f"⚠️ Error finding 'View log' link for {GCCode}: {e}")
            continue
            
        page.wait_for_load_state()
        time.sleep(2)
        
        try:
            try:
                # Find the first visible edit button
                edit_button = page.locator("button[data-testid='edit-log']:visible").first
                edit_button.wait_for(timeout=5000)
                edit_button.click()
                time.sleep(2)
                page.wait_for_load_state()
                time.sleep(1)

                # Edit log type
                if EditLogType:
                    try:
                        log_type_dropdown = page.locator('//label[contains(., "Typ logu")]/div/div/div[2]')
                        if not log_type_dropdown.is_visible():
                            log_type_dropdown = page.locator('//label[contains(., "Log type")]/div/div/div[2]')
                        if log_type_dropdown.is_visible():
                            log_type_dropdown.click()
                            time.sleep(0.5)
                            
                            log_type_texts = {
                                "FOUND": {"EN": "Found it", "CZ": "Nalezeno"},
                                "DNF": {"EN": "Didn't find it", "CZ": "Nenalezeno"},
                                "NOTE": {"EN": "Write note", "CZ": "Poznámka"},
                                "NEEDS_OWNER_ATTENTION": {"EN": "Needs owner attention", "CZ": "Vyžadována pozornost vlastníka keše"},
                                "NEEDS_REVIEWER_ATTENTION": {"EN": "Needs reviewer attention", "CZ": "Vyžaduje pozornost reviewera"}
                            }
                            target_text = log_type_texts.get(EditLogType, log_type_texts["FOUND"])[Language]
                            page.locator(f'text="{target_text}"').click()
                            time.sleep(0.5)
                    except Exception as e:
                        print(f"⚠️ Error changing log type for {GCCode}: {e}")
                
                # Edit date
                if EditDate:
                    try:
                        Year, Month, Day = EditDate.split("-")
                        page.locator('//*[@id="log-date"]').click()
                        time.sleep(0.5)

                        page.fill(
                            "body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-months > div > div > div > input",
                            Year,
                        )

                        months = (
                            ["January", "February", "March", "April", "May", "June",
                             "July", "August", "September", "October", "November", "December"]
                            if Language == "EN" else
                            ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                             "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
                        )
                        month = months[int(Month) - 1]
                        page.select_option(
                            "body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-months > div > div > select",
                            value=month
                        )

                        firstField = 0
                        while True:
                            firstField += 1
                            Element = f"body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-innerContainer > div > div.flatpickr-days > div > span:nth-child({firstField})"
                            if page.locator(Element).inner_text() == "1":
                                break

                        Element = f"body > div.flatpickr-calendar.animate.arrowTop.arrowLeft.open > div.flatpickr-innerContainer > div > div.flatpickr-days > div > span:nth-child({firstField + int(Day) - 1})"
                        page.locator(Element).click()
                    except Exception as e:
                        print(f"⚠️ Error changing date for {GCCode}: {e}")

                # Edit log text
                if EditLogText:
                    try:
                        text_field = page.locator('//*[@id="gc-md-editor_md"]')
                        text_field.fill(EditLogText)
                    except Exception as e:
                        print(f"⚠️ Error changing log text for {GCCode}: {e}")

                # Submit updated log
                try:
                    submit_selector = (
                        "#__next > div > div.flex.flex-col.flex-grow.items-center.page-container > main > div > "
                        "div.content-container > div > form > div.mt-5.mb-6.mx-0.flex.flex-col-reverse.gap-3."
                        "md\\:flex-row.md\\:justify-end > div.post-button-container.flex.items-center."
                        "justify-center.md\\:flex-row > button"
                    )
                    submit_button = page.locator(submit_selector)
                    if submit_button.is_visible():
                        submit_button.click()
                    else:
                        fallback_submit = page.locator("button:has-text('Aktualizovat log'), button:has-text('Update log')").first
                        if fallback_submit.is_visible():
                            fallback_submit.click()
                    time.sleep(2)
                    page.wait_for_load_state()
                    print(f"✅ Successfully updated log for {GCCode}")
                except Exception as e:
                    print(f"⚠️ Error submitting updated log for {GCCode}: {e}")
                    
            except Exception as e:
                print(f"⚠️ 'Edit log' button not found or error loading edit form for {GCCode}: {e}")
                
        except Exception as e:
            print(f"⚠️ Error editing log for {GCCode}: {e}")
            continue

        time.sleep(1)


def DeleteFoundLogs(page, GCCodes, Language):
    for GCCode in GCCodes:
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                response = page.goto("https://www.geocaching.com/geocache/" + GCCode, timeout=10000)
                if response is not None and response.status == 200:
                    success = True
                    break
            except Exception:
                pass
            time.sleep(2)

        if not success:
            print(f"❌ Failed to load page for {GCCode}")
            continue

        page.wait_for_load_state()
        CheckForGDPR(page)
        page.wait_for_load_state()

        # Find the "View Log" link
        try:
            if Language == "EN":
                view_log_selector = "a[title='View Log'], a[title='View log']"
            else:
                view_log_selector = "a[title='Zobrazit log'], a[title='Zobrazit Log']"
            
            view_log_link = page.locator(view_log_selector).first
            if view_log_link.is_visible(timeout=3000):
                view_log_link.click()
            else:
                print(f"⚠️ 'View log' link not found for {GCCode}. Log might not exist.")
                continue
        except Exception as e:
            print(f"⚠️ Error finding 'View log' link for {GCCode}: {e}")
            continue
            
        page.wait_for_load_state()
        time.sleep(2)
        
        try:
            # Setup dialog handler to auto-accept the confirmation dialog
            page.once("dialog", lambda dialog: dialog.accept())
            
            # The user mentioned the button is called "Delete log" even in Czech
            delete_button = page.locator("a:has-text('Delete log'), button:has-text('Delete log'), span:has-text('Delete log')").first
                
            if delete_button.is_visible(timeout=3000):
                delete_button.click()
                time.sleep(2)
                
                # Check for secondary custom confirmation modal just in case
                try:
                    # Use Playwright get_by_role to exactly find the "Delete" button (ignoring "Delete log")
                    confirm_btn = page.get_by_role("button", name="Delete", exact=True)
                    if confirm_btn.is_visible(timeout=3000):
                        confirm_btn.click()
                        time.sleep(2)
                    else:
                        # Fallback: try to find any 'Delete' button within a dialog element (popup window)
                        fallback_btn = page.locator("[role='dialog'] button:has-text('Delete'), .modal button:has-text('Delete')").first
                        if fallback_btn.is_visible(timeout=2000):
                            fallback_btn.click()
                            time.sleep(2)
                        else:
                            print(f"⚠️ Confirmation 'Delete' button not found for {GCCode}")
                except Exception as e:
                    print(f"⚠️ Error clicking confirmation 'Delete' for {GCCode}: {e}")

                print(f"🗑️ Successfully deleted log for {GCCode}")
            else:
                print(f"⚠️ 'Delete log' button not found on log page for {GCCode}")
                
        except Exception as e:
            print(f"⚠️ Error deleting log for {GCCode}: {e}")
            continue

        time.sleep(1)


def CheckLanguage(page):
    try:
        page.wait_for_selector("text='Back to My Lists'", timeout=500)
        return "EN"
    except:
        return "CZ"


def CheckForGDPR(page):
    gdpr_button = page.query_selector('//*[@id="CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"]')
    if gdpr_button is not None:
        print("Accepting GDPR cookie consent")
        gdpr_button.click()


def Login(page, Username, Password):
    page.goto("https://www.geocaching.com/account/signin?returnUrl=")
    page.wait_for_load_state()
    CheckForGDPR(page)
    page.wait_for_load_state()

    page.fill("#UsernameOrEmail", Username)
    page.fill("#Password", Password)
    page.click("#SignIn")
    page.wait_for_load_state()
    print("Login successful")

def extract_gc_codes_from_folder(folder_path):
    gc_codes = []
    print(f"Scanning files in folder: {folder_path}")
    
    for file_name in os.listdir(folder_path):
        print(f"Checking file: {file_name}")
        file_path = os.path.join(folder_path, file_name)

        if file_name.endswith('.loc'):
            print(f"Reading content of .loc file: {file_name}")
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
                found_codes = re.findall(r'GC\w+', content)
                print(f"Found GC codes in {file_name}: {found_codes}")
                gc_codes.extend(found_codes)

        elif file_name.endswith('.gpx'):
            print(f"Reading content of .gpx file: {file_name}")
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
                found_codes = re.findall(r'<name>(GC\w+)</name>', content)
                print(f"Found GC codes in {file_name}: {found_codes}")
                gc_codes.extend(found_codes)

    return gc_codes


def readConfig():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file_path = os.path.join(script_dir, "InputData.json")

    with open(input_file_path, encoding="utf-8") as json_file:
        data = json.load(json_file)
        Username = data["Username"]
        Password = data["Password"]

        # First, load codes from files
        GCCodes = extract_gc_codes_from_folder(script_dir)
        print(f"Loaded GC codes from files: {GCCodes}")

        # Then add any manually entered codes from JSON
        extra_codes_raw = data["GCCodes"].strip()
        if extra_codes_raw:
            extra_codes = [code.strip() for code in extra_codes_raw.split(",") if code.strip()]
            GCCodes += extra_codes
            print(f"Added GC codes from InputData.json: {extra_codes}")

        # Remove any duplicates
        GCCodes = list(set(GCCodes))
        print(f"Final list of GC codes: {GCCodes}")

        templates = []
        for key, value in data.items():
            if key.startswith("LogTemplate_"):
                templates.append(value)

        ShowScreen = data["ShowScreen"]

    return Username, Password, GCCodes, templates, ShowScreen



if __name__ == "__main__":
    main()
