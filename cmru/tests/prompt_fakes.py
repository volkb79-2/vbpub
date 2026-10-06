"""A scripted stand-in for the cli-extended prompt API (``runtime.prompts``).

``cmru init`` asks through ``runtime.prompts.text/select/confirm``. The real API
refuses a non-terminal; tests script the answers instead. One answer list covers
every question in the order asked, which keeps a wizard transcript readable:
``""`` accepts the offered default, ``"yes"``/``"no"`` answer a confirmation.
"""
from __future__ import annotations

from collections.abc import Sequence


class ScriptedPrompts:
    def __init__(self, answers: Sequence[str]):
        self._answers = iter(answers)
        self.asked: list[str] = []

    def _next(self, message: str) -> str:
        self.asked.append(message)
        try:
            return next(self._answers)
        except StopIteration as exc:  # a question the transcript did not expect
            raise AssertionError(f"unscripted prompt: {message}") from exc

    def text(self, message: str, *, default: str | None = None, required: bool = True) -> str:
        answer = self._next(message)
        if answer == "" and default is not None:
            return default
        return answer

    def select(self, message: str, choices, *, default: str | None = None) -> str:
        answer = self._next(message)
        return default if answer == "" and default is not None else answer

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return self._next(message) == "yes"
