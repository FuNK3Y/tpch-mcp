import gzip
import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    p = FIX / name
    if p.suffix == ".gz":
        return gzip.decompress(p.read_bytes()).decode("utf-8")
    return p.read_text(encoding="utf-8")


def load_json(name: str):
    return json.loads(load(name))


@pytest.fixture
def fx():
    return load


@pytest.fixture
def fxj():
    return load_json
