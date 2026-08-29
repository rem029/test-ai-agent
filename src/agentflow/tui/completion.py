"""Slash command completer for prompt_toolkit REPL."""

from __future__ import annotations

from typing import Callable, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from ..backends import BACKENDS
from ..models import CURATED_MODELS
from .commands import COMMANDS, CommandSpec


class SlashCommandCompleter(Completer):
    """Prompt-toolkit completer for agentflow slash commands and their arguments."""

    def __init__(
        self,
        session_resolver: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._session_resolver = session_resolver

    def _resolve(self, source_key: str) -> tuple[str, ...]:
        try:
            if source_key == "role":
                return ("review", "build", "verify")
            if source_key == "backend":
                return tuple(BACKENDS.keys())
            if source_key == "permission":
                return ("auto", "prompt", "deny")
            if source_key == "config_sub":
                return ("permissions", "max-cost", "review", "build", "verify")
            if source_key == "model_id":
                model_ids: set[str] = set()
                for model_list in CURATED_MODELS.values():
                    for m in model_list:
                        if isinstance(m, dict) and "id" in m:
                            model_ids.add(str(m["id"]))
                return tuple(sorted(model_ids))
            if source_key == "session_id":
                # TODO: Pass session lister if available from caller
                if self._session_resolver is not None:
                    return tuple(self._session_resolver())
                return ()
        except Exception:
            return ()
        return ()

    def _get_arg_source_key(
        self, spec: CommandSpec, tokens: list[str], arg_idx: int
    ) -> str | None:
        if spec.name == "/config":
            if arg_idx == 0:
                return "config_sub"
            if arg_idx == 1:
                if len(tokens) > 1 and tokens[1].lower() == "permissions":
                    return "permission"
                if len(tokens) > 1 and tokens[1].lower() in ("review", "build", "verify"):
                    return "backend"
                return None
            if arg_idx == 2:
                if len(tokens) > 1 and tokens[1].lower() in ("review", "build", "verify"):
                    return "model_id"
                return None
            return None

        if arg_idx < len(spec.arg_completions):
            return spec.arg_completions[arg_idx]
        return None

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        try:
            text = document.text_before_cursor
            if not text.lstrip().startswith("/"):
                return

            tokens = text.split()
            has_trailing_space = text.endswith(" ")

            # Completing the command word (one token, no trailing space)
            if len(tokens) <= 1 and not has_trailing_space:
                token = tokens[0] if tokens else "/"
                for spec in COMMANDS:
                    if spec.name.lower().startswith(token.lower()):
                        yield Completion(
                            spec.name,
                            start_position=-len(token),
                            display=spec.name,
                            display_meta=spec.summary,
                        )
                return

            # Completing an argument
            if not tokens:
                return

            cmd_name = tokens[0].lower()
            spec = next((s for s in COMMANDS if s.name.lower() == cmd_name), None)
            if spec is None:
                return

            if has_trailing_space:
                arg_idx = len(tokens) - 1
                partial_token = ""
            else:
                arg_idx = len(tokens) - 2
                partial_token = tokens[-1]

            if arg_idx < 0:
                return

            source_key = self._get_arg_source_key(spec, tokens, arg_idx)
            if not source_key:
                return

            candidates = self._resolve(source_key)
            for candidate in candidates:
                if candidate.lower().startswith(partial_token.lower()):
                    yield Completion(
                        candidate,
                        start_position=-len(partial_token),
                        display=candidate,
                    )
        except Exception:
            return
