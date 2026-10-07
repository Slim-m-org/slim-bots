import pytest

from slimbots.components import Button, rows, to_wire


def test_blank_label_is_refused_locally():
    with pytest.raises(ValueError, match="label"):
        Button("   ", "id")


def test_hidden_character_in_a_label_is_refused_locally():
    with pytest.raises(ValueError, match="invisible"):
        Button("Nick‍", "id")


def test_hidden_character_in_a_custom_id_is_refused_locally():
    with pytest.raises(ValueError, match="invisible"):
        Button("Nick", "id‮")


def test_the_label_goes_out_trimmed_like_the_server_trims_it():
    assert Button("  Hit  ", "hit").to_wire()["label"] == "Hit"


def test_an_empty_row_is_refused_locally():
    with pytest.raises(ValueError, match="at least one button"):
        rows([])
    with pytest.raises(ValueError, match="at least one button"):
        to_wire([{"buttons": []}])


def test_a_duplicate_custom_id_is_refused_locally():
    with pytest.raises(ValueError, match="custom_id"):
        rows([Button("a", "id"), Button("b", "id")])
    with pytest.raises(ValueError, match="custom_id"):
        rows([Button("a", "id")], [Button("b", "id")])
    with pytest.raises(ValueError, match="custom_id"):
        to_wire([[Button("a", "id")], {"buttons": [{"label": "b", "style": "secondary", "custom_id": "id"}]}])


def test_link_buttons_and_distinct_ids_still_build():
    assert len(rows([Button("a", "a"), Button("b", "b"), Button.link("docs", "https://example.com")])[0]["buttons"]) == 3
