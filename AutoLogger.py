from playwright.sync_api import sync_playwright
import datetime
import time
import os
import json
import re

def main():
    print("AutoLogger started")
    Username, Password, GCCodes, LogText, DoScreenshots, Date, Mode, ShowScreen = readConfig()

    if Mode == "NEEDS_REVIEWER_ATTENTION":
        print("\n\033[93m⚠️ VAROVÁNÍ: Vybrali jste hromadné odesílání logu 'Vyžaduje pozornost reviewera'.\033[0m")
        print("\033[93mToto upozorní reviewery na VŠECHNY zadané keše.\033[0m")
        while True:
            response = input("Opravdu chcete pokračovat? [y/N]: ").strip().lower()
            if response == 'y':
                break
            elif response in ['n', '']:
                print("Operace byla zrušena uživatelem.")
                return
            else:
                print("Prosím zadejte 'y' pro ano nebo 'n' pro ne.")

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

        print(f"Celkem keší k zpracování: {len(GCCodes)}")

        if Mode in ["LOG", "FOUND", "DNF", "NOTE", "NEEDS_OWNER_ATTENTION", "NEEDS_REVIEWER_ATTENTION"]:
            LogCaches(page, GCCodes, LogText, DoScreenshots, Date, Language, Mode)
        elif Mode == "IGNORE":
            PutToIgnoreList(page, GCCodes, LogText, DoScreenshots, Date, Language)


def PutToIgnoreList(page, GCCodes, LogText, DoScreenshots, Date, Language):
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
            print(f"❌ Nepodařilo se načíst stránku pro {GCCode}")
            continue
        page.wait_for_load_state()
        CheckForGDPR(page)
        page.wait_for_load_state()

        Element = "#ctl00_ContentBody_GeoNav_uxIgnoreBtn > a"
        button = page.locator(Element)
        button.click()
        page.wait_for_load_state()
        if DoScreenshots:
            page.screenshot(path='screenshotIgnore.png')

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


