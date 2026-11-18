from tpch_mcp import sfh


def test_all_server_strings_roundtrip(fxj):
    cases = fxj("sfh_cases.json")
    assert len(cases) >= 30
    for c in cases:
        sel = sfh.decode(c["sfh"])
        assert not sel.unknown, c["sfh"]
        assert not any(k.startswith("?") for k, _, _ in sel.props), c["sfh"]
        assert sfh.encode(sel, c["order"]) == c["sfh"]


def test_encode_examples():
    sel = sfh.Selection(
        props=[("or", "ngff417x14", "v112070"), ("or", "ngff417x14", "v112167"), ("max", "ngff4549x14", "v263655")],
        manufacturers=[1534, 24], offset=48, sort="pa", max_availability=1, min_year=2023,
        price_min=343, price_max=806.5, text="samsung", expert=True)
    s = sfh.encode(sel, ["ngff417x14", "ngff4549x14"])
    assert s == ("oi~ongff417x14:sv112070::ongff417x14:sv112167::tngff4549x14:tv263655+m~24.1534+o~48+s~pa"
                 "+a~1+iy~2023+ip~34300+xp~80650+pt~c2Ftc3VuZw+e~1")
    back = sfh.decode(s)
    assert back.price_max == 806.5 and back.text == "samsung" and back.manufacturers == [24, 1534]


def test_filter_order_follows_definition():
    sel = sfh.Selection(props=[("range_from", "ngff182x51", "v22928"), ("or", "ngff178x51", "v76361"),
                               ("or", "ngff1453x51", "v56481")])
    assert sfh.encode(sel, ["ngff178x51", "ngff182x51", "ngff1453x51"]) == \
        "oi~ongff178x51:sv76361::rngff182x51:fv22928::ongff1453x51:sv56481"


def test_same_selection_detects_dropped_values():
    ok, diffs = sfh.same_selection("oi~ongff417x14:sv1::ongff417x14:sv2+m~3", "oi~ongff417x14:sv1")
    assert not ok and any("ignored" in d for d in diffs) and any("manufacturers" in d for d in diffs)
    ok, _ = sfh.same_selection("oi~ongff1:sv1+o~24+s~pa", "oi%7Eongff1%3Asv1%2Bs~pa%2Bo~24")
    assert ok
