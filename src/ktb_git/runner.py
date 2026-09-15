from __future__ import annotations

import shlex
import subprocess
from collections import deque
from dataclasses import dataclass


@dataclass
class Completed:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, completed: Completed):
        self.completed = completed
        detail = completed.stderr.strip() or completed.stdout.strip()
        message = f"명령 실패 ({completed.returncode}): {shlex.join(completed.args)}"
        if detail:
            message += f"\n{detail}"
        super().__init__(message)


class Runner:
    def run(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        input_text: str | None = None,
        check: bool = True,
        dry_run: bool = False,
    ) -> Completed:
        raise NotImplementedError


class SubprocessRunner(Runner):
    def run(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        input_text: str | None = None,
        check: bool = True,
        dry_run: bool = False,
    ) -> Completed:
        if dry_run:
            print(shlex.join(args))
            return Completed(list(args), 0, "", "")
        result = subprocess.run(
            args,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        completed = Completed(list(args), result.returncode, result.stdout, result.stderr)
        if check and completed.returncode:
            raise CommandError(completed)
        return completed


class FakeRunner(Runner):
    """Record calls and serve queued results matching exact args or a prefix."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._scripts: dict[tuple[str, ...], deque[Completed]] = {}

    def script(self, args: list[str], completed: Completed) -> None:
        if not args:
            raise ValueError("script args cannot be empty")
        self._scripts.setdefault(tuple(args), deque()).append(completed)

    def run(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        input_text: str | None = None,
        check: bool = True,
        dry_run: bool = False,
    ) -> Completed:
        del cwd, input_text
        self.calls.append(list(args))
        if dry_run:
            return Completed(list(args), 0, "", "")
        matching = [key for key, queue in self._scripts.items() if queue and tuple(args[: len(key)]) == key]
        if matching:
            key = max(matching, key=len)
            scripted = self._scripts[key].popleft()
            completed = Completed(list(args), scripted.returncode, scripted.stdout, scripted.stderr)
        else:
            completed = Completed(list(args), 0, "", "")
        if check and completed.returncode:
            raise CommandError(completed)
        return completed
