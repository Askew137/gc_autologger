from playwright.sync_api import sync_playwright
import datetime
import time
import os
import json
import re

def main():
    accounts, user_1_default, FolderPath, templates, ShowScreen = readConfig()

    default_user = accounts.get("1", {}).get("Username", "User")
    print(f"\n[AutoLogger_GC v1.2.0] Hi {default_user}, let's log some caches!")

    if user_1_default and "1" in accounts:
        Username = accounts["1"]["Username"]
        Password = accounts["1"]["Password"]
    else:
        print("Select account:")
        sorted_idx = sorted(accounts.keys(), key=lambda x: int(x))
        for i, idx in enumerate(sorted_idx, 1):
            print(f"{i}. {accounts[idx]['Username']}")
            
        while True:
            try:
                acc_choice = int(input("Enter account number: ").strip())
                if 1 <= acc_choice <= len(sorted_idx):
                    Username = accounts[sorted_idx[acc_choice - 1]]["Username"]
                    Password = accounts[sorted_idx[acc_choice - 1]]["Password"]
                    break
                else:
                    print("Invalid choice. Enter a number from the list.")
            except ValueError:
                print("Invalid input. Enter a number.")

    mode_options = [
        ("FOUND", "Found it"),
        ("DNF", "DNF"),
        ("NOTE", "Write note"),
        ("NEEDS_OWNER_ATTENTION", "Needs maintenance"),
        ("NEEDS_REVIEWER_ATTENTION", "Needs archive"),
        ("IGNORE", "Ignore"),
        ("EDIT_FOUND_LOGS", "Edit FI logs"),
        ("DELETE_FOUND_LOGS", "Delete FI logs"),
        ("COPY_USER", "Log the same caches as [user]"),
        ("EDIT_PREFERENCES", "Edit preferences")
    ]
    print("\nSelect mode:")
    for i, (internal_mode, display_name) in enumerate(mode_options, 1):
        print(f"{i}. {display_name}")
    
    while True:
        try:
            choice = int(input("Enter mode number: ").strip())
            if 1 <= choice <= len(mode_options):
                Mode = mode_options[choice - 1][0]
                break
            else:
                print("Invalid choice. Enter a number from the list.")
        except ValueError:
            print("Invalid input. Enter a number.")

    if Mode == "EDIT_PREFERENCES":
        import subprocess
        import sys
        script_dir = os.path.dirname(os.path.abspath(__file__))
        json_file = os.path.join(script_dir, "InputData.json")
        print(f"\nOpening {json_file} for editing...")
        try:
            if sys.platform == "win32":
                os.startfile(json_file)
            else:
                editor = os.environ.get('EDITOR', 'nano')
                subprocess.call([editor, json_file])
        except Exception as e:
            print(f"Error opening editor: {e}")
        return

    TargetUsername = ""
    TargetPassword = ""
    
    if Mode == "COPY_USER":
        print("\nSelect the target user to copy logs from:")
        target_accounts = {k: v for k, v in accounts.items() if v["Username"] != Username}
        if not target_accounts:
            print("No other accounts found in config to copy from. Add more accounts to JSON.")
            return
            
        sorted_target_idx = sorted(target_accounts.keys(), key=lambda x: int(x))
        for i, idx in enumerate(sorted_target_idx, 1):
            print(f"{i}. {target_accounts[idx]['Username']}")
            
        while True:
            try:
                t_choice = int(input("Enter target account number: ").strip())
                if 1 <= t_choice <= len(sorted_target_idx):
                    TargetUsername = target_accounts[sorted_target_idx[t_choice - 1]]["Username"]
                    TargetPassword = target_accounts[sorted_target_idx[t_choice - 1]]["Password"]
                    break
                else:
                    print("Invalid choice. Enter a number from the list.")
            except ValueError:
                print("Invalid input. Enter a number.")
        
        GCCodes = []
    else:
        print("\nSelect input method:")
        print("1. Upload all the relevant files")
        print("2. Upload a single file")
        print("3. [insert GC codes]")
        
        GCCodes = []
        source_msg = ""
        while True:
            try:
                input_choice = int(input("Enter input method number: ").strip())
                if input_choice == 1:
                    GCCodes = extract_gc_codes_from_folder(FolderPath)
                    source_msg = "across all files"
                    break
                elif input_choice == 2:
                    files = [f for f in os.listdir(FolderPath) if f.endswith(('.loc', '.gpx'))]
                    if not files:
                        print(f"No .loc or .gpx files found in {FolderPath}")
                        continue
                    print("\nSelect a file:")
                    for idx, f in enumerate(files, 1):
                        print(f"{idx}. {f}")
                    while True:
                        try:
                            f_choice = int(input("Enter file number: ").strip())
                            if 1 <= f_choice <= len(files):
                                selected_file = files[f_choice - 1]
                                GCCodes = extract_gc_codes_from_file(os.path.join(FolderPath, selected_file))
                                source_msg = f"in {selected_file}"
                                break
                            else:
                                print("Invalid choice. Enter a number from the list.")
                        except ValueError:
                            print("Invalid input. Enter a number.")
                    break
                elif input_choice == 3:
                    raw_codes = input("Enter GC codes (comma or space separated): ").strip()
                    GCCodes = re.findall(r'GC\w+', raw_codes.upper())
                    source_msg = "from input"
                    break
                else:
                    print("Invalid choice. Enter 1, 2, or 3.")
            except ValueError:
                print("Invalid input. Enter a number.")
                
        GCCodes = list(set(GCCodes))
        print(f"\nFound a total of {len(GCCodes)} unique GC codes {source_msg}.")
        if not GCCodes:
            print("No GC codes to process. Exiting.")
            return

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
        
        if Mode == "COPY_USER":
            print(f"\n--- Logging into target account ({TargetUsername}) to fetch GC codes ---")
            context1 = browser.new_context()
            page1 = context1.new_page()
            
            CheckForGDPR(page1)
            page1.wait_for_load_state()
            Login(page1, TargetUsername, TargetPassword)
            
            GCCodes = ExtractCachesFromUser(page1, Date)
            context1.close()
            
            print(f"\nFound {len(GCCodes)} unique GC codes logged by {TargetUsername} on {Date}.")
            if not GCCodes:
                print("No caches to copy. Exiting.")
                browser.close()
                return
                
            print(f"\n--- Logging into main account ({Username}) to log the caches ---")
            Mode = "FOUND"

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

        success_count = 0
        action_word = "logged"
        if Mode in ["LOG", "FOUND", "DNF", "NOTE", "NEEDS_OWNER_ATTENTION", "NEEDS_REVIEWER_ATTENTION"]:
            success_count = LogCaches(page, GCCodes, LogText, Date, Language, Mode)
        elif Mode == "IGNORE":
            success_count = PutToIgnoreList(page, GCCodes, LogText, Date, Language)
            action_word = "ignored"
        elif Mode == "EDIT_FOUND_LOGS":
            success_count = EditFoundLogs(page, GCCodes, EditLogType, EditDate, EditLogText, Language)
            action_word = "updated"
        elif Mode == "DELETE_FOUND_LOGS":
            success_count = DeleteFoundLogs(page, GCCodes, Language)
            action_word = "deleted"

        print(f"\n✨ {success_count}/{len(GCCodes)} caches have been successfully {action_word} ✨\n")
        input("[press enter to exit]")


