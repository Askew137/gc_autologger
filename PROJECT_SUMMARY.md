# Project Summary: GC Autologger

> **Příkaz pro navázání konverzace přes Antigravity CLI (`agy`):**
> ```bash
> # Přímé navázání na tuto konkrétní konverzaci:
> agy --conversation 274afaaf-a377-4f13-82ef-60029bc88882
> 
> # Nebo rychlé pokračování v posledním aktivním sezení:
> agy -c
> ```

---

## 📌 Přehled projektu a cílů

Projekt **GC Autologger** byl kompletně přepracován z původního monolitického skriptu `AutoLogger.py` (který ovládal prohlížeč přes Playwright) na moderní, vysoce výkonnou a modulární aplikaci. Nová architektura replikuje síťový přístup známé aplikace **c:geo** – komunikuje přímo s interními REST a tRPC batch API Geocaching.com bez pomalého vykreslování v prohlížeči.

---

## 🚀 Klíčové funkce a komponenty

### 1. Hromadné nahrávání souřadnic (Bulk Coordinate Upload)
* **Přímé volání API:** Využívá endpoint `POST https://www.geocaching.com/seek/geocache.usercoordinate` s hlavičkou `__RequestVerificationToken`.
* **Matematická konverze GC kódu:** Implementován algoritmus z c:geo (`GCUtils.java`), který převádí GC kód (např. `GC2MEGA`) přímo na číselné `cacheId` (Base16 pro starší keše, Base31 pro novější). Odstraňuje nutnost stahovat celou stránku keše.
* **Dva režimy nahrávání:**
  * **Režim A (Kompletní přepis):** Nahraje souřadnice pro všechny keše obsažené v GPX/LOC souboru.
  * **Režim B (Pouze dosud nezměněné):** Nejprve zkontroluje stav na Geocaching.com a nahraje souřadnice jen u keší, které dosud nemají nastavené uživatelsky opravené souřadnice (`userCorrectedCoordinates`).
* **Univerzální GPX Parser:** Podporuje hlavní waypoints (`<wpt>`), c:geo exporty, GSAK tagy (`LatBeforeCorrect`), GeoGet i finální child waypoints (`FN...`, `FZ...`).
* **Crash Recovery & Checkpointing:** Při přerušení nebo chybě se průběžně ukládá stav běhu do `runs/` a GUI nabízí tlačítko **Resume upload**.

### 2. Dávkové logování (Bulk Logging Engine)
* **Vysokorychlostní tRPC Batching:** Odesílání logů přes `/api/live/v1/trpc/web.logs.createGeocacheLog`.
* **Podporované typy logů:** *Found it*, *Didn't find it (DNF)*, *Write note*, *Needs maintenance*, *Needs archive* (s potvrzovacím dialogem).
* **Šablony & datum:** Podpora šablon logů s náhledy, krokování data po dnech (`▼` / `▲`) a tlačítko `Dnes`.
* **Správa stávajících logů:** Úprava a mazání dříve vytvořených logů bez otevírání prohlížeče.

### 3. Bezpečnostní Pacing Engine (Anti-Detection)
* **Realistické zpoždění a jitter:** Nastavitelná prodleva (výchozí 0.7s – 1.2s) s náhodným rozptylem.
* **Oddechové pauzy (Batch Breathers):** Automatická pauza každých 50 keší (na 4 sekundy).
* **Autentické hlavičky prohlížeče:** Odesílá reálné `Sec-Ch-Ua`, `Referer` odpovídající dané keši, `Origin` a `X-Requested-With`.
* **Izolace relací (Per-Account Session Isolation):** Cookies jsou bezpečně uloženy pro každý účet zvlášť v `session_cookies.json`, což umožňuje okamžité přepínání účtů za <0.05s bez nutnosti opakovaného zadávání hesel.
* **Auto-Recovery:** Automatická obnova relace při chybách `401/403` a exponenciální backoff při `429 Too Many Requests`.

### 4. Další nástroje
* **Copy User:** Stahování a kopírování logů jiného geocachera za zadaný den.
* **Ignore List:** Hromadné přidávání stovek keší do seznamu ignorovaných během několika sekund.

---

## 🎨 Uživatelské rozhraní a systémová integrace

