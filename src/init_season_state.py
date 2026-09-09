"""One-time season initialization: seed squad_state.json from the current GW1-3 optimizer output.
Run once, before judgment_pass.py (which needs squad_state to exist, to build the transfer
watchlist) and before step 4 (live weekly runs) starts. Refuses to run twice - see
memory.init_squad_state. Decision logging happens in judgment_pass.py itself now, not here - it's
the thing that actually produces the decision packet, so it makes sense for it to also log it.
"""

import glob
import json
import os

import memory

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")


def latest_bootstrap_path() -> str:
    return sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))[-1]


def main():
    with open(latest_bootstrap_path()) as f:
        bootstrap = json.load(f)
    with open(os.path.join(PROCESSED_DIR, "squad_gw1-3.json")) as f:
        squad_result = json.load(f)

    entry_id = os.environ.get("FPL_ENTRY_ID")
    state = memory.init_squad_state(bootstrap, squad_result, as_of_gw=1, entry_id=int(entry_id) if entry_id else None)
    print(f"Initialized squad_state.json: bank=£{state['bank']/10:.1f}m, "
          f"free_transfers={state['free_transfers']}, chips available={len(state['chips'])}")


if __name__ == "__main__":
    main()
