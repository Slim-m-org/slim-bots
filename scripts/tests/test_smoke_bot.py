import json
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smoke_bot as s  # noqa: E402

GOOD_ENV = {"SLIMM_URL": "u", "SLIMM_BOT_TOKEN": "b", "SLIMM_SMOKE_TESTER_TOKEN": "t", "SLIMM_SMOKE_CHANNEL": "c", "SLIMM_SMOKE_PRIVATE": "1"}


def test_refuses_without_secrets_or_private_confirmation():
    assert s.missing_env({}) != []
    assert s.missing_env({**GOOD_ENV, "SLIMM_SMOKE_PRIVATE": "0"}) == ["SLIMM_SMOKE_PRIVATE=1"]
    assert s.missing_env(GOOD_ENV) == []


def test_main_exits_2_and_never_boots_a_bot_without_env(monkeypatch):
    for k in GOOD_ENV:
        monkeypatch.delenv(k, raising=False)
    assert s.main(["greeter"]) == 2


def test_shipped_manifests_parse():
    for path in (Path(__file__).resolve().parent.parent / "smoke").glob("*.json"):
        if path.name != "exempt.json":
            s.load_manifest(path)


@pytest.mark.parametrize("bad", [{}, {"steps": []}, {"steps": [{"say": ""}]}, {"steps": [{"say": "x", "expect_contains": [1]}]}])
def test_bad_manifest_rejected(tmp_path, bad):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        s.load_manifest(p)


def test_replies_after_takes_only_others_newer_messages():
    msgs = [{"id": "3", "author_id": "bot"}, {"id": "2", "author_id": "me"}, {"id": "1", "author_id": "bot"}]
    assert [m["id"] for m in s.replies_after(msgs, "2", "me")] == ["3"]
    assert s.replies_after(msgs, "gone", "me") == []


def test_run_step_records_reply_and_missing():
    msgs = [{"id": "r", "author_id": "bot", "content": "hello commands"}, {"id": "q", "author_id": "me", "content": "!help"}]
    step = {"say": "!help", "expect_contains": ["commands", "nope"], "timeout": 3}
    r = s.run_step(step, lambda _t: "q", lambda: msgs, "me", sleep=lambda _d: None)
    assert r["replies"] == ["hello commands"]
    assert r["missing"] == ["nope"]
    assert not r["ok"]


def test_run_step_no_reply_is_failure():
    r = s.run_step({"say": "!x", "timeout": 2}, lambda _t: "q", lambda: [{"id": "q", "author_id": "me"}], "me", sleep=lambda _d: None)
    assert not r["ok"]
    assert "(no reply)" in s.render("b", [r])


def test_run_step_polls_at_most_the_cap():
    polls = []
    s.run_step({"say": "!x", "timeout": 10**9}, lambda _t: "q", lambda: polls.append(1) or [], "me", sleep=lambda _d: None)
    assert len(polls) == s.MAX_TIMEOUT


@pytest.mark.parametrize("bot", ["../etc", "a/b", "A", "-x", "", "a.b", "x\n"])
def test_bad_bot_names_refused(bot):
    with pytest.raises(ValueError):
        s.bot_paths(bot)


def make_repo(root):
    (root / "bots" / "casino").mkdir(parents=True)
    (root / "scripts" / "smoke").mkdir(parents=True)
    (root / "scripts" / "smoke" / "casino.json").write_text("{}")


def test_good_bot_name_resolves_from_listing(tmp_path):
    make_repo(tmp_path)
    name, bot_dir, manifest = s.bot_paths("casino", root=tmp_path)
    assert name == "casino"
    assert bot_dir == (tmp_path / "bots" / "casino").resolve()
    assert manifest == (tmp_path / "scripts" / "smoke" / "casino.json").resolve()


def test_unlisted_bot_and_manifest_refused(tmp_path):
    make_repo(tmp_path)
    with pytest.raises(ValueError):
        s.bot_paths("nope", root=tmp_path)
    with pytest.raises(ValueError):
        s.bot_paths("casino", "../../etc/passwd", root=tmp_path)


def test_symlink_escape_refused(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "outside").mkdir()
    (tmp_path / "bots" / "evil").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        s.bot_paths("evil", root=tmp_path)


def test_manifest_path_is_confined(tmp_path):
    with pytest.raises(ValueError):
        s.confined(tmp_path / ".." / "x.json", tmp_path)


def test_main_refuses_traversal_bot_name(monkeypatch):
    for k, v in GOOD_ENV.items():
        monkeypatch.setenv(k, v)
    assert s.main(["../../etc"]) == 2


@pytest.mark.parametrize("manifest", [
    {"steps": [{"say": "x"}] * (51)},
    {"steps": [{"say": "x", "timeout": 10**9}]},
    {"steps": [{"say": "x", "timeout": "5"}]},
    {"steps": [{"say": "x", "timeout": True}]},
    {"steps": [{"say": "x"}], "ready_seconds": 10**6},
    {"steps": [{"say": "x"}], "env": {"A": 1}},
])
def test_manifest_bounds_enforced(tmp_path, manifest):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        s.load_manifest(p)


def test_a_private_reply_counts_as_the_bots_answer():
    msgs = [{"id": "q", "author_id": "me", "content": "!deals"}]
    private = {"q": [{"content": "only you see this"}]}
    step = {"say": "!deals", "expect_contains": ["only you"], "timeout": 2}
    r = s.run_step(step, lambda _t: "q", lambda: msgs, "me", sleep=lambda _d: None, private=lambda sent: private.get(sent, []))
    assert r["ok"] and r["replies"] == ["only you see this"]


