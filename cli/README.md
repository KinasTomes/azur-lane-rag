# Azur Lane Ship TUI

A colorful terminal UI for browsing ships from `../AzurLaneData/data/ships.json` and drop data from `../AzurLaneData/data/ship_drops.json`.

## Features

- Shows all ship names in a scrollable table on startup
- Arrow up/down to navigate ships
- Enter to open full ship information view
- Esc to go back from ship information view
- `/` to focus search bar
- Search suggestions/autocomplete via Textual suggester

## Run

```powershell
cd cli
pip install -r requirements.txt
python ship_browser.py
```

## Keys

- `Up` / `Down`: move selection
- `Enter`: open selected ship
- `Esc`: back from detail screen
- `/`: focus search bar
- `Ctrl+C`: quit app