def LogCaches(page, GCCodes, LogText, DoScreenshots, Date, Language, Mode="LOG"):
    for GCCode in GCCodes:
        # Pokus o načtení stránky s retry
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            try:
                response = page.goto(f"https://www.geocaching.com/live/geocache/{GCCode}/log", timeout=10000)
                if response is not None and response.status == 200:
                    success = True
                    break
                else:
                    print(f"⚠️ Pokus {attempt + 1}: stránka {GCCode} nedostupná (status {response.status if response else 'none'})")
            except Exception as e:
                print(f"⚠️ Pokus {attempt + 1} selhal při načítání {GCCode}: {e}")
            time.sleep(2)

        if not success:
            print(f"❌ Nepodařilo se načíst stránku pro {GCCode} ani po {max_retries} pokusech")
            continue

        page.wait_for_load_state()
        CheckForGDPR(page)
        page.wait_for_load_state()

        # Otevřít výběr typu logu
        try:
            log_type_dropdown = page.locator('//label[contains(., "Typ logu")]/div/div/div[2]')
            log_type_dropdown.click()
            time.sleep(0.5)
        except Exception as e:
            print(f"❌ Nepodařilo se kliknout na dropdown pro {GCCode}: {e}")
            continue

        # Čekání na tlačítko vybraného logu
        log_type_texts = {
            "LOG": {"EN": "Found it", "CZ": "Nalezeno"},
            "FOUND": {"EN": "Found it", "CZ": "Nalezeno"},
            "DNF": {"EN": "Didn't find it", "CZ": "Nenalezeno"},
            "NOTE": {"EN": "Write note", "CZ": "Poznámka"},
            "NEEDS_OWNER_ATTENTION": {"EN": "Needs owner attention", "CZ": "Vyžadována pozornost vlastníka keše"},
            "NEEDS_REVIEWER_ATTENTION": {"EN": "Needs reviewer attention", "CZ": "Vyžaduje pozornost reviewera"}
        }
        target_text = log_type_texts.get(Mode, log_type_texts["LOG"])[Language]

        max_wait = 3
        for i in range(max_wait):
            try:
                if page.locator(f'text="{target_text}"').is_visible():
                    break
            except:
                pass
            time.sleep(1)
        else:
            print(f"❌ Tlačítko typu logu ({target_text}) se neobjevilo ani po {max_wait}s – přeskočeno {GCCode}")
            continue

        # Název keše
        try:
            CacheName = page.locator(
                '#__next > div > div.page-container.flex.flex-col.flex-grow.items-center > main > div > div.content-container > div > section > h2 > a'
            ).inner_text()
        except:
            CacheName = "(nezjištěno)"

        # Kliknutí na typ logu
        try:
            page.locator(f'text="{target_text}"').click()
        except:
            print(f"⚠️ Nelze kliknout na typ logu ({target_text}) pro {GCCode} – {CacheName}")
            continue

        page.wait_for_load_state()
        time.sleep(0.5)

        if DoScreenshots:
            page.screenshot(path=f"screenshot_{GCCode}_logform.png")

        # Vyplnit text logu
        try:
            text_field = page.locator('//*[@id="gc-md-editor_md"]')
            text_field.fill(LogText)
        except:
            print(f"⚠️ Nepodařilo se vyplnit text logu pro {GCCode}")
            continue

        if DoScreenshots:
            page.screenshot(path=f"screenshot_{GCCode}_logtext.png")

        # Nastavit datum
        try:
            Year, Month, Day = Date.split("-")
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
            print(f"⚠️ Chyba při nastavování data pro {GCCode}: {e}")
            continue

        if DoScreenshots:
            page.screenshot(path=f"screenshot_{GCCode}_date.png")

        # Odeslat log
        try:
            submit_selector = (
                "#__next > div > div.flex.flex-col.flex-grow.items-center.page-container > main > div > "
                "div.content-container > div > form > div.mt-5.mb-6.mx-0.flex.flex-col-reverse.gap-3."
                "md\\:flex-row.md\\:justify-end > div.post-button-container.flex.items-center."
                "justify-center.md\\:flex-row > button"
            )
            submit_button = page.locator(submit_selector)
            submit_button.click()
        except:
            print(f"⚠️ Chyba při odesílání logu pro {GCCode}")
            continue

        page.wait_for_load_state()
        if DoScreenshots:
            page.screenshot(path=f"screenshot_{GCCode}_submit.png")

        print(f"✅ Zalogováno {GCCode} – {CacheName}")
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
    print(f"Procházím soubory ve složce: {folder_path}")
    
    for file_name in os.listdir(folder_path):
        print(f"Kontroluji soubor: {file_name}")
        file_path = os.path.join(folder_path, file_name)

        if file_name.endswith('.loc'):
            print(f"Načítám obsah .loc souboru: {file_name}")
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
                found_codes = re.findall(r'GC\w+', content)
                print(f"Nalezené GC kódy v {file_name}: {found_codes}")
                gc_codes.extend(found_codes)

        elif file_name.endswith('.gpx'):
            print(f"Načítám obsah .gpx souboru: {file_name}")
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
                found_codes = re.findall(r'<name>(GC\\w+)</name>', content)
                print(f"Nalezené GC kódy v {file_name}: {found_codes}")
                gc_codes.extend(found_codes)

    return gc_codes


def readConfig():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file_path = os.path.join(script_dir, "InputData.json")

    with open(input_file_path, encoding="utf-8") as json_file:
        data = json.load(json_file)
        Username = data["Username"]
        Password = data["Password"]

        # Nejprve načteme kódy ze souborů
        GCCodes = extract_gc_codes_from_folder(script_dir)
        print(f"Načtené GC kódy ze souborů: {GCCodes}")

        # Pak přičteme případné ručně zadané kódy z JSON
        extra_codes_raw = data["GCCodes"].strip()
        if extra_codes_raw:
            extra_codes = [code.strip() for code in extra_codes_raw.split(",") if code.strip()]
            GCCodes += extra_codes
            print(f"Přidané GC kódy z InputData.json: {extra_codes}")

        # Odstraníme případné duplicity
        GCCodes = list(set(GCCodes))
        print(f"Konečný seznam GC kódů: {GCCodes}")

        LogText = data["LogText"]
        DoScreenshots = data["DoScreenshots"]
        Date = data["Date"]
        Mode = data["Mode"]
        ShowScreen = data["ShowScreen"]

    return Username, Password, GCCodes, LogText, DoScreenshots, Date, Mode, ShowScreen



if __name__ == "__main__":
    main()