def PutToIgnoreList(page, GCCodes, LogText, Date, Language):
    total = len(GCCodes)
    success_count = 0
    for idx, GCCode in enumerate(GCCodes, 1):
        prefix = f"[{idx}/{total}]"
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
            print(f"{prefix} ❌ Failed to load page for {GCCode}")
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
            print(f"{prefix} Ignoring {GCCode}")
            button.click()
            success_count += 1
        except:
            print(f"{prefix} Already ignored {GCCode}")
            success_count += 1
            continue
        page.wait_for_load_state()
        
    return success_count


def LogCaches(page, GCCodes, LogText, Date, Language, Mode="LOG"):
    sticky_date_saved = False
    total = len(GCCodes)
    success_count = 0
    for idx, GCCode in enumerate(GCCodes, 1):
        prefix = f"[{idx}/{total}]"
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
                    print(f"{prefix} ⚠️ Attempt {attempt + 1}: page {GCCode} unavailable (status {response.status if response else 'none'})")
            except Exception as e:
                print(f"{prefix} ⚠️ Attempt {attempt + 1} failed while loading {GCCode}: {e}")
            time.sleep(2)

        if not success:
            print(f"{prefix} ❌ Failed to load page for {GCCode} even after {max_retries} attempts")
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
            print(f"{prefix} ❌ Failed to click dropdown for {GCCode}: {e}")
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
            print(f"{prefix} ❌ Skipping {GCCode}")
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
            print(f"{prefix} ⚠️ Cannot click log type ({target_text}) for {GCCode} – {CacheName}")
            continue

        page.wait_for_load_state()
        time.sleep(0.5)

        # Fill log text
        try:
            text_field = page.locator('//*[@id="gc-md-editor_md"]')
            text_field.fill(LogText)
        except:
            print(f"{prefix} ⚠️ Failed to fill log text for {GCCode}")
            continue
            
        # Set date
        if Mode in ["FOUND", "DNF", "NOTE"] and not sticky_date_saved:
            try:
                Year, Month, Day = Date.split("-")
                page.locator('//*[@id="log-date"]' ).click()
                time.sleep(0.5)

                # 1. Změna roku klikáním na fyzické šipky
                year_input = page.locator(".flatpickr-calendar.open input.cur-year")
                try:
                    curr_y = int(year_input.input_value())
                    target_y = int(Year)
                    diff = curr_y - target_y
                    
                    if diff > 0:
                        sipka_dolu = page.locator(".flatpickr-calendar.open span.arrowDown")
                        for _ in range(diff):
                            sipka_dolu.click()
                            time.sleep(0.1)
                    elif diff < 0:
                        sipka_nahoru = page.locator(".flatpickr-calendar.open span.arrowUp")
                        for _ in range(-diff):
                            sipka_nahoru.click()
                            time.sleep(0.1)
                except Exception as e:
                    print(f"{prefix} ⚠️ Error clicking year arrows: {e}")
                            
                time.sleep(0.5)

                # 2. Vybrat měsíc
                months = (
                    ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December"]
                    if Language == "EN" else
                    ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                     "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
                )
                month = months[int(Month) - 1]
                page.select_option(".flatpickr-calendar.open .flatpickr-months select", label=month)
                time.sleep(0.5)

                # 3. Kliknout na den
                firstField = 0
                while True:
                    firstField += 1
                    Element = f".flatpickr-calendar.open .flatpickr-days span.flatpickr-day:nth-child({firstField})"
                    if page.locator(Element).inner_text().strip() == "1":
                        break

                Element = f".flatpickr-calendar.open .flatpickr-days span.flatpickr-day:nth-child({firstField + int(Day) - 1})"
                page.locator(Element).click()
                time.sleep(0.5)
                
                try:
                    pin_button = page.locator("button[aria-label='Enable sticky date']")
                    if pin_button.is_visible():
                        pin_button.click()
                except Exception as pin_e:
                    print(f"{prefix} ⚠️ Error clicking sticky date pin for {GCCode}: {pin_e}")

            except Exception as e:
                print(f"{prefix} ⚠️ Error setting date for {GCCode}: {e}")
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
            print(f"{prefix} ⚠️ Error submitting log for {GCCode}: {e}")
            continue

        try:
            page.wait_for_load_state()
        except:
            pass

        # Update the state so next caches skip date selection
        if Mode in ["FOUND", "DNF", "NOTE"]:
            sticky_date_saved = True

        print(f"{prefix} ✅ Logged {GCCode} – {CacheName}")
        success_count += 1
        time.sleep(1)
        
    return success_count


