"""Thin client for the official, keyless FPL API."""

import requests

BASE_URL = "https://fantasy.premierleague.com/api"


def get_bootstrap_static() -> dict:
    resp = requests.get(f"{BASE_URL}/bootstrap-static/", timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_entry_history(entry_id: int) -> dict:
    resp = requests.get(f"{BASE_URL}/entry/{entry_id}/history/", timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_fixtures() -> list:
    resp = requests.get(f"{BASE_URL}/fixtures/", timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_event_live(gw: int) -> dict:
    resp = requests.get(f"{BASE_URL}/event/{gw}/live/", timeout=15)
    resp.raise_for_status()
    return resp.json()
