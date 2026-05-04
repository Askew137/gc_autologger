### AutoLogger Script

---

**AutoLogger**
A Python script to automate managing geocaches on Geocaching.com. It supports logging (found, DNF, note, needs attention), adding to ignore list, editing existing logs, and deleting logs.

---

### Features

- **FOUND / DNF / NOTE / NEEDS_OWNER_ATTENTION / NEEDS_REVIEWER_ATTENTION**: Submits new logs with a customizable date and template-based or manually entered text.
- **IGNORE**: Adds geocaches to your ignore list.
- **EDIT_FOUND_LOGS**: Edits your existing logs (can change log type, date, and text).
- **DELETE_FOUND_LOGS**: Deletes your existing logs automatically.
- **Interactive Execution**: The script prompts you for the operation mode, date, and log text every time you run it.
- **Log Templates**: You can pre-define multiple log text templates in the configuration file.
- **Automatic Code Extraction**: Automatically extracts GC codes from `.gpx` and `.loc` files in the same directory.

---

### Requirements

1. Python 3.7+ installed.
2. Install the `playwright` Python library:
   
   ```bash
   pip install playwright
   playwright install
   ```

---

### How to Use

#### 1. Prepare Configuration

Edit the `InputData.json` file to include your credentials and settings:

```json
{
    "Username": "YourUsername",
    "Password": "YourPassword",
    "GCCodes": "GC1234,GC2345,GC3456",
    "LogTemplate_1": "Thanks for the cache!",
    "LogTemplate_2": "TFTC! Found it easily.",
    "ShowScreen": false
}
```

- **Username / Password**: Your Geocaching.com login credentials.
- **GCCodes**: Comma-separated list of GC codes. Leave empty `""` if you only use file extraction.
- **LogTemplate_X**: You can define multiple log templates (e.g., `LogTemplate_1`, `LogTemplate_2`, `LogTemplate_3`). The script will offer these as choices during execution.
- **ShowScreen**: Set to `true` to watch the browser window during execution, or `false` to run it invisibly (headless).

Instead of pasting "GCCodes" manually, you can place any file containing GC codes (`.gpx`, `.loc`) in the same folder and the script will automatically parse and load them.

#### 2. Run the Script

Run the script in your terminal:
```bash
python AutoLogger.py
```
(Or use the "play" button in VSCode).

#### 3. Interactive Prompts

When you run the script, it will interactively ask you:
1. **Mode**: Choose the operation you want to perform (e.g., `FOUND`, `IGNORE`, `EDIT_FOUND_LOGS`, `DELETE_FOUND_LOGS`).
2. **Date**: Type a date in `YYYY-MM-DD` format, or press Enter to use today's date. (Skipped for IGNORE and DELETE operations).
3. **Log Text**: Choose one of the templates from your JSON file, or select `[New log]` to type a completely new log text directly in the terminal.

---

### Troubleshooting

- **Check Browser Action**: If something isn't working, set `"ShowScreen": true` in `InputData.json` to observe the browser actions in real-time.
- **Valid Codes**: Make sure your `.gpx`/`.loc` files are valid or your `GCCodes` JSON string is properly comma-separated.