* **CustomTkinter GUI:** Moderní tmavý motiv inspirovaný Discordem a Spotify s responzivním rozložením, progress barem a živou konzolí.
* **CLI mód:** Plně interaktivní terminálový režim dostupný přes parametr `--cli`.
* **Nativní macOS ikona (Liquid Glass kompatibilní):**
  * Vytvořen 10vrstvý Apple Retina balíček `assets/icon.icns` (16×16 až 1024×1024 px) pomocí `iconutil`.
  * Přesné zaoblení rohu (squircle) podle Apple HIG (824×824 px na 1024×1024 px plátně) s plně průhlednými rohy.
  * Cocoa/AppKit integrace v `gui/app.py` pro ostré zobrazení v macOS Docku namísto výchozí rakety Pythonu.
* **Zkratka v terminálu (`gc`):**
  * V `~/.zshrc` nastaven alias: `alias gc="/Users/askew137/Git/gc_autologger/start.sh"`.
  * Spustitelné odkudkoliv z terminálu, s automatickým přepnutím do složky projektu a podporou argumentů (např. `gc --cli`).

---

## 🪟 Windows Sestavení (GitHub Actions)

* Nastaveno automatické CI workflow `.github/workflows/build_windows.yml`.
* Při vytvoření tagu (např. `v1.0.0`) nebo ručním spuštění přes *workflow_dispatch* sestaví virtuální Windows server samostatný `GC_Autologger.exe` pomocí PyInstaller.
* Vytvoří oficiální GitHub Release a přiloží hotový `GC_Autologger_Windows.zip` ke stažení bez nutnosti instalovat Python.
* **První oficiální vydání:** [Release v1.0.0](https://github.com/Askew137/gc_autologger/releases/tag/v1.0.0).

---

## 📂 Struktura projektu

```
gc_autologger/
├── main.py                     # Hlavní vstupní bod (spouští GUI nebo CLI)
├── cli.py                      # Interaktivní terminálové rozhraní
├── start.sh                    # Spouštěcí bash skript s auto-instalací venv
├── requirements.txt            # Python závislosti (CustomTkinter, requests, pyobjc...)
├── config.example.json         # Vzorová konfigurace
├── config.json                 # Lokální konfigurace uživatele (v .gitignore)
├── session_cookies.json        # Uložené aktivní relace účtů (v .gitignore)
├── PROJECT_SUMMARY.md          # Tento souhrnný dokument
├── README.md                   # Veřejná dokumentace pro GitHub
├── LICENSE                     # GNU Affero General Public License v3
├── assets/                     # Grafické podklady
│   ├── icon.icns               # Nativní macOS Retina ikona (10 rozlišení)
│   ├── icon.ico                # Vícevrstvá Windows ikona (16–256 px)
│   └── icon.png                # PNG ikona s průhledným pozadím
├── core/
│   ├── client.py               # Přímý HTTP klient pro Geocaching.com (REST, tRPC, tokeny)
│   ├── converter.py            # Base16/Base31 GC kód <-> cacheId (adaptováno z c:geo)
│   ├── gpx_parser.py           # Univerzální parser GPX/LOC s podporou child waypoints
│   ├── config.py               # Správa nastavení a migrace
│   └── safety.py               # Pacing engine, jitter, rate-limit ochrana
├── operations/
│   ├── coordinate_uploader.py  # Nahrávání opravených souřadnic (2 režimy)
│   ├── cache_logger.py         # Logování keší a správa ignorovaných
│   └── user_copy.py            # Stahování a kopírování logů uživatele
├── gui/
│   ├── app.py                  # Hlavní CustomTkinter okno aplikace
│   └── theme.py                # Barevné schéma a styly
└── .github/
    └── workflows/
        └── build_windows.yml   # Automatické sestavení Windows .exe na GitHubu
```

---

## 🛡️ Bezpečnost a licence

* **Ochrana soukromí:** Soubory `config.json`, `session_cookies.json`, `runs/` i `venv/` jsou striktně ignorovány přes `.gitignore` a nikdy se nenahrávají do gitu.
* **Licence:** Celý projekt je licencován pod **GNU Affero General Public License v3 (AGPL-3.0)**, která je plně kompatibilní s Apache License 2.0 (používanou projektem c:geo).
