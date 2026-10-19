"""Pure-logic tests for countries, form layouts, lists, theme and translations (no database needed)."""

import json
import re
from datetime import date
from pathlib import Path

from ledgerline import countries as c
from ledgerline import customization as cz

LOCALES = Path(__file__).resolve().parent.parent / "ledgerline" / "locales"


def test_phone_split_and_join_round_trip():
    assert c.split_phone("+92 3001234567") == ("PK", "3001234567")
    assert c.split_phone("0092 300 1234567") == ("PK", "3001234567")
    assert c.split_phone("0300 1234567", "PK") == ("PK", "3001234567")
    assert c.split_phone("+1 242 5551234")[0] == "BS"       # longest dialling code wins
    assert c.split_phone("+7 912 3456789")[0] == "RU"       # shared code: preferred country
    assert c.join_phone("PK", "0300-1234567") == "+92 3001234567"
    assert c.join_phone("PK", "+44 7911 123456") == "+44 7911123456"   # a pasted international number wins
    assert c.join_phone("PK", "") == ""
    assert c.phone_problem("+92 12") and not c.phone_problem("+92 3001234567")


def test_every_country_has_unique_iso_and_a_dial_code():
    assert len(c.ISO_CODES) == len(set(c.ISO_CODES)) >= 230
    assert all(x.dial.isdigit() for x in c.COUNTRIES)
    assert c.get("PK").flag and c.get("nonsense").iso == "PK"


def test_locked_fields_cannot_be_hidden_and_custom_fields_survive():
    custom = cz.new_custom_field("Birthday", "date")
    fields = cz.resolve_fields("client", [{"key": "name", "visible": False, "required": False}, custom,
                                           {"key": "phone", "visible": False, "custom_label": "Mobile"}])
    by_key = {f["key"]: f for f in fields}
    assert fields[0]["key"] == "name" and by_key["name"]["visible"] and by_key["name"]["required"]
    assert by_key["phone"]["visible"] is False and by_key["phone"]["custom_label"] == "Mobile"
    assert custom["key"] in by_key and len(fields) == 6


def test_garbage_layout_is_ignored():
    fields = cz.resolve_fields("invoice", [{"key": "nope"}, "x", {"key": "cf_a", "type": "bad", "label": "x"}])
    assert [f["key"] for f in fields] == [f["key"] for f in cz.BUILTIN_FIELDS["invoice"]]


def test_value_encoding_round_trip():
    d, n, b = (cz.new_custom_field("x", k) for k in ("date", "number", "checkbox"))
    assert cz.decode_value(d, cz.encode_value(d, date(2026, 1, 2))) == date(2026, 1, 2)
    assert cz.decode_value(n, cz.encode_value(n, 12.5)) == 12.5
    assert cz.decode_value(b, cz.encode_value(b, True)) is True and cz.encode_value(b, False) == ""


def test_lists_are_cleaned_and_never_empty():
    assert cz.sanitize_list([" a ", "A", "", "b"]) == ["a", "b"]
    assert cz.sanitize_list([], ["General"]) == ["General"]
    assert "Cash" in cz.get_lists(None)["payment_methods"]


def test_theme_is_sanitised_and_css_is_safe():
    theme = cz.sanitize_theme({"accent": "red", "radius": 999, "custom_css": "a{}</style><script>x</script>"})
    assert theme["accent"] == cz.DEFAULT_THEME["accent"] and theme["radius"] == 28
    assert "<" not in theme["custom_css"]
    css = cz.theme_css({"accent": "#0F9D74"}, rtl=True)
    assert "#0F9D74" in css and "direction: rtl" in css
    assert cz.text_on("#FFFFFF") != cz.text_on("#000000")


def test_locale_files_are_complete_and_placeholders_match():
    keys = json.loads((LOCALES / "_keys.json").read_text(encoding="utf-8"))
    ph = lambda s: sorted(re.findall(r"\{[^}]*\}", s))
    for path in LOCALES.glob("*.json"):
        if path.name.startswith("_"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "name" in data["__meta__"]
        for key, text in data.items():
            if key != "__meta__":
                assert key in keys, f"{path.name}: unknown sentence {key!r}"
                assert ph(key) == ph(text), f"{path.name}: placeholders differ in {key!r}"
        assert len(data) - 1 >= 0.6 * len(keys)