def EditFoundLogs(page, GCCodes, EditLogType, EditDate, EditLogText, Language):
    total = len(GCCodes)
    success_count = 0
    for idx, GCCode in enumerate(GCCodes, 1):
        prefix = f"[{idx}/{total}]"
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
            print(f"{prefix} ❌ Failed to load page for {GCCode}")
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
                print(f"{prefix} ⚠️ 'View log' link not found for {GCCode}. Log might not exist.")
                continue
        except Exception as e:
            print(f"{prefix} ⚠️ Error finding 'View log' link for {GCCode}: {e}")
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
                        print(f"{prefix} ⚠️ Error changing log type for {GCCode}: {e}")
                
                # Edit date
                if EditDate:
                    try:
                        Year, Month, Day = EditDate.split("-")
                        date_input = page.locator('//*[@id="log-date"]')
                        date_input.click()
                        time.sleep(0.5)
                        
                        months = (
                            ["January", "February", "March", "April", "May", "June",
                             "July", "August", "September", "October", "November", "December"]
                            if Language == "EN" else
                            ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
                             "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
                        )
                        month = months[int(Month) - 1]
                        
                        # 1. Změna roku klikáním na fyzické šipky
                        year_input = page.locator(".flatpickr-calendar.open input.cur-year")
                        try:
                            curr_y = int(year_input.input_value())
                            target_y = int(Year)
                            diff = curr_y - target_y
                            
                            if diff > 0:
                                sipka_dolu = page.locator(".flatpickr-calendar.open span.arrowDown")
                                for _ in range(diff):
                                    sipka_dolu.click()
                                    time.sleep(0.1)
                            elif diff < 0:
                                sipka_nahoru = page.locator(".flatpickr-calendar.open span.arrowUp")
                                for _ in range(-diff):
                                    sipka_nahoru.click()
                                    time.sleep(0.1)
                        except Exception as e:
                            print(f"{prefix} ⚠️ Error clicking year arrows: {e}")
                            
                        time.sleep(0.5)

                        # 2. Vybrat měsíc
                        page.select_option(".flatpickr-calendar.open .flatpickr-months select", label=month)
                        time.sleep(0.5)

                        # 3. Kliknout na den - to finálně uloží datum do paměti Geocachingu!
                        firstField = 0
                        while True:
                            firstField += 1
                            Element = f".flatpickr-calendar.open .flatpickr-days span.flatpickr-day:nth-child({firstField})"
                            if page.locator(Element).inner_text().strip() == "1":
                                break

                        Element = f".flatpickr-calendar.open .flatpickr-days span.flatpickr-day:nth-child({firstField + int(Day) - 1})"
                        page.locator(Element).click()
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"{prefix} ⚠️ Error changing date for {GCCode}: {e}")

                # Edit log text
                if EditLogText:
                    try:
                        text_field = page.locator('//*[@id="gc-md-editor_md"]')
                        text_field.fill(EditLogText)
                    except Exception as e:
                        print(f"{prefix} ⚠️ Error changing log text for {GCCode}: {e}")

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
                    print(f"{prefix} ✅ Successfully updated log for {GCCode}")
                    success_count += 1
                except Exception as e:
                    print(f"{prefix} ⚠️ Error submitting updated log for {GCCode}: {e}")
                    
            except Exception as e:
                print(f"{prefix} ⚠️ 'Edit log' button not found or error loading edit form for {GCCode}: {e}")
                
        except Exception as e:
            print(f"{prefix} ⚠️ Error editing log for {GCCode}: {e}")
            continue

        time.sleep(1)
        
    return success_count


