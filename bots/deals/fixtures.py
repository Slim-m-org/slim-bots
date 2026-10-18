"""Loads the recorded API answers in fixtures/ (GamerPower, Steam appdetails and storesearch) for the tests."""

import json
import os

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def raw(name):
    with open(os.path.join(HERE, f"{name}.json"), "rb") as handle:
        return handle.read()


def load(name):
    return json.loads(raw(name))
