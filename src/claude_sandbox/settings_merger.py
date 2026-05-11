"""One-key surgical merger for `<workspace>/.claude/settings.json`.

The contract is deliberately tiny: touch only `hooks.UserPromptSubmit`,
append-with-dedupe our sandbox-check hook block, raise
`SettingsConflictError` on real disagreement (same hook script wired
with a different command). Never read, write, or move any other key
(`permissions`, `additionalDirectories`, `env`, …).

Why a dedicated module: the prior bash project's SettingsMerger was a
~700 LoC jq pipeline that grew because it tried to merge "everything
the user might want." This one trades surface for composability — the
user's other settings stay entirely theirs, which is also the only
sane default for an installer that runs unattended on rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

# The single key path this merger ever touches. Documented as a constant
# so a regression that adds a second key path is grep-detectable.
TARGET_KEY_PATH = ("hooks", "UserPromptSubmit")


class SettingsConflictError(RuntimeError):
    """Raised when the surgical merge encounters a real disagreement.

    "Real" means: the same hook script (matched by basename of the first
    whitespace-delimited token in `command`) is already wired with a
    different command string. Same-script-same-command is idempotent
    and never raises.
    """


def merge(existing: dict, ours: dict) -> dict:
    """Append-with-dedupe `ours` into `existing`'s `hooks.UserPromptSubmit`.

    Pure function — no I/O. Returns a new dict (the input is not
    mutated, so callers can compare byte-for-byte before deciding to
    write). Idempotent: merge(merge(x, h), h) == merge(x, h).

    `ours` is the hook *block* shape — a dict with `hooks: [{type, command}]`.
    """
    merged = dict(existing)
    hooks = dict(merged.get("hooks") or {})
    user_prompt_submit = list(hooks.get("UserPromptSubmit") or [])

    our_inner_hooks = ours.get("hooks", [])
    our_command = next(
        (h.get("command") for h in our_inner_hooks if h.get("type") == "command"),
        None,
    )

    # Dedupe / conflict detection by basename of the script. Users may
    # absolutise the path or append flags; we still recognise it as the
    # same hook and refuse only on a real command-string mismatch.
    if our_command:
        our_basename = Path(_first_token(our_command)).name
        for block in user_prompt_submit:
            for entry in block.get("hooks", []):
                if entry.get("type") != "command":
                    continue
                cmd = entry.get("command") or ""
                cmd_basename = Path(_first_token(cmd)).name
                if cmd_basename == our_basename:
                    if cmd != our_command:
                        raise SettingsConflictError(
                            f"refusing — UserPromptSubmit hook for {our_basename} is "
                            f"already wired with a different command ({cmd}). "
                            f"Reconcile by editing the file directly."
                        )
                    # Same hook already wired — no-op (idempotent).
                    return merged

    user_prompt_submit.append(ours)
    hooks["UserPromptSubmit"] = user_prompt_submit
    merged["hooks"] = hooks
    return merged


def merge_file(existing_path: Path, ours: dict) -> dict:
    """Read a settings.json, merge in our hook, return the merged dict.

    Raises `SettingsConflictError` if the file is unparseable JSON —
    the user has hand-edited a broken file and we refuse to overwrite
    it (a bad merge would lose the hand-edits).
    """
    if not existing_path.exists():
        return merge({}, ours)
    text = existing_path.read_text()
    # Claude Code itself writes settings.json as JSONC (// and /* */
    # comments allowed), so strict json.loads rejects perfectly valid
    # user files. Strip comments before parsing, but string-aware so we
    # don't mangle values like {"url": "https://x"}.
    stripped = _strip_jsonc_comments(text)
    if not stripped.strip():
        return merge({}, ours)
    try:
        existing = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SettingsConflictError(
            f"existing {existing_path} is not valid JSON; refusing to overwrite ({exc.msg})."
        ) from exc
    return merge(existing, ours)


def _strip_jsonc_comments(text: str) -> str:
    """Remove `//` line and `/* */` block comments, ignoring those inside strings.

    Tracks JSON-string state (with backslash escapes) so URLs and
    comment-like fragments inside string values survive. Does not
    attempt JSON5 trailing-comma stripping — Claude Code only emits
    comments, not other JSON5 extensions.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _first_token(command: str) -> str:
    """Return the first whitespace-delimited token of a command string."""
    return command.split(maxsplit=1)[0] if command.strip() else ""
