### README: AutoLogger Script

---

**AutoLogger**
A Python script to automate logging geocaches and adding geocaches to an ignore list on Geocaching.com.

---

### Features

- **LOG**: Logs geocache finds with customizable log text and date. Works for FI, DNF, NOTE & NA logs.
- **IGNORE**: Adds geocaches to your ignore list.

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

#### Prepare Configuration:

Edit the `InputData.json` file to include:

```json
{
    "Username": "YourUsername",
    "Password": "YourPassword",
    "GCCodes": "GC1234,GC2345,GC3456",
    "LogText": "Your log message",
    "Date": "YYYY-MM-DD",
    "ShowScreen": false,
    "Mode": "LOG",
    "DoScreenshots": false
}
```

Replace the placeholder values with your own details.
Instead of pasting "GCCodes", you can put any file containing GC codes (.gpx, .loc, etc.)in the same folder and the script will automatically extract them.

#### Modes:

- Set "Mode": "FOUND" or "LOG": Found it
- Set "Mode": "DNF": Didn't find it
- Set "Mode": "NOTE": Write note
- Set "Mode": "NEEDS_OWNER_ATTENTION": Needs owner attention
- Set "Mode": "NEEDS_REVIEWER_ATTENTION": Needs reviewer attention
- Set "Mode": "IGNORE" to add geocaches to the ignore list.

#### Run the Script

- In VSCode you'll find the "play" button at top right corner of the editor window.

---

### Debugging

- In `InputData.json` enable `"ShowScreen": true` to see what went wrong in the real time. You can also enable there `"DoScreenshots": true` to save screenshots for troubleshooting.

---

### Notes

- Ensure valid credentials are provided in `InputData.json`.
- Make sure `GCCodes` contains comma-separated geocache codes or the brackets are left empty.
