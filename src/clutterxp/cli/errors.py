"""Error handling and exit code mapping for ClutterXP CLI.

Exit codes per docs/08_CLI.md:
  0: success (including nothing to do)
  1: unexpected error (traceback written to log, short message to stderr)
  2: usage or configuration error
  3: partial failure (some files failed/blocked/refused) or --strict with ambiguity
  4: refused by safety rule (root, denylist, confirmation required in non-interactive session)
  5: lock held by another instance
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

import typer

from clutterxp.models import ClutterError, ConfigError, ErrorCode, SafetyRefusal

logger = logging.getLogger("clutterxp.cli")

P = ParamSpec("P")
R = TypeVar("R")


def exit_with_code(code: int) -> None:
    """Exit cleanly with the given exit code using typer.Exit."""
    raise typer.Exit(code=code)


@contextmanager
def handle_errors(
    *,
    json_mode: bool = False,
    command_name: str | None = None,
) -> Generator[None, None, None]:
    """Context manager to trap exceptions and map them to appropriate CLI exit codes.

    Can also be used directly inside commands or as a decorator.
    """
    try:
        yield
    except typer.Exit:
        # Pass through deliberate typer exit codes
        raise
    except typer.Abort:
        # Typer user cancel (Ctrl-C or abort)
        raise typer.Exit(code=1) from None
    except SafetyRefusal as e:
        is_lock = e.code == ErrorCode.LOCK_TIMEOUT or "lock" in str(e).lower()
        exit_code = 5 if is_lock else 4
        msg = f"error: {e.message or str(e)}"
        if json_mode:
            import json

            sys.stdout.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": command_name or "unknown",
                        "error": str(e),
                        "code": exit_code,
                    }
                )
                + "\n"
            )
        else:
            sys.stderr.write(f"{msg}\n")
        raise typer.Exit(code=exit_code) from None
    except ConfigError as e:
        if json_mode:
            import json

            payload: dict[str, Any] = {
                "schema_version": 1,
                "command": command_name or "unknown",
                "error": str(e),
                "code": 2,
            }
            if e.problems:
                payload["problems"] = list(e.problems)
            sys.stdout.write(json.dumps(payload) + "\n")
        else:
            if e.problems:
                for p in e.problems:
                    sys.stderr.write(f"error: {p}\n")
            else:
                sys.stderr.write(f"error: {e.message or str(e)}\n")
        raise typer.Exit(code=2) from None
    except ClutterError as e:
        is_lock = e.code == ErrorCode.LOCK_TIMEOUT or "lock" in str(e).lower()
        exit_code = 5 if is_lock else 3
        if json_mode:
            import json

            sys.stdout.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": command_name or "unknown",
                        "error": str(e),
                        "code": exit_code,
                    }
                )
                + "\n"
            )
        else:
            sys.stderr.write(f"error: {e.message or str(e)}\n")
        raise typer.Exit(code=exit_code) from None
    except (FileNotFoundError, FileExistsError) as e:
        if json_mode:
            import json

            sys.stdout.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": command_name or "unknown",
                        "error": str(e),
                        "code": 2,
                    }
                )
                + "\n"
            )
        else:
            sys.stderr.write(f"error: {e}\n")
        raise typer.Exit(code=2) from None
    except Exception as e:
        logger.exception("Unexpected error during CLI execution: %s", e)
        if json_mode:
            import json

            sys.stdout.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": command_name or "unknown",
                        "error": str(e),
                        "code": 1,
                    }
                )
                + "\n"
            )
        else:
            sys.stderr.write(f"error: unexpected error: {e}\n")
        raise typer.Exit(code=1) from None


def error_handler_decorator(func: Callable[P, R]) -> Callable[P, R]:
    """Decorator version of handle_errors for Typer commands."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        json_mode = bool(kwargs.get("json_output", False))
        with handle_errors(json_mode=json_mode, command_name=func.__name__):
            return func(*args, **kwargs)

    return wrapper


__all__ = ["error_handler_decorator", "exit_with_code", "handle_errors"]
