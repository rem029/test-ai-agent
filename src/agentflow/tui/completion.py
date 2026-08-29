"""Slash command and @file completers for prompt_toolkit REPL."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
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


NOISE_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".cache",
    "dist", "build", ".gradle", ".idea", "site-packages",
})


def _is_subsequence(sub: str, s: str) -> bool:
    """Check if sub is a subsequence of s in order."""
    if not sub:
        return True
    it = iter(s)
    return all(c in it for c in sub)


def _list_project_files(cwd: str) -> list[str]:
    """List project files relative to cwd, using git ls-files if available, falling back to os.walk."""
    try:
        r1 = subprocess.run(
            ["git", "ls-files"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        r2 = subprocess.run(
            ["git", "ls-files", "--others"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r1.returncode == 0 and r2.returncode == 0:
            raw_files: set[str] = set()
            for line in r1.stdout.splitlines():
                line = line.strip()
                if line:
                    raw_files.add(line.replace("\\", "/"))
            for line in r2.stdout.splitlines():
                line = line.strip()
                if line:
                    raw_files.add(line.replace("\\", "/"))
            filtered_files = [
                p
                for p in raw_files
                if not any(
                    seg in NOISE_DIRS or seg.endswith(".egg-info")
                    for seg in p.split("/")
                )
            ]
            file_list = sorted(filtered_files)[:8000]
            dir_set: set[str] = set()
            for path in file_list:
                parts = path.split("/")
                for i in range(1, len(parts)):
                    dir_set.add("/".join(parts[:i]) + "/")
            return sorted(set(file_list) | dir_set)
    except Exception:
        pass

    try:
        file_set: set[str] = set()
        cwd_path = Path(cwd).resolve()
        for root, dirs, filenames in os.walk(cwd_path):
            dirs[:] = [
                d
                for d in dirs
                if d not in NOISE_DIRS and not d.endswith(".egg-info")
            ]
            for filename in filenames:
                full_path = Path(root) / filename
                try:
                    rel_path = full_path.relative_to(cwd_path).as_posix()
                    if not any(
                        seg in NOISE_DIRS or seg.endswith(".egg-info")
                        for seg in rel_path.split("/")
                    ):
                        file_set.add(rel_path)
                except ValueError:
                    continue
                if len(file_set) >= 8000:
                    break
            if len(file_set) >= 8000:
                break
        file_list = sorted(file_set)[:8000]
        dir_set: set[str] = set()
        for path in file_list:
            parts = path.split("/")
            for i in range(1, len(parts)):
                dir_set.add("/".join(parts[:i]) + "/")
        return sorted(set(file_list) | dir_set)
    except Exception:
        return []


class FileMentionCompleter(Completer):
    """Prompt-toolkit completer for @file mentions in the REPL."""

    def __init__(
        self,
        cwd: str,
        file_lister: Callable[[], list[str]] | None = None,
        cache_ttl: float = 30.0,
    ) -> None:
        self._cwd = str(cwd)
        self._file_lister = file_lister
        self._cache_ttl = cache_ttl
        self._cached_files: list[str] | None = None
        self._cache_time: float = 0.0

    def _get_files(self) -> list[str]:
        now = time.monotonic()
        if self._cached_files is None or (now - self._cache_time) > self._cache_ttl:
            try:
                if self._file_lister is not None:
                    self._cached_files = list(self._file_lister())
                else:
                    self._cached_files = _list_project_files(self._cwd)
            except Exception:
                self._cached_files = []
            self._cache_time = now
        return self._cached_files

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        try:
            text = document.text_before_cursor
            if not text or text[-1].isspace():
                return
            if text.lstrip().startswith("/"):
                return

            tokens = text.split()
            if not tokens:
                return

            current_token = tokens[-1]
            if not current_token.startswith("@"):
                return

            query = current_token[1:]
            query_lower = query.lower()
            files = self._get_files()

            if not query:
                for path in sorted(files)[:50]:
                    display_meta = "dir" if path.endswith("/") else os.path.dirname(path)
                    yield Completion(
                        path,
                        start_position=0,
                        display=path,
                        display_meta=display_meta,
                    )
                return

            matching_paths: list[str] = [
                p for p in files if _is_subsequence(query_lower, p.lower())
            ]
            if not matching_paths:
                return

            def _score(path: str) -> tuple[int, int, str]:
                p = path[:-1] if path.endswith("/") else path
                p_lower = p.lower()
                basename_lower = os.path.basename(p).lower()
                if basename_lower == query_lower:
                    tier = 0
                elif basename_lower.startswith(query_lower):
                    tier = 1
                elif any(
                    seg.startswith(query_lower)
                    for seg in p_lower.replace("\\", "/").split("/")
                    if seg
                ):
                    tier = 2
                elif query_lower in basename_lower:
                    tier = 3
                elif query_lower in p_lower:
                    tier = 4
                else:
                    tier = 5
                return (tier, len(path), path)

            matching_paths.sort(key=_score)

            for path in matching_paths[:50]:
                display_meta = "dir" if path.endswith("/") else os.path.dirname(path)
                yield Completion(
                    path,
                    start_position=-len(query),
                    display=path,
                    display_meta=display_meta,
                )
        except Exception:
            return
