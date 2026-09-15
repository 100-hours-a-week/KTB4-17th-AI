from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import questionary

from ktb_git.errors import UserAbort


@dataclass(frozen=True)
class Choice:
    label: str
    value: Any
    disabled_reason: str | None = None


class Wizard(Protocol):
    def text(self, key: str, prompt: str, *, default: str = "") -> str: ...

    def select(self, key: str, prompt: str, choices: list[Choice], *, default: Any = None) -> Any: ...

    def select_many(self, key: str, prompt: str, choices: list[Choice]) -> list[Any]: ...

    def confirm(self, key: str, prompt: str, *, default: bool = False) -> bool: ...


class QuestionaryWizard:
    @staticmethod
    def _answer(question: Any) -> Any:
        answer = question.ask()
        if answer is None:
            raise UserAbort("작업을 취소했습니다.")
        return answer

    def text(self, key: str, prompt: str, *, default: str = "") -> str:
        del key
        return str(self._answer(questionary.text(prompt, default=default)))

    def select(self, key: str, prompt: str, choices: list[Choice], *, default: Any = None) -> Any:
        del key
        items = [
            questionary.Choice(
                title=choice.label,
                value=choice.value,
                disabled=choice.disabled_reason,
            )
            for choice in choices
        ]
        return self._answer(questionary.select(prompt, choices=items, default=default))

    def select_many(self, key: str, prompt: str, choices: list[Choice]) -> list[Any]:
        del key
        items = [questionary.Choice(title=choice.label, value=choice.value) for choice in choices]
        return list(self._answer(questionary.checkbox(prompt, choices=items)))

    def confirm(self, key: str, prompt: str, *, default: bool = False) -> bool:
        del key
        return bool(self._answer(questionary.confirm(prompt, default=default)))


class FakeWizard:
    """Script answers by question key, preserving prompt order for assertions."""

    def __init__(self, answers: Mapping[str, list[Any]] | None = None) -> None:
        self.answers = {key: deque(values) for key, values in (answers or {}).items()}
        self.calls: list[tuple[str, str]] = []

    def _next(self, key: str, prompt: str) -> Any:
        self.calls.append((key, prompt))
        queue = self.answers.get(key)
        if not queue:
            raise AssertionError(f"FakeWizard answer missing for {key}: {prompt}")
        return queue.popleft()

    def text(self, key: str, prompt: str, *, default: str = "") -> str:
        answer = self._next(key, prompt)
        return default if answer is None else str(answer)

    def select(self, key: str, prompt: str, choices: list[Choice], *, default: Any = None) -> Any:
        answer = self._next(key, prompt)
        value = default if answer is None else answer
        allowed = [choice.value for choice in choices if not choice.disabled_reason]
        if value not in allowed:
            raise AssertionError(f"FakeWizard invalid answer {value!r} for {key}")
        return value

    def select_many(self, key: str, prompt: str, choices: list[Choice]) -> list[Any]:
        answer = self._next(key, prompt)
        allowed = [choice.value for choice in choices]
        if not isinstance(answer, list) or any(value not in allowed for value in answer):
            raise AssertionError(f"FakeWizard invalid selection for {key}")
        return answer

    def confirm(self, key: str, prompt: str, *, default: bool = False) -> bool:
        answer = self._next(key, prompt)
        return default if answer is None else bool(answer)
