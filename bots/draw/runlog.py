"""One stdout line per command outcome, with stage timings; never a secret, never more than 60 characters of the prompt."""

import json
import time
import traceback
from contextlib import contextmanager

PROMPT_CHARS = 60


class RunLog:
    def __init__(self, command, channel_id, prompt="", emit=print, clock=time.monotonic):
        self.command, self.channel_id, self.emit, self.clock = command, channel_id, emit, clock
        self.prompt = " ".join(str(prompt).split())[:PROMPT_CHARS]
        self.source, self.stages, self.started, self.finished = None, {}, clock(), False

    @contextmanager
    def stage(self, name):
        """Adds the time spent inside the block to the stage `name`, even when it raises."""
        begin = self.clock()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (self.clock() - begin)

    def done(self, outcome):
        """Emits the one line for this command; later calls do nothing."""
        if self.finished:
            return
        self.finished = True
        fields = [f"command={self.command}", f"channel={self.channel_id}", f"outcome={json.dumps(str(outcome)[:120])}"]
        if self.source:
            fields.append(f"source={self.source}")
        fields += [f"{name}={seconds:.1f}s" for name, seconds in self.stages.items()]
        fields += [f"total={self.clock() - self.started:.1f}s", f"prompt={json.dumps(self.prompt)}"]
        self.emit("draw " + " ".join(fields), flush=True)

    def failed(self, err):
        """Records an unexpected exception: its class as the outcome and the traceback on its own lines."""
        self.emit("draw traceback " + "".join(traceback.format_exception(err)).rstrip(), flush=True)
        self.done(f"error {type(err).__name__}")