def DeleteFoundLogs(page, GCCodes, Language):
    total = len(GCCodes)
    success_count = 0
    for idx, GCCode in enumerate(GCCodes, 1):
        prefix = f"[{idx}/{total}]"
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
            print(f"{prefix} ❌ Failed to load page for {GCCode}")
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
                print(f"{prefix} ⚠️ 'View log' link not found for {GCCode}. Log might not exist.")
                continue
        except Exception as e:
            print(f"{prefix} ⚠️ Error finding 'View log' link for {GCCode}: {e}")
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
                            print(f"{prefix} ⚠️ Confirmation 'Delete' button not found for {GCCode}")
                except Exception as e:
                    print(f"{prefix} ⚠️ Error clicking confirmation 'Delete' for {GCCode}: {e}")

                print(f"{prefix} 🗑️ Successfully deleted log for {GCCode}")
                success_count += 1
            else:
                print(f"{prefix} ⚠️ 'Delete log' button not found on log page for {GCCode}")
                
        except Exception as e:
            print(f"{prefix} ⚠️ Error deleting log for {GCCode}: {e}")
            continue

        time.sleep(1)
        
    return success_count


def CheckLanguage(page):
    try:
        page.wait_for_selector("text='Back to My Lists'", timeout=500)
        return "EN"
    except:
        return "CZ"


def CheckForGDPR(page):
    gdpr_button = page.query_selector('//*[@id="CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"]')
    if gdpr_button is not None:
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
    print("\nLogin successful")

