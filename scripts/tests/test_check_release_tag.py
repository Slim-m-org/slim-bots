import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_release_tag as c  # noqa: E402


def make_package(root: Path, version: str) -> Path:
    (root / "slimbots").mkdir()
    (root / "slimbots" / "pyproject.toml").write_text(f'[project]\nname = "slim-m"\nversion = "{version}"\n')
    return root


def test_the_tag_for_the_package_version_passes():
    assert c.tag_problem("v0.19.0", "0.19.0") is None


@pytest.mark.parametrize("tag", ["v0.18.0", "0.19.0", "v0.19.0-rc1", "v0.19", "", "latest", "refs/tags/v0.19.0"])
def test_any_other_tag_is_refused(tag):
    assert "v0.19.0" in (c.tag_problem(tag, "0.19.0") or "")


def test_the_version_is_read_from_the_package_file(tmp_path):
    assert c.package_version(make_package(tmp_path, "1.2.3")) == "1.2.3"


def test_main_exits_nonzero_for_a_tag_that_is_not_the_version(capsys):
    assert c.main(["v0.0.1"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_exits_zero_for_the_current_version():
    assert c.main([f"v{c.package_version()}"]) == 0
