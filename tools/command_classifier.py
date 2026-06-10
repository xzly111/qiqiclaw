"""Hardcoded command classifier.

Given a raw shell command string, return a category:

* ``QUICK``       — short, synchronous execution is fine (ls, cat, echo, pwd, …)
* ``LONG``        — should route to resilient_runner (pytest, npm install, …)
* ``INTERACTIVE`` — needs a TTY, cannot be run headless (vim, top, less, …)
* ``RISKY``       — destructive against qiqiclaw itself (handled by guard,
                    classifier just flags so callers can reject outright)
* ``UNKNOWN``     — doesn't match any heuristic; caller should default to
                    QUICK path (with a bounded timeout)

The classification lives in code, not config — non-programmer users won't
and shouldn't tune this.  Ordering inside the table matters: we scan
INTERACTIVE and RISKY first so they win over lookalikes (``top`` is
interactive even though the string ``top`` appears inside many words;
our match uses word boundaries).
"""

from __future__ import annotations

import re
import shlex
from enum import Enum
from typing import Optional


class CommandKind(str, Enum):
    QUICK = "quick"
    LONG = "long"
    INTERACTIVE = "interactive"
    RISKY = "risky"
    UNKNOWN = "unknown"


# Built-in shell commands + quick file inspections that never need backgrounding.
_QUICK_PROGRAMS = frozenset({
    "ls", "cat", "head", "tail", "wc", "pwd", "cd", "echo", "printf",
    "date", "uptime", "whoami", "id", "hostname", "uname",
    "which", "whereis", "type", "file", "stat",
    "env", "printenv", "set", "export", "alias",
    "true", "false", ":",
    # git read-only
    "git",  # scoped below to status/diff/log only
    # simple reads
    "grep", "egrep", "fgrep", "rg", "ripgrep",
    "basename", "dirname", "readlink", "realpath",
    "tr", "cut", "sort", "uniq", "awk", "sed",
    "test", "[",
    "ps",  # snapshot only; not "ps -T tty" with watch
    "df", "du",
    "ping", "nslookup",   # quick network info, not continuous
    "find",               # read-only unless -delete / -exec rm (guard catches those)
})

# Programs whose first-arg invocation is almost always long-running.
_LONG_COMMAND_PREFIXES = (
    # JS ecosystem
    "npm install", "npm i ", "npm ci", "npm run build", "npm run test",
    "npm run dev", "npm start", "npm test",
    "yarn install", "yarn add", "yarn build", "yarn test", "yarn dev",
    "pnpm install", "pnpm i ", "pnpm add", "pnpm build", "pnpm test",
    # Python
    "pip install", "pip3 install", "pip install -r",
    "uv pip install", "uv sync", "uv run",
    "poetry install", "poetry lock",
    "python setup.py", "python -m build",
    "pytest", "python -m pytest", "python -m unittest", "tox",
    # Rust
    "cargo build", "cargo test", "cargo run", "cargo install", "cargo check",
    # Go
    "go build", "go test", "go install", "go mod",
    # Make / CMake
    "make ", "make\t", "cmake ", "ninja",
    # System package managers
    "apt install", "apt-get install", "apt update", "apt upgrade",
    "apt-get update", "apt-get upgrade",
    "yum install", "dnf install", "pacman -S",
    "snap install", "brew install", "brew update", "brew upgrade",
    # Docker / containers
    "docker build", "docker pull", "docker push", "docker run",
    "docker-compose up", "docker compose up",
    # Bazel
    "bazel build", "bazel test", "bazel run",
    # Networking bulk
    "wget ", "curl -O", "curl -L", "rsync ", "scp ",
    "tar ", "zip -r", "unzip ",
    # Compiled
    "gcc ", "g++ ", "clang ", "clang++ ",
    # Data science / ML
    "jupyter", "accelerate launch", "deepspeed",
    "torchrun", "python -m accelerate",
    # Benchmarks / long tests
    "ab ", "siege ", "wrk ",
)

