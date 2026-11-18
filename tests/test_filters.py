import pytest

from tpch_mcp import sfh
from tpch_mcp.parse.filters import FilterError, FilterSet, quantity


@pytest.fixture
def mesh(fxj):
    return FilterSet(fxj("reload_mesh_expert.json.gz")["sFilter"])


@pytest.fixture
def ssd(fxj):
    return FilterSet(fxj("reload_ssd_filtered.json.gz")["sFilter"])


def test_quantity():
    assert quantity("2 TB") == (2e12, "b")
    assert quantity("512 GB") == (512e9, "b")
    assert quantity("575 W") == (575, "w")
    assert quantity("2 ×") == (2, "")
    assert quantity("107.5 mm")[0] == pytest.approx(0.1075)


def test_compact(mesh):
    c = mesh.compact()
    names = [f["name"] for f in c["filters"]]
    assert "Wi-Fi standard" in names and "weight" not in names  # expert hidden by default
    assert c["expert_filters_hidden"] == 20
    assert len(mesh.compact(include_expert=True)["filters"]) > len(c["filters"])


def test_resolve_or_bool_min_and(mesh):
    props, applied = mesh.resolve({
        "Wi-Fi standard": ["Wi-Fi 7", "Wi-Fi 6E"],
        "WLAN": True,
        "LAN 2.5GBase-T ports": {"min": 2},
        "WLAN encryption": ["WPA3"],
    })
    kinds = {(k, f) for k, f, _ in props}
    assert ("or", "ngff2864x27") in kinds and ("bool", "ngff870x27") in kinds
    assert ("min", "ngff1784x27") in kinds and ("and", "ngff853x27") in kinds
    assert sum(1 for k, f, _ in props if f == "ngff2864x27") == 2
    s = sfh.encode(sfh.Selection(props=props), mesh.order)
    assert s.startswith("oi~") and "::" in s


def test_resolve_range_and_max_snap(mesh):
    props, applied = mesh.resolve({"depth": {"max": "110 mm"}, "weight": {"max": "500 g"}})
    by_filter = {f: (k, v) for k, f, v in props}
    assert by_filter["ngff1109x27"][0] == "range_to"
    assert by_filter["ngff1113x27"][0] == "max"
    assert any("<=" in a for a in applied)


def test_capacity_units(ssd):
    props, applied = ssd.resolve({"total capacity": {"min": "1 TB", "max": "2 TB"}})
    assert [k for k, _, _ in props] == ["range_from", "range_to"]
    assert any("1 TB" in a or "1000 GB" in a for a in applied)
    with pytest.raises(FilterError, match="several units"):
        ssd.resolve({"total capacity": {"min": 1}})


def test_errors_are_helpful(mesh):
    with pytest.raises(FilterError, match="Available filters"):
        mesh.resolve({"colour of the moon": ["blue"]})
    with pytest.raises(FilterError, match="Options include"):
        mesh.resolve({"Wi-Fi standard": ["Wi-Fi 9"]})


def test_brands(mesh):
    ids, labels = mesh.brand_ids(["tp-link", "asus"])
    assert ids == [1534, 24] and labels == ["TP-Link", "Asus"]
    with pytest.raises(FilterError):
        mesh.brand_ids(["Nokia3310"])
