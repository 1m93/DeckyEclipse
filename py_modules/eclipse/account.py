"""The user's SteamOS password, the one sudo asks for.

A fresh SteamOS has none, so sudo cannot be used until one is set. ``passwd``
then only asks for the new password; with a password set it asks for the
current one first. Both facts are read from ``passwd``'s own prompts, driven
through a pseudo-terminal because it only talks to a terminal. ``os.openpty``
is used rather than the ``pty`` module, which Decky's bundled Python may lack.

Passwords are written to ``passwd`` only: never logged or stored.
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import time
from typing import Optional, Sequence

PROMPT_TIMEOUT_S = 10.0


class _Passwd:
    """A ``passwd`` process on a pseudo-terminal."""

    def __init__(self, cmd: Sequence[str], env: dict[str, str]) -> None:
        # English prompts whatever the system language.
        env = {**env, "LC_ALL": "C", "LANG": "C"}
        self.fd, terminal = os.openpty()
        try:
            self.process = subprocess.Popen(
                list(cmd),
                stdin=terminal,
                stdout=terminal,
                stderr=terminal,
                env=env,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            os.close(self.fd)
            raise
        finally:
            os.close(terminal)
        self.output = ""
        self.exit_code: Optional[int] = None

    def wait_for(self, *needles: str, timeout: float = PROMPT_TIMEOUT_S) -> Optional[str]:
        """Read until one of ``needles`` shows up; returns it, or None on EOF/timeout.

        Prompt needles (``_CURRENT``, ``_NEW``, ``_RETYPE``) only match a prompt
        waiting at the end of the output, e.g. "Current password: ", not words
        elsewhere (a user called "newton" in "Changing password for newton.").
        """
        deadline = time.monotonic() + timeout
        while True:
            for needle in needles:
                if _matches(needle, self.output):
                    return needle
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.fd < 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                return None
            try:
                chunk = os.read(self.fd, 4096)
            except OSError:  # EIO: the process closed the terminal
                return None
            if not chunk:
                return None
            self.output += chunk.decode(errors="replace")

    def consume(self) -> None:
        """Forget what was read so far, so the next prompt is matched afresh."""
        self.output = ""

    def send(self, line: str) -> None:
        # passwd turns echo off right after printing its prompt: give it a moment,
        # so the password is not echoed back.
        time.sleep(0.2)
        os.write(self.fd, (line + "\n").encode())

    def finish(self, timeout: float = PROMPT_TIMEOUT_S) -> int:
        """Read the remaining output and wait for the exit code."""
        self.wait_for("\0never\0", timeout=timeout)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            code = self._poll()
            if code is not None:
                return code
            time.sleep(0.05)
        self.kill()
        return -1

    def _poll(self) -> Optional[int]:
        if self.exit_code is None:
            self.exit_code = self.process.poll()
        return self.exit_code

    def kill(self) -> None:
        """Stop passwd if it still runs (at a prompt nothing has been changed yet) and clean up."""
        if self._poll() is None:
            try:
                self.process.kill()
            except OSError:
                pass  # It exited on its own in the meantime.
            try:
                self.exit_code = self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.exit_code = -1
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


_CURRENT = "current"
_NEW = "new"
_RETYPE = "retype"
_PROMPTS = {
    _CURRENT: re.compile(r"current[^\n]*password:\s*$", re.IGNORECASE),
    _NEW: re.compile(r"(?<!retype )new[^\n]*password:\s*$", re.IGNORECASE),
    _RETYPE: re.compile(r"(retype|repeat|again)[^\n]*password:\s*$", re.IGNORECASE),
}


def _matches(needle: str, output: str) -> bool:
    prompt = _PROMPTS.get(needle)
    return bool(prompt.search(output)) if prompt else needle in output.lower()


def password_state(cmd: Sequence[str], env: dict[str, str]) -> str:
    """"set", "none" (passwd asks for no current password) or "unknown". Changes nothing."""
    try:
        process = _Passwd(cmd, env)
    except OSError:
        return "unknown"
    try:
        found = process.wait_for(_CURRENT, _NEW)
    finally:
        # Killed at the prompt: nothing was changed.
        process.kill()
    return {_CURRENT: "set", _NEW: "none"}.get(found or "", "unknown")


def set_first_password(password: str, cmd: Sequence[str], env: dict[str, str]) -> Optional[str]:
    """Set the password when none is set yet. Returns None on success, else the reason."""
    if not password:
        return "Enter a password"
    if "\n" in password or "\r" in password:
        return "The password cannot contain line breaks"
    try:
        process = _Passwd(cmd, env)
    except OSError as exc:
        return f"Could not run passwd: {exc}"
    try:
        found = process.wait_for(_CURRENT, _NEW)
        if found == _CURRENT:
            return "This device already has a password. Enter it instead."
        if found != _NEW:
            return "passwd did not ask for a new password"
        process.consume()
        process.send(password)
        # A rejected password makes passwd ask again ("New password:") or fail.
        if process.wait_for(_RETYPE, _NEW) != _RETYPE:
            return _reason(process.output, password) or "passwd rejected the password, try a longer one"
        process.consume()
        process.send(password)
        if process.wait_for(_NEW, "success", "updated") == _NEW:
            return _reason(process.output, password) or "passwd rejected the password, try a longer one"
        code = process.finish()
        if code != 0:
            return _reason(process.output, password) or f"passwd failed (exit code {code})"
        return None
    finally:
        process.kill()


def _reason(output: str, password: str) -> str:
    """passwd's explanation, e.g. "BAD PASSWORD: The password is shorter than 8 characters"."""
    # Never show the password, should the terminal have echoed it after all.
    output = output.replace(password, "")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    useful = [line for line in lines if not line.lower().rstrip(":").endswith("password")]
    # The reason beats the summary ("passwd: password unchanged") that follows it.
    reasons = [line for line in useful if re.search(r"bad password|must|short|simple|dictionary|match", line, re.I)]
    return (reasons or useful or [""])[-1]