# Programs that require a controlling TTY.
_INTERACTIVE_PROGRAMS = frozenset({
    "vim", "vi", "nvim", "nano", "emacs", "pico", "micro",
    "top", "htop", "btop", "atop", "iotop", "nvtop",
    "less", "more", "most",
    "man", "info",
    "tmux", "screen", "byobu",
    "ssh", "mosh", "telnet",
    "mysql", "psql", "mongo", "redis-cli", "sqlite3",
    "python -i", "ipython", "bpython", "ptpython",
    "node", "irb",   # interactive when no args
    "gdb", "lldb", "pdb", "ipdb",
    "fzf",
})

_INTERACTIVE_TOKENS = [
    r"\bvim\b",
    r"\bnvim\b",
    r"\bvi\b(?!\w)",
    r"\bnano\b",
    r"\bemacs\b",
    r"\btop\b(?!\w)",
    r"\bhtop\b",
    r"\bbtop\b",
    r"\bless\b(?!\w)",
    r"\bmore\b(?!\w)",
    r"\bman\s+",
    r"\btmux\b",
    r"\bscreen\b",
    r"\bssh\b\s+\S",
    r"\btelnet\b",
    r"\bmysql\s+-u",
    r"\bpsql\b\s+\S",
    r"\bipython\b",
    r"\bbpython\b",
]

# "Risky" only for the classifier's purposes — the self_integrity_guard
# makes the final call.  Here we merely flag so callers can present a
# friendlier message (the guard still does the hard block).
_RISKY_PATTERNS = [
    r"\brm\s+(?:-[rRfv]+\s+)+",
    r"\bmkfs\b",
    r"\bdd\s+.*\bof=",
    r"\bshred\b",
    r"\btruncate\s+-s\s*0\b",
]


def _first_word(cmd: str) -> str:
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        parts = cmd.strip().split()
    return parts[0] if parts else ""


def _starts_with_any(cmd: str, prefixes: tuple[str, ...]) -> bool:
    stripped = cmd.lstrip()
    for pref in prefixes:
        if stripped.startswith(pref):
            return True
    return False


def classify(cmd: str) -> CommandKind:
    """Return the kind of *cmd*.  Empty or whitespace input → UNKNOWN."""
    if not isinstance(cmd, str):
        return CommandKind.UNKNOWN
    s = cmd.strip()
    if not s:
        return CommandKind.UNKNOWN

    # 1. Risky beats everything (so the caller can reject visibly).
    for pat in _RISKY_PATTERNS:
        if re.search(pat, s):
            return CommandKind.RISKY

    # 2. Interactive beats long.
    for tok in _INTERACTIVE_TOKENS:
        if re.search(tok, s):
            return CommandKind.INTERACTIVE

    # 3. Long prefixes.
    if _starts_with_any(s, _LONG_COMMAND_PREFIXES):
        return CommandKind.LONG

    # 3b. Bare `make` is a long build command even without arguments.
    head_word = _first_word(s).split("/")[-1]
    if head_word in {"make", "cmake", "ninja", "bazel", "cargo"}:
        return CommandKind.LONG

    # 4. Quick-program first word.
    head = _first_word(s)
    base = head.split("/")[-1]  # strip leading path like /usr/bin/ls
    if base in _QUICK_PROGRAMS:
        # Special-case: git can be long (git clone, git push -f)
        if base == "git":
            rest = s.split(None, 1)[1] if " " in s else ""
            if rest.startswith(("clone", "fetch --all", "push", "pull")):
                return CommandKind.LONG
        return CommandKind.QUICK

    return CommandKind.UNKNOWN


def suggested_timeout_seconds(kind: CommandKind) -> Optional[int]:
    """Return a reasonable wall-clock timeout for each kind.

    None means "caller should use resilient_runner (no single timeout)".
    """
    if kind is CommandKind.QUICK:
        return 30
    if kind is CommandKind.LONG:
        return None  # use resilient_runner's watcher + escalation
    if kind is CommandKind.INTERACTIVE:
        return 0     # refuse
    if kind is CommandKind.RISKY:
        return 0     # refuse
    # UNKNOWN — conservative short default
    return 60
