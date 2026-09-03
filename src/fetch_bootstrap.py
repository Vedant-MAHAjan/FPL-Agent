"""Pull bootstrap-static (and entry history, if FPL_ENTRY_ID is set) and save raw JSON."""

import json
import os
from datetime import date

from fpl_api import get_bootstrap_static, get_entry_history

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    today = date.today().isoformat()

    data = get_bootstrap_static()
    out_path = os.path.join(RAW_DIR, f"bootstrap_static_{today}.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved bootstrap-static -> {out_path}")

    entry_id = os.environ.get("FPL_ENTRY_ID")
    if entry_id:
        history = get_entry_history(int(entry_id))
        hist_path = os.path.join(RAW_DIR, f"entry_{entry_id}_history_{today}.json")
        with open(hist_path, "w") as f:
            json.dump(history, f, indent=2)
        print(f"Saved entry history -> {hist_path}")
    else:
        print("FPL_ENTRY_ID not set - skipping entry history pull.")


if __name__ == "__main__":
    main()
