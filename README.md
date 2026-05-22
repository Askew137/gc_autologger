# AutoLogger_GC

A Python script to automate managing geocaches on Geocaching.com. It supports logging (found, DNF, note, needs attention), adding to ignore list, editing existing logs, and deleting logs.

## Features

- **Multi-Account Support**: Manage multiple Geocaching accounts directly within the script.

- **Flexible Execution Modes**: Supports Found it, DNF, Write note, Needs maintenance, Need archive, adds caches to ignore list, Edit/Delete existing logs, and a unique **COPY_USER** mode to sync logs with other accounts.

- **Smart Input**: Choose between uploading files (batch or individual), or manual GC code entry. Automatically parses `.gpx` and `.loc` files.

- **Integrated Config Management**: Edit your `InputData.json` settings directly from the terminal.

- **Interactive UI**: Log templates, date pinning, and clean summary statistics instead of verbose logs.

- **Browser Automation**: Uses `playwright` for reliable interaction with Geocaching.com.

## Requirements

- Python 3.7+

- Install the `playwright` library and browser binaries:
  
  Bash
  
  ```
  pip install playwright
  playwright install
  ```

## How to Use

### 1. Configuration (`InputData.json`)

Configure your accounts and preferences in the `InputData.json` file:

JSON

```
{
    "User_1_default": true,
    "Username_1": "YourUsername",
    "Password_1": "YourPassword",
    "Username_2": "AnotherUsername",
    "Password_2": "AnotherPassword",
    "FolderPath": "C:/Path/To/Your/GPX/Files",
    "LogTemplate_1": "Found it, thanks!",
    "LogTemplate_2": "TFTC.",
    "ShowScreen": false
}
```

- **Username_X / Password_X**: Add as many accounts as you need.

- **User_1_default**: Set to `true` to automatically select the first account, or `false` to choose manually at startup.

- **FolderPath**: Define a default directory for your `.gpx` or `.loc` files. If empty, the script uses the current directory.

- **LogTemplate_X**: Pre-define your logs for quick selection; add as many templates as you need.

- **ShowScreen**: Set to `true` to watch the browser in real-time, or `false` for headless mode.

### 2. Execution

Run the script:

Bash

```
python AutoLogger.py
```

## 🚀 Update v1.2.0: Changelog

**New Features:**

- **File Selection Menu:** Easily load all files at once, select specific files, or enter GC codes manually.

- **Multi-Account Support:** Select your account directly at startup.

- **Enhanced Config:** Set a deafult path in JSON and edit your settings directly within the terminal.

- **Sync Functionality:** Added "Log the same caches as [user]" to mirror logs from another account based on a specific date.

- **Efficiency:** Added "Pin" feature for date selection to accelerate bulk logging.

- **UI/UX:** Cleaner terminal output with summary statistics (logs processed vs. successfully completed); updated startup banner.

**Bug Fixes:**

- **Date Logic:** Resolved an issue preventing the logging of caches from previous years.