def test_a_private_reply_to_another_message_is_not_taken():
    msgs = [{"id": "q", "author_id": "me", "content": "!deals"}]
    step = {"say": "!deals", "timeout": 1}
    r = s.run_step(step, lambda _t: "q", lambda: msgs, "me", sleep=lambda _d: None, private=lambda _sent: [])
    assert not r["ok"]


def test_a_step_pauses_before_it_is_typed():
    slept = []
    msgs = [{"id": "r", "author_id": "bot", "content": "ok"}, {"id": "q", "author_id": "me", "content": "x"}]
    s.run_step({"say": "x", "pause": 4, "timeout": 1}, lambda _t: "q", lambda: msgs, "me", sleep=slept.append)
    assert slept[0] == 4


def test_a_pause_over_the_cap_is_refused(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"steps": [{"say": "x", "pause": s.MAX_PAUSE + 1}]}))
    with pytest.raises(ValueError):
        s.load_manifest(p)


def test_waiting_for_registration_stops_as_soon_as_the_bot_has_registered():
    answers = iter([False, False, True])
    slept = []
    assert s.wait_until_registered(lambda: next(answers), 30, sleep=slept.append)
    assert len(slept) == 2


def test_waiting_for_registration_gives_up_at_its_limit_and_survives_a_failed_poll():
    slept = []

    def failing():
        raise s.httpx.ConnectError("not up yet")

    assert not s.wait_until_registered(failing, 3, sleep=slept.append)
    assert len(slept) == 3


def test_a_non_json_registration_probe_is_a_failed_poll_not_a_crash():
    answers = iter([ValueError("not json"), True])

    def probe():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert s.wait_until_registered(probe, 5, sleep=lambda _d: None)


@pytest.mark.parametrize("body, expected", [
    ({"commands": [{"name": "help"}]}, True),
    ({"commands": []}, False),
    ({}, False),
    ([], False),
    (None, False),
    ("<html>", False),
])
def test_only_a_commands_object_with_entries_counts_as_registered(body, expected):
    assert s.has_commands(body) is expected


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return self

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, *_args, **_kwargs):
        self.sent = []

    def get(self, path, **_kwargs):
        if path == "/me":
            return FakeResponse({"id": "tester"})
        return FakeResponse([{"id": self.sent[-1], "author_id": "tester"}] if self.sent else [])

    def post(self, _path, json=None, **_kwargs):
        self.sent.append(json["id"])
        return FakeResponse({})


class FakePrivate:
    def __init__(self, *_args):
        self.closed = False

    def replies_to(self, _sent_id):
        return []

    def close(self):
        self.closed = True


def stubborn_bot_repo(root):
    bot_dir = root / "bots" / "stubborn"
    bot_dir.mkdir(parents=True)
    (bot_dir / "bot.py").write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n")
    manifest = root / "scripts" / "smoke" / "stubborn.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"ready_seconds": 0.01, "steps": [{"say": "!x", "timeout": 1}]}))
    return bot_dir, manifest


@pytest.fixture
def smoke_run(tmp_path, monkeypatch):
    """Wires main() to a fake server and a real SIGTERM-ignoring bot process; returns the processes and fake sockets it started."""
    bot_dir, manifest = stubborn_bot_repo(tmp_path)
    started, sockets = [], []
    real_popen = s.subprocess.Popen

    def recording_popen(*args, **kwargs):
        started.append(real_popen(*args, **kwargs))
        return started[-1]

    def recording_private(*args):
        sockets.append(FakePrivate(*args))
        return sockets[-1]

    for key, value in GOOD_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SLIMM_SMOKE_BOT_ID", raising=False)
    monkeypatch.setattr(s, "bot_paths", lambda *_a, **_k: ("stubborn", bot_dir, manifest))
    monkeypatch.setattr(s, "TRANSCRIPT_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(s, "STOP_GRACE", 0.5)
    monkeypatch.setattr(s.httpx, "Client", FakeHttp)
    monkeypatch.setattr(s, "PrivateReplies", recording_private)
    monkeypatch.setattr(s.subprocess, "Popen", recording_popen)
    yield started, sockets
    for proc in started:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_bot_that_ignores_sigterm_is_killed_and_the_transcript_still_lands(smoke_run, tmp_path):
    started, sockets = smoke_run
    assert s.main(["stubborn"]) == 1
    (proc,) = started
    assert proc.poll() == -signal.SIGKILL
    assert sockets[0].closed
    (transcript,) = (tmp_path / "transcripts").glob("smoke-stubborn-*.md")
    assert "FAIL: `!x`" in transcript.read_text()


def test_the_transcript_is_written_even_when_stopping_the_bot_fails(smoke_run, tmp_path, monkeypatch):
    started, sockets = smoke_run

    def hung(_proc, grace=None):
        raise s.subprocess.TimeoutExpired("bot.py", 0.5)

    monkeypatch.setattr(s, "stop_bot", hung)
    with pytest.raises(s.subprocess.TimeoutExpired):
        s.main(["stubborn"])
    assert sockets[0].closed
    assert len(list((tmp_path / "transcripts").glob("smoke-stubborn-*.md"))) == 1


def test_stop_bot_leaves_a_bot_that_exits_on_sigterm_alone():
    class Polite:
        killed = False

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    proc = Polite()
    s.stop_bot(proc, grace=1)
    assert not proc.killed
