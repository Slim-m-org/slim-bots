#!/usr/bin/env python3
"""The arena installer against in-memory archives and a temp install dir, no network; run directly: python3 test_installer.py."""

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import installer  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

GAMEINFO = '"GameInfo"\n{\n\tSearchPaths\n\t{\n\t\t\tGame_LowViolence\tcsgo_lv // x\n\t\t\tGame\tcsgo\n\t\t\tGame\tcore\n\t}\n}\n'


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def package(name, data, dest="game/csgo", marker="addons/x/marker.txt"):
    return installer.Package(name, f"https://example.test/{name}", hashlib.sha256(data).hexdigest(), dest, marker)


class Rig:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.fetched = []
        self.blobs = {}

    def add(self, name, data, **kwargs):
        pkg = package(name, data, **kwargs)
        self.blobs[pkg.url] = data
        return pkg

    def fetch(self, url):
        self.fetched.append(url)
        return self.blobs[url]

    def ensure(self, packages):
        return installer.ensure(self.root, packages, fetch=self.fetch)

    def path(self, *parts):
        return os.path.join(self.root, *parts)


def test_ensure_unpacks_zip_and_tar_packages_under_their_destinations_and_writes_the_pre_hook():
    rig = Rig()
    tar = rig.add("mm", tar_bytes({"addons/metamod/bin/m.so": b"so"}), marker="addons/metamod/bin/m.so")
    zipped = rig.add("css", zip_bytes({"plugins/a/a.dll": b"dll"}), dest="game/csgo/addons/counterstrikesharp", marker="plugins/a/a.dll")
    assert rig.ensure([tar, zipped]) == ["mm", "css"]
    assert open(rig.path("game/csgo/addons/metamod/bin/m.so"), "rb").read() == b"so"
    assert open(rig.path("game/csgo/addons/counterstrikesharp/plugins/a/a.dll"), "rb").read() == b"dll"
    assert oct(os.stat(rig.path("game/csgo/addons/metamod/bin/m.so")).st_mode & 0o777) == "0o755"
    assert "slimm-installer.py\" patch" in open(rig.path("pre.sh")).read()


def test_a_second_run_downloads_nothing_and_a_deleted_file_is_reinstalled():
    rig = Rig()
    pkg = rig.add("p", zip_bytes({"addons/x/marker.txt": b"1"}))
    rig.ensure([pkg])
    rig.fetched.clear()
    assert rig.ensure([pkg]) == [] and rig.fetched == []
    os.remove(rig.path("game/csgo/addons/x/marker.txt"))
    assert rig.ensure([pkg]) == ["p"] and len(rig.fetched) == 1


def test_a_new_pin_replaces_the_old_install():
    rig = Rig()
    old = rig.add("p-1", zip_bytes({"addons/x/marker.txt": b"old"}))
    rig.ensure([old])
    new = rig.add("p-1", zip_bytes({"addons/x/marker.txt": b"new"}))
    assert rig.ensure([new]) == ["p-1"]
    assert open(rig.path("game/csgo/addons/x/marker.txt"), "rb").read() == b"new"


def test_a_download_that_does_not_match_its_pin_installs_nothing():
    rig = Rig()
    pkg = rig.add("p", zip_bytes({"addons/x/marker.txt": b"1"}))
    rig.blobs[pkg.url] = zip_bytes({"addons/x/marker.txt": b"tampered"})
    try:
        rig.ensure([pkg])
    except installer.InstallError as err:
        assert "does not match the pin" in str(err)
    else:
        raise AssertionError("a wrong hash was accepted")
    assert not os.path.exists(rig.path("game/csgo/addons/x/marker.txt")) and not os.path.exists(rig.path("pre.sh"))


def test_an_archive_member_cannot_escape_its_folder():
    rig = Rig()
    for blob in (zip_bytes({"../evil.txt": b"x"}), tar_bytes({"../../evil.txt": b"x"})):
        pkg = rig.add("evil", blob)
        try:
            rig.ensure([pkg])
        except installer.InstallError as err:
            assert "escapes" in str(err)
        else:
            raise AssertionError("a path traversal was extracted")
    assert not os.path.exists(os.path.join(os.path.dirname(rig.root), "evil.txt"))


def test_patch_puts_metamod_before_the_csgo_search_path_once():
    rig = Rig()
    os.makedirs(rig.path("game/csgo"))
    with open(rig.path("game/csgo/gameinfo.gi"), "w") as handle:
        handle.write(GAMEINFO)
    assert installer.patch_gameinfo(rig.root) is True
    text = open(rig.path("game/csgo/gameinfo.gi")).read()
    assert text.index("csgo/addons/metamod") < text.index("Game\tcsgo\n") and text.count("addons/metamod") == 1
    assert installer.patch_gameinfo(rig.root) is False
    assert open(rig.path("game/csgo/gameinfo.gi")).read() == text


def test_patch_survives_a_game_update_that_restores_the_stock_file():
    rig = Rig()
    os.makedirs(rig.path("game/csgo"))
    for _ in range(2):
        with open(rig.path("game/csgo/gameinfo.gi"), "w") as handle:
            handle.write(GAMEINFO)
        assert installer.patch_gameinfo(rig.root) is True
        assert "csgo/addons/metamod" in open(rig.path("game/csgo/gameinfo.gi")).read()


def test_patch_refuses_a_gameinfo_without_the_csgo_search_path():
    try:
        installer.patched_text('"GameInfo"\n{\n\tGame\tcore\n}\n')
    except installer.InstallError as err:
        assert "Game csgo" in str(err)
    else:
        raise AssertionError("an unpatchable gameinfo.gi was accepted")


def test_the_pins_are_https_urls_with_full_sha256_hashes_and_unique_names():
    assert len({p.name for p in installer.PACKAGES}) == len(installer.PACKAGES)
    for pkg in installer.PACKAGES:
        assert pkg.url.startswith("https://") and len(pkg.sha256) == 64 and int(pkg.sha256, 16) >= 0


def test_main_reports_failure_with_a_nonzero_exit_and_no_traceback():
    rig = Rig()
    os.environ["STEAMAPPDIR"] = rig.root
    assert installer.main(["installer.py", "patch"]) == 1
    assert installer.main(["installer.py", "nope"]) == 2


if __name__ == "__main__":
    run_module_tests(globals())
