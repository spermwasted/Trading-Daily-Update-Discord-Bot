"""Imports bot.py once (without starting it) from a throw-away working directory so the
bot's JSON state files never land in the repo."""
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def bot_mod(tmp_path_factory):
    work = tmp_path_factory.mktemp("botstate")
    old = os.getcwd()
    os.chdir(work)
    try:
        spec = importlib.util.spec_from_file_location("bot_under_test", ROOT / "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bot_under_test"] = mod
        spec.loader.exec_module(mod)
        yield mod
    finally:
        os.chdir(old)
