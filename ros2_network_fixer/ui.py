"""
ui.py — Terminal output helpers.

Provides colour-coded output, spinners, banners, and step indicators
that degrade gracefully on terminals without colour support.
"""

from __future__ import annotations

import os
import sys
import time
import threading
from contextlib import contextmanager
from typing import Iterator


# ---------------------------------------------------------------------------
# Colour support detection
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    # Windows: enable VT100 if possible
    if sys.platform == "win32":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()


class _C:
    """ANSI colour codes."""
    RESET   = "\033[0m"  if _COLOR else ""
    BOLD    = "\033[1m"  if _COLOR else ""
    DIM     = "\033[2m"  if _COLOR else ""

    RED     = "\033[91m" if _COLOR else ""
    GREEN   = "\033[92m" if _COLOR else ""
    YELLOW  = "\033[93m" if _COLOR else ""
    BLUE    = "\033[94m" if _COLOR else ""
    MAGENTA = "\033[95m" if _COLOR else ""
    CYAN    = "\033[96m" if _COLOR else ""
    WHITE   = "\033[97m" if _COLOR else ""

    # Semantic aliases
    OK      = GREEN
    WARN    = YELLOW
    ERR     = RED
    INFO    = CYAN
    HEAD    = BOLD + BLUE


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_C.RESET}"


# ---------------------------------------------------------------------------
# Basic output primitives
# ---------------------------------------------------------------------------

def banner() -> None:
    print()
    print(_c(_C.HEAD, "╔══════════════════════════════════════════════════════════╗"))
    print(_c(_C.HEAD, "║        ROS 2  Cross-Platform  Network  Fixer  v1.0       ║"))
    print(_c(_C.HEAD, "║   Automates DDS discovery · firewall · WSL2 · Docker     ║"))
    print(_c(_C.HEAD, "╚══════════════════════════════════════════════════════════╝"))
    print()


def section(title: str) -> None:
    bar = "─" * (len(title) + 4)
    print()
    print(_c(_C.BLUE, f"┌{bar}┐"))
    print(_c(_C.BLUE, f"│  {_C.BOLD}{title}{_C.RESET}{_C.BLUE}  │"))
    print(_c(_C.BLUE, f"└{bar}┘"))


def ok(msg: str) -> None:
    print(f"  {_c(_C.OK, '✔')}  {msg}")


def warn(msg: str) -> None:
    print(f"  {_c(_C.WARN, '⚠')}  {msg}")


def error(msg: str) -> None:
    print(f"  {_c(_C.ERR, '✘')}  {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"  {_c(_C.INFO, '→')}  {msg}")


def step(msg: str) -> None:
    print(f"  {_c(_C.MAGENTA, '◆')}  {_c(_C.BOLD, msg)}")


def detail(msg: str) -> None:
    print(f"     {_c(_C.DIM, msg)}")


def cmd_block(cmd: str) -> None:
    """Display a command the user should run."""
    print(f"\n    {_c(_C.DIM, '$')} {_c(_C.YELLOW, cmd)}\n")


def code_block(lines: list[str], label: str = "") -> None:
    """Display a multi-line code/config block."""
    if label:
        print(f"  {_c(_C.DIM, f'─── {label} ───')}")
    for line in lines:
        print(f"    {_c(_C.CYAN, line)}")
    print()


def kv(key: str, value: str, ok_val: bool = True) -> None:
    colour = _C.OK if ok_val else _C.WARN
    print(f"    {_c(_C.DIM, key + ':')}  {_c(colour, value)}")


def hr() -> None:
    print(_c(_C.DIM, "  " + "─" * 58))


def nl() -> None:
    print()


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

class Spinner:
    _FRAMES = ["⠋", "⠙", "⠸", "⠴", "⠦", "⠇"]

    def __init__(self, message: str) -> None:
        self._msg = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            sys.stdout.write(f"\r  {_c(_C.CYAN, frame)}  {self._msg} ")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1
        sys.stdout.write("\r" + " " * (len(self._msg) + 10) + "\r")
        sys.stdout.flush()

    def start(self) -> None:
        if _COLOR:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            print(f"  ...  {self._msg}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()


@contextmanager
def spinner(message: str) -> Iterator[None]:
    s = Spinner(message)
    s.start()
    try:
        yield
    finally:
        s.stop()


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def confirm(prompt: str, default: bool = False) -> bool:
    """Ask yes/no. Returns bool."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(f"\n  {_c(_C.YELLOW, '?')}  {prompt}{suffix}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


def choose(prompt: str, options: list[str]) -> int:
    """Present a numbered menu, return 0-based index."""
    print(f"\n  {_c(_C.YELLOW, '?')}  {prompt}")
    for i, opt in enumerate(options, 1):
        print(f"    {_c(_C.CYAN, str(i))}) {opt}")
    while True:
        try:
            raw = input("  → ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        warn("Please enter a valid number.")


def prompt(msg: str, default: str = "") -> str:
    """Free-text prompt with optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"\n  {_c(_C.YELLOW, '?')}  {msg}{suffix}: ").strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        return default


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def summary_table(rows: list[tuple[str, str, str]]) -> None:
    """rows: list of (label, value, status) where status in ok/warn/err/info."""
    STATUS_ICON = {
        "ok":   (_C.OK,   "✔"),
        "warn": (_C.WARN, "⚠"),
        "err":  (_C.ERR,  "✘"),
        "info": (_C.INFO, "→"),
    }
    col_w = max((len(r[0]) for r in rows), default=10) + 2
    for label, value, status in rows:
        colour, icon = STATUS_ICON.get(status, (_C.DIM, " "))
        padded = label.ljust(col_w)
        print(f"  {_c(colour, icon)}  {_c(_C.DIM, padded)} {value}")
