# 🧭 GC Autologger

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![GUI-CustomTkinter](https://img.shields.io/badge/GUI-CustomTkinter-1db954.svg)](https://customtkinter.tomschimansky.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**GC Autologger** is a modern, high-performance desktop application for batch geocache logging and bulk coordinate uploading on [Geocaching.com](https://www.geocaching.com).

Unlike traditional browser automation tools that rely on slow browser rendering (Playwright/Selenium), GC Autologger communicates **directly with Geocaching.com's internal REST and tRPC batch APIs**, replicating the proven architecture of **c:geo**. 

---

## ✨ Features

### 📍 Bulk Coordinate Upload (from GPX / LOC)
* **Direct Coordinate API**: Sets corrected coordinates via `/seek/geocache.usercoordinate` using Base16/Base31 mathematical conversion (`gc_code_to_cache_id`), eliminating listing download overhead.
* **Dual Upload Modes**:
  1. **Complete Overwrite**: Updates coordinates for all caches in your GPX/LOC file.
  2. **Only Unmodified**: Automatically checks Geocaching.com and only updates caches that don't already have corrected coordinates.
* **Smart GPX Parser**: Reads standard waypoints (`<wpt>`), c:geo exports, GSAK tags (`LatBeforeCorrect`), GeoGet, and child final waypoints (`FN...`).

### 📝 Bulk Logging Engine
* **High-Speed tRPC Batching**: Submits logs via Geocaching.com's modern batch endpoint `/api/live/v1/trpc/web.logs.createGeocacheLog`.
* **Supported Log Types**:
  * *Found it*
  * *Didn't find it (DNF)*
  * *Write note*
  * *Needs maintenance*
  * *Needs archive* (with confirmation safeguard)
* **Log Templates**: Manage and select predefined log templates with customizable timestamps and dates.

### 🛡️ Anti-Detection & Safety Engine (Designed for 1000+ Caches)
* **Realistic Pacing & Jitter**: Configurable delay (0.6s – 1.2s) with randomized jitter prevents mechanical request patterns.
* **Batch Breathers**: Automatically pauses every 50 caches (3s – 6s) to avoid triggering Cloudflare or rate-limiters.
* **Genuine Browser Headers**: Sends authentic browser headers (`Sec-Ch-Ua`, `Referer` per cache, `Origin`, `X-Requested-With`).
* **Session Persistence**: Encrypted session cookies are cached in `session_cookies.json`, avoiding repeated logins.
* **Auto-Recovery**: Automatically handles `401 Unauthorized` / `403 Forbidden` with instant re-authentication, and handles `429 Too Many Requests` with exponential backoff.

### 👥 Copy User & Ignore List
* **Copy User**: Scans another user's logged caches for a given date and logs the same caches to your account.
* **Ignore List**: Bulk-add hundreds of caches to your ignore bookmark list in seconds.

### 💻 Modern GUI + Terminal CLI
* **CustomTkinter Dark Theme**: Sleek, responsive interface inspired by Spotify and Discord.
* **Interactive Config & Accounts Editor**: Add, edit, remove accounts and adjust safety sliders directly from the GUI.
* **Live Progress & Console**: Displays percentage progress, real-time ETA countdown, and color-coded logs.
* **CLI Mode**: Full terminal interface available via `python3 main.py --cli`.

---

## 🚀 Getting Started

### Prerequisites
* Python 3.9 or higher

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Askew137/gc_autologger.git
   cd gc_autologger
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Launch the application:**
   ```bash
   # Launch GUI (Modern CustomTkinter)
   python3 main.py

   # Or launch in interactive terminal mode
   python3 main.py --cli
   ```

---

## ⚙️ Configuration

You can configure your accounts and settings directly in the **⚙️ Config & Accounts** tab in the GUI, or by creating a `config.json` file (see `config.example.json`):

```json
{
  "accounts": [
    {
      "id": "1",
      "username": "YourUsername",
      "password": "YourPassword",
      "is_default": true
    }
  ],
  "folder_path": "/path/to/your/gpx_files",
  "log_templates": [
    "Thanks for the cache! TFTC",
    "Found during our weekend caching trip. Greetings from Czech Republic!"
  ],
  "safety": {
    "min_delay_seconds": 0.7,
    "max_delay_seconds": 1.2,
    "breather_interval": 50,
    "breather_duration_seconds": 4.0,
    "auto_relogin": true
  },
  "gui": {
    "theme": "dark",
    "color_theme": "blue"
  }
}
```

> **Note**: `config.json` and `session_cookies.json` are automatically ignored by `.gitignore` to protect your credentials.

---

## 📁 Project Architecture

```
gc_autologger/
├── main.py                     # Primary entry point (launches GUI or CLI)
├── cli.py                      # Interactive terminal interface
├── requirements.txt            # Python dependencies
├── config.example.json         # Example configuration
├── core/
│   ├── client.py               # Direct Geocaching HTTP client (REST, tRPC, tokens)
│   ├── converter.py            # Base16/Base31 GC code <-> cacheId converter (from c:geo)
│   ├── gpx_parser.py           # Universal GPX & LOC parser with namespace stripping
│   ├── config.py               # JSON settings manager with legacy migration
│   └── safety.py               # Pacing engine, jitter, and rate-limit backoff
├── operations/
│   ├── coordinate_uploader.py  # Bulk coordinate upload manager (2 modes)
│   ├── cache_logger.py         # Bulk logging & ignore manager
│   └── user_copy.py            # User log scraper and copier
└── gui/
    ├── app.py                  # CustomTkinter GUI main window
    └── theme.py                # Visual styling and color palette
```

---

## ⚖️ Disclaimer

This tool is designed for personal use to manage your own geocaching logs and solved mystery coordinates. Please cache responsibly, respect rate limits, and adhere to the Geocaching.com Terms of Use.
