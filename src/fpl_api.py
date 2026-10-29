"""Thin client for the official, keyless FPL API."""

import requests

BASE_URL = "https://fantasy.premierleague.com/api"
TIMEOUT = 60  # seconds; the API can take ~20s per call under load


def get_bootstrap_static() -> dict:
    resp = requests.get(f"{BASE_URL}/bootstrap-static/", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_entry_history(entry_id: int) -> dict:
    resp = requests.get(f"{BASE_URL}/entry/{entry_id}/history/", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_fixtures() -> list:
    resp = requests.get(f"{BASE_URL}/fixtures/", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_event_live(gw: int) -> dict:
    resp = requests.get(f"{BASE_URL}/event/{gw}/live/", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_entry_picks(entry_id: int, gw: int) -> dict:
    """The team an entry fielded in a given gameweek. Public only after that gw's deadline
    passes; raises for an unset/future gw."""
    resp = requests.get(f"{BASE_URL}/entry/{entry_id}/event/{gw}/picks/", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_element_summary(element_id: int) -> dict:
    """Per-player detail: this season's game-by-game history (minutes, xG, xA, points) plus
    upcoming fixtures. The source for in-season form/underlying-stat trends."""
    resp = requests.get(f"{BASE_URL}/element-summary/{element_id}/", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()
