"""Tests for the contents helpers and the per-entry store."""

from custom_components.overseas_express.contents import (
    CONTENTS_MAX_LEN,
    CONTENTS_STORE_MAX_CODES,
    ContentsStore,
    clean_contents,
    join_contents,
    normalize_code,
)


def test_clean_contents_trims_collapses_and_caps():
    assert clean_contents("  Running \n shoes ") == "Running shoes"
    assert clean_contents("x" * 500) == "x" * CONTENTS_MAX_LEN
    assert clean_contents("   ") is None
    assert clean_contents(None) is None
    assert clean_contents(5) is None
    assert clean_contents("N/A", frozenset({"n/a"})) is None


def test_join_contents_drops_empties_and_repeats():
    assert join_contents(["Shoes", " ", "Socks", "Shoes", None]) == "Shoes; Socks"
    assert join_contents([None, ""]) is None


def test_normalize_code():
    assert normalize_code(" abc1 ") == "ABC1"


async def test_store_set_replace_clear_and_unchanged(hass):
    store = ContentsStore(hass, "e1")
    assert store.async_set("abc", "Shoes") is True
    assert store.get("ABC") == "Shoes"
    assert store.async_set("ABC", " Shoes ") is False
    assert store.async_set("ABC", "Socks") is True
    assert store.async_set("ABC", None) is True
    assert store.get("ABC") is None
    assert store.async_set("ABC", "") is False


async def test_store_cap_drops_the_oldest(hass):
    store = ContentsStore(hass, "e1")
    for index in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"CODE{index}", "Shoes")
    assert store.get("CODE0") is None
    assert store.get("CODE1") == "Shoes"
    assert store.get(f"CODE{CONTENTS_STORE_MAX_CODES}") == "Shoes"


async def test_store_loads_saved_values_and_tolerates_junk(hass, hass_storage):
    hass_storage["overseas_express.contents.e1"] = {
        "version": 1,
        "key": "overseas_express.contents.e1",
        "data": {"by_code": {"abc": " Shoes ", "def": 5, "ghi": ""}},
    }
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.get("ABC") == "Shoes"
    assert store.get("DEF") is None
    assert store.get("GHI") is None


async def test_store_without_a_file_or_with_a_bad_shape_reads_empty(hass, hass_storage):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.get("ABC") is None
    hass_storage["overseas_express.contents.e2"] = {
        "version": 1,
        "key": "overseas_express.contents.e2",
        "data": {"by_code": ["x"]},
    }
    other = ContentsStore(hass, "e2")
    await other.async_load()
    assert other.get("ABC") is None


async def test_store_remove_deletes_the_file(hass, hass_storage):
    store = ContentsStore(hass, "e1")
    store.async_set("ABC", "Shoes")
    await store.async_remove()
    assert store.get("ABC") is None