def ExtractCachesFromUser(page, TargetDate):
    gc_codes = []
    try:
        Year, Month, Day = TargetDate.split("-")
    except:
        print("Invalid date format.")
        return []

    possible_dates = [
        f"{Day}.{Month}.{Year}",
        f"{Day}. {Month}. {Year}",
        f"{int(Day)}.{int(Month)}.{Year}",
        f"{int(Day)}. {int(Month)}. {Year}",
        f"{Month}/{Day}/{Year}",
        f"{int(Month)}/{int(Day)}/{Year}",
        f"{Year}-{Month}-{Day}"
    ]
    
    print(f"Scanning logs for date: {TargetDate} ...")
    page.goto("https://www.geocaching.com/my/logs.aspx?s=1&lt=2", timeout=30000)
    page.wait_for_load_state()
    
    try:
        page.wait_for_selector("table", timeout=5000)
    except:
        print("No logs found on the page.")
        return []
        
    last_count = 0
    scroll_attempts = 0
    max_scrolls = 50
    no_new_rows_count = 0
    
    while scroll_attempts < max_scrolls:
            
        rows_data = page.evaluate('''() => {
            let rows = document.querySelectorAll("table tbody tr");
            let data = [];
            for(let r of rows) {
                let text = r.innerText.replace(/\\s+/g, ' ');
                let links = r.querySelectorAll("a");
                let gccode = null;
                for(let a of links) {
                    let href = a.getAttribute("href");
                    if(href) {
                        let match = href.match(/GC[A-Z0-9]+/i);
                        if(match) gccode = match[0].toUpperCase();
                    }
                }
                if(gccode) {
                    data.push({text: text, gccode: gccode});
                }
            }
            return data;
        }''')
        
        current_gc_codes = set()
        for r in rows_data:
            txt = r['text']
            if any(d in txt for d in possible_dates):
                current_gc_codes.add(r['gccode'])
                
        new_finds = current_gc_codes - set(gc_codes)
        for code in new_finds:
            gc_codes.append(code)
                    
        if len(rows_data) > last_count:
            # Pokud jsme už dříve něco našli, ale teď se nenačtla z tohoto data žádná další, jsme už za ním!
            if len(gc_codes) > 0 and len(new_finds) == 0:
                print("Passed the target date. Stopping scan.")
                break
                
            no_new_rows_count = 0
            last_count = len(rows_data)
            
            # Robustnější scrollování pro React: kombinace klávesy End a najetí na poslední prvek
            page.keyboard.press("End")
            page.evaluate('''() => {
                let rows = document.querySelectorAll("table tbody tr");
                if(rows.length > 0) {
                    rows[rows.length - 1].scrollIntoView();
                }
            }''')
            time.sleep(2.5) # Počkat na načtení infinite scrollu
            scroll_attempts += 1
        else:
            no_new_rows_count += 1
            if no_new_rows_count >= 3:
                break
            page.keyboard.press("PageDown")
            time.sleep(2)
        
    return list(set(gc_codes))

def extract_gc_codes_from_file(file_path, verbose=True):
    gc_codes = []
    file_name = os.path.basename(file_path)
    if verbose:
        print(f"Reading content of file: {file_name}")
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
        if file_name.endswith('.loc'):
            found_codes = re.findall(r'GC\w+', content)
        elif file_name.endswith('.gpx'):
            found_codes = re.findall(r'<name>(GC\w+)</name>', content)
        else:
            found_codes = []
        gc_codes.extend(found_codes)
    return gc_codes

def extract_gc_codes_from_folder(folder_path):
    gc_codes = []
    print(f"Scanning files in folder: {folder_path}")
    for file_name in os.listdir(folder_path):
        if file_name.endswith('.loc') or file_name.endswith('.gpx'):
            file_path = os.path.join(folder_path, file_name)
            gc_codes.extend(extract_gc_codes_from_file(file_path, verbose=False))

    return gc_codes


def readConfig():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file_path = os.path.join(script_dir, "InputData.json")

    with open(input_file_path, encoding="utf-8") as json_file:
        data = json.load(json_file)
        
        accounts = {}
        for key, value in data.items():
            if key.startswith("Username_"):
                idx = key.split("_")[1]
                if f"Password_{idx}" in data:
                    accounts[idx] = {
                        "Username": value,
                        "Password": data[f"Password_{idx}"]
                    }
                    
        # Záchrana pro starší konfigurační soubory
        if not accounts and "Username" in data and "Password" in data:
            accounts["1"] = {"Username": data["Username"], "Password": data["Password"]}
            
        user_1_default = data.get("User_1_default", False)

        # Read custom folder path for files
        FolderPath = data.get("FolderPath", "").strip()
        if not FolderPath:
            FolderPath = script_dir

        templates = []
        for key, value in data.items():
            if key.startswith("LogTemplate_"):
                templates.append(value)

        ShowScreen = data["ShowScreen"]

    return accounts, user_1_default, FolderPath, templates, ShowScreen



if __name__ == "__main__":
    main()
