import pytest

from slimbots.permissions import Permissions
from slimbots.ui import UiEntry


def test_view_moderation_history_is_named_like_the_server_names_it():
    assert Permissions.VIEW_MODERATION_HISTORY == 1 << 18
    assert Permissions.names(1 << 18) == ["VIEW_MODERATION_HISTORY"]


def test_a_ui_entry_takes_exactly_one_known_permission_bit():
    for bits in (Permissions.KICK_MEMBERS | Permissions.BAN_MEMBERS, 0, 1 << 30, -1):
        with pytest.raises(ValueError, match="permission"):
            UiEntry("x", "X", permission=bits)


def test_every_named_permission_is_a_valid_ui_entry_permission():
    named = [v for k, v in vars(Permissions).items() if k.isupper() and k != "NONE"]
    assert 1 << 18 in named
    for bit in named:
        assert UiEntry("x", "X", permission=bit).to_wire()["permission"] == bit
    assert UiEntry("x", "X").permission is None
