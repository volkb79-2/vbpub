"""Optional interactive prompts with an injectable driver.

Importing this module does not import Questionary or prompt-toolkit. The
optional UI dependencies are loaded only after an actual prompt is requested
from an interactive stdin/stdout pair.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable, Sequence
from typing import Any, Protocol, TextIO, runtime_checkable

__all__ = ["PromptAPI", "PromptCancelled", "PromptDriver"]


class PromptCancelled(Exception):
    """Raised when an interactive prompt is cancelled without an answer."""


@runtime_checkable
class PromptDriver(Protocol):
    """The five prompt interactions a CLI runtime can request.

    A driver may return ``None`` when the user cancels. The runtime converts
    that sentinel into :class:`PromptCancelled`; it is never treated as an
    answer such as ``False``, ``""``, or an empty checkbox selection.

    Implementations honor supplied defaults as the default terminal driver
    does. In particular, pressing Enter on optional text returns its supplied
    default, or the empty string when no default was given.
    """

    def text(
        self, message: str, *, default: str | None = None, required: bool = True
    ) -> str | None: ...

    def password(self, message: str, *, required: bool = True) -> str | None: ...

    def confirm(self, message: str, *, default: bool = False) -> bool | None: ...

    def select(
        self,
        message: str,
        choices: Sequence[str],
        *,
        default: str | None = None,
    ) -> str | None: ...

    def checkbox(
        self,
        message: str,
        choices: Sequence[str],
        *,
        default: Sequence[str] = (),
    ) -> list[str] | None: ...


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _raise_cli_failure(message: str, *, hint: str | None = None) -> None:
    # Import locally so this module can be loaded from parser.py without a
    # parser/prompts import cycle. CliFailure is defined before a runtime can
    # ask a prompt.
    from .parser import CliFailure

    raise CliFailure(message, exit_code=2, hint=hint)


class PromptAPI:
    """Validate prompt requests and enforce the runtime's terminal policy."""

    def __init__(
        self,
        *,
        stdin: TextIO,
        stdout: TextIO,
        driver: PromptDriver | None = None,
        interactive_extra: str = "cli-extended[interactive]",
        register_secret: Callable[[str], None] | None = None,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._driver = driver
        self._interactive_extra = interactive_extra
        self._register_secret = register_secret or (lambda _secret: None)

    def _get_driver(self) -> PromptDriver:
        if not (_is_tty(self._stdin) and _is_tty(self._stdout)):
            _raise_cli_failure(
                "interactive prompts require both stdin and stdout to be terminals",
                hint="run this command from an interactive terminal",
            )
        if self._driver is None:
            self._driver = _load_questionary_driver(
                self._stdin, self._stdout, self._interactive_extra
            )
        return self._driver

    @staticmethod
    def _validate_required(required: bool) -> None:
        if not isinstance(required, bool):
            raise TypeError("required must be a bool")

    @staticmethod
    def _validate_choices(choices: Sequence[str]) -> tuple[str, ...]:
        if isinstance(choices, (str, bytes)):
            raise TypeError("choices must be a sequence of strings, not a string")
        try:
            values = tuple(choices)
        except TypeError as exc:
            raise TypeError("choices must be a sequence of strings") from exc
        if not values:
            raise ValueError("choices must contain at least one string")
        if any(not isinstance(choice, str) for choice in values):
            raise TypeError("choices must contain only strings")
        if len(set(values)) != len(values):
            raise ValueError("choices must be unique")
        return values

    @staticmethod
    def _cancel_if_none(value: Any) -> Any:
        if value is None:
            raise PromptCancelled
        return value

    def text(
        self, message: str, *, default: str | None = None, required: bool = True
    ) -> str:
        """Collect text; required answers cannot be blank or whitespace-only."""

        self._validate_required(required)
        if default is not None and not isinstance(default, str):
            raise TypeError("default must be a string or None")
        value = self._cancel_if_none(
            self._get_driver().text(message, default=default, required=required)
        )
        if not isinstance(value, str):
            _raise_cli_failure("text prompt returned a non-text answer")
        if required and not value.strip():
            _raise_cli_failure("a required text answer cannot be blank")
        return value

    def password(self, message: str, *, required: bool = True) -> str:
        """Collect hidden text and register it for output redaction."""

        self._validate_required(required)
        value = self._cancel_if_none(
            self._get_driver().password(message, required=required)
        )
        if not isinstance(value, str):
            _raise_cli_failure("password prompt returned a non-text answer")
        if required and not value:
            _raise_cli_failure("a required password answer cannot be empty")
        if value:
            self._register_secret(value)
        return value

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Collect a yes/no answer; cancellation is distinct from ``False``."""

        if not isinstance(default, bool):
            raise TypeError("default must be a bool")
        value = self._cancel_if_none(
            self._get_driver().confirm(message, default=default)
        )
        if not isinstance(value, bool):
            _raise_cli_failure("confirmation prompt returned a non-boolean answer")
        return value

    def select(
        self,
        message: str,
        choices: Sequence[str],
        *,
        default: str | None = None,
    ) -> str:
        """Collect one choice, refusing any value outside the declared set."""

        values = self._validate_choices(choices)
        if default is not None and default not in values:
            raise ValueError("default must be one of the choices")
        value = self._cancel_if_none(
            self._get_driver().select(message, values, default=default)
        )
        if not isinstance(value, str) or value not in values:
            _raise_cli_failure("selection prompt returned a value outside its choices")
        return value

    def checkbox(
        self,
        message: str,
        choices: Sequence[str],
        *,
        default: Sequence[str] = (),
    ) -> list[str]:
        """Collect zero or more unique values from the declared choices."""

        values = self._validate_choices(choices)
        if isinstance(default, (str, bytes)):
            raise TypeError("default must be a sequence of choice strings")
        try:
            selected = tuple(default)
        except TypeError as exc:
            raise TypeError("default must be a sequence of choice strings") from exc
        if any(not isinstance(item, str) for item in selected):
            raise TypeError("default must contain only choice strings")
        if len(set(selected)) != len(selected):
            raise ValueError("default choices must be unique")
        if not set(selected).issubset(values):
            raise ValueError("default values must be among the choices")
        answer = self._cancel_if_none(
            self._get_driver().checkbox(message, values, default=selected)
        )
        if not isinstance(answer, list) or any(
            not isinstance(item, str) or item not in values for item in answer
        ):
            _raise_cli_failure("checkbox prompt returned a value outside its choices")
        if len(set(answer)) != len(answer):
            _raise_cli_failure("checkbox prompt returned duplicate choices")
        return answer


class _QuestionaryDriver:
    """Questionary adapter using the runtime's injected terminal streams."""

    def __init__(self, questionary: Any, input_stream: Any, output_stream: Any) -> None:
        self._questionary = questionary
        self._input = input_stream
        self._output = output_stream

    def _ask(self, question: Any) -> Any:
        # Question.ask() catches KeyboardInterrupt and returns None. The shared
        # CLI boundary owns Ctrl-C and its exit status, so use the uncaught form.
        return question.unsafe_ask()

    def text(
        self, message: str, *, default: str | None = None, required: bool = True
    ) -> str | None:
        validate = None
        if required:
            validate = lambda value: (
                True if value.strip() else "A non-whitespace value is required."
            )
        question = self._questionary.text(
            message,
            default="" if default is None else default,
            validate=validate,
            input=self._input,
            output=self._output,
        )
        return self._ask(question)

    def password(self, message: str, *, required: bool = True) -> str | None:
        validate = None
        if required:
            validate = lambda value: True if value else "A password is required."
        question = self._questionary.password(
            message,
            validate=validate,
            input=self._input,
            output=self._output,
        )
        return self._ask(question)

    def confirm(self, message: str, *, default: bool = False) -> bool | None:
        question = self._questionary.confirm(
            message,
            default=default,
            input=self._input,
            output=self._output,
        )
        return self._ask(question)

    def select(
        self,
        message: str,
        choices: Sequence[str],
        *,
        default: str | None = None,
    ) -> str | None:
        question = self._questionary.select(
            message,
            choices=list(choices),
            default=default,
            input=self._input,
            output=self._output,
        )
        return self._ask(question)

    def checkbox(
        self,
        message: str,
        choices: Sequence[str],
        *,
        default: Sequence[str] = (),
    ) -> list[str] | None:
        marked = set(default)
        question = self._questionary.checkbox(
            message,
            choices=[
                {"name": choice, "value": choice, "checked": choice in marked}
                for choice in choices
            ],
            input=self._input,
            output=self._output,
        )
        return self._ask(question)


def _load_questionary_driver(
    stdin: TextIO, stdout: TextIO, interactive_extra: str
) -> PromptDriver:
    try:
        import questionary
        from prompt_toolkit.input.defaults import create_input
        from prompt_toolkit.output.defaults import create_output
    except ImportError as exc:
        from .parser import CliFailure

        install_command = (
            f"{shlex.quote(sys.executable)} -m pip install "
            f"{shlex.quote(interactive_extra)}"
        )
        raise CliFailure(
            "interactive prompts need the optional Questionary dependency",
            exit_code=2,
            hint=f"install it with: {install_command}",
        ) from exc
    return _QuestionaryDriver(
        questionary,
        create_input(stdin=stdin),
        create_output(stdout=stdout),
    )
