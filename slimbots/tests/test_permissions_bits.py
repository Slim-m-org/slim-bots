import json
import os
import re
from pathlib import Path

import pytest

from slimbots.permissions import Permissions
from slimbots.ui import UiEntry

FIXTURE = Path(__file__).parent / "fixtures" / "server_permissions.json"
UPSTREAM_ENV = "SLIMM_PERMISSIONS_RS"
SERVER_BIT = re.compile(r"pub const ([A-Z_]+): Self = Self\(1 << (\d+)\);")


def _named_bits():
    return {k: v for k, v in vars(Permissions).items() if k.isupper() and k != "NONE"}


def test_every_server_permission_bit_is_named_with_the_servers_value():
    shifts = json.loads(FIXTURE.read_text())["shifts"]
    assert _named_bits() == {name: 1 << shift for name, shift in shifts.items()}


def test_move_members_is_a_usable_requirement():
    assert Permissions.MOVE_MEMBERS == 1 << 19
    assert Permissions.names(1 << 19) == ["MOVE_MEMBERS"]
    assert UiEntry("x", "X", permission=Permissions.MOVE_MEMBERS).to_wire()["permission"] == 1 << 19


def test_the_vendored_bit_list_is_still_slim_ms():
    upstream = os.environ.get(UPSTREAM_ENV)
    if not upstream:
        pytest.skip(f"set {UPSTREAM_ENV} to slim-m's crates/slimm-server/src/permissions.rs to check for drift")
    server = {name: int(shift) for name, shift in SERVER_BIT.findall(Path(upstream).read_text())}
    assert server == json.loads(FIXTURE.read_text())["shifts"]


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
