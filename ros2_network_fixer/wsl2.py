"""
wsl2.py — WSL2 network mode detection and configuration.

Guides users through switching from NAT to mirrored networking,
which resolves multicast-based DDS discovery failures between
WSL2 and the Windows host.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Optional

from .platform_utils import EnvironmentInfo, _run
from . import ui


# WSL config path on the Windows host (accessed from WSL via /mnt/c/Users/...)
WSLCONFIG_FILENAME = ".wslconfig"

# The Hyper-V VM GUID for the WSL2 VM (fixed across Windows installs)
WSL2_VM_GUID = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_windows_userprofile() -> Optional[Path]:
    """
    Resolve the Windows %UserProfile% directory from within WSL2.
    Returns a WSL2 path like /mnt/c/Users/Alice or None if not detectable.
    """
    # Try wslvar (available in recent WSL2 builds)
    rc, out, _ = _run(["wslvar", "USERPROFILE"])
    if rc == 0 and out:
        # Convert Windows path to WSL mount: C:\Users\Alice → /mnt/c/Users/Alice
        win_path = out.strip()
        m = re.match(r"([A-Za-z]):\\(.*)", win_path)
        if m:
            drive = m.group(1).lower()
            rest = m.group(2).replace("\\", "/")
            return Path(f"/mnt/{drive}/{rest}")

    # Fallback: look for /mnt/c/Users/<username>
    try:
        users_dir = Path("/mnt/c/Users")
        if users_dir.exists():
            # Prefer the directory that isn't Public/Default
            candidates = [
                d for d in users_dir.iterdir()
                if d.is_dir() and d.name not in ("Public", "Default", "All Users")
            ]
            if len(candidates) == 1:
                return candidates[0]
            # Try matching current Linux username
            linux_user = os.environ.get("USER", "")
            for c in candidates:
                if c.name.lower() == linux_user.lower():
                    return c
    except (OSError, PermissionError):
        pass

    return None


def _read_wslconfig(wslconfig_path: Path) -> str:
    try:
        return wslconfig_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_wsl_networking_mode(content: str) -> Optional[str]:
    m = re.search(r"^\s*networkingMode\s*=\s*(\S+)", content, re.MULTILINE | re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return None


def _set_mirrored_in_wslconfig(content: str) -> str:
    """
    Insert or update networkingMode=mirrored in the [wsl2] section.
    Preserves all other content.
    """
    if not content.strip():
        return "[wsl2]\nnetworkingMode=mirrored\n"

    # If [wsl2] section exists and has networkingMode, replace it
    if re.search(r"^\s*networkingMode\s*=", content, re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"(?m)^\s*networkingMode\s*=.*$",
            "networkingMode=mirrored",
            content,
            flags=re.IGNORECASE,
        )

    # If [wsl2] section exists but no networkingMode, insert after [wsl2]
    if re.search(r"^\[wsl2\]", content, re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"(?mi)^\[wsl2\]",
            "[wsl2]\nnetworkingMode=mirrored",
            content,
        )

    # No [wsl2] section at all — prepend it
    return "[wsl2]\nnetworkingMode=mirrored\n\n" + content


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_wsl2_status(env: EnvironmentInfo) -> dict:
    """Return a dict with WSL2 status details."""
    result = {
        "in_wsl2": env.in_wsl2,
        "networking_mode": env.wsl2_networking_mode,
        "windows_userprofile": None,
        "wslconfig_path": None,
        "wslconfig_content": "",
        "wslconfig_mode": None,
    }

    if not env.in_wsl2:
        return result

    win_up = _get_windows_userprofile()
    result["windows_userprofile"] = win_up

    if win_up:
        wslconfig = win_up / WSLCONFIG_FILENAME
        result["wslconfig_path"] = wslconfig
        content = _read_wslconfig(wslconfig)
        result["wslconfig_content"] = content
        result["wslconfig_mode"] = _parse_wsl_networking_mode(content)

    return result


# ---------------------------------------------------------------------------
# Fixes
# ---------------------------------------------------------------------------

def fix_wsl2_networking(env: EnvironmentInfo, auto_apply: bool = False) -> bool:
    """
    Guide the user through enabling mirrored networking mode in WSL2.
    If auto_apply=True and we can write to .wslconfig, do it automatically.
    """
    ui.section("WSL2 Network Configuration")

    if not env.in_wsl2:
        ui.info("Not running inside WSL2 — skipping WSL2 network fix.")
        return True

    status = detect_wsl2_status(env)

    # Report current state
    current_mode = status["wslconfig_mode"] or status["networking_mode"] or "nat"
    ui.kv("Current networking mode", current_mode,
          ok_val=(current_mode == "mirrored"))

    if current_mode == "mirrored":
        ui.ok("WSL2 is already in mirrored networking mode.")
        ui.info("Multicast traffic should flow between WSL2 and Windows host.")
        _print_hyperv_firewall_reminder()
        return True

    ui.warn("WSL2 is in NAT mode — multicast is blocked between WSL2 and Windows host.")
    ui.nl()

    # Show the fix
    wslconfig_path = status["wslconfig_path"]
    win_up = status["windows_userprofile"]

    if not win_up:
        ui.warn("Could not locate Windows %UserProfile% from WSL2.")
        _print_wslconfig_manual(None)
        return False

    wslconfig_display = str(wslconfig_path).replace("/mnt/c/", "C:\\").replace("/", "\\")
    ui.info(f"Will modify: {wslconfig_display}")

    # Preview the change
    old_content = status["wslconfig_content"]
    new_content = _set_mirrored_in_wslconfig(old_content)

    ui.section("Proposed .wslconfig change")
    ui.code_block(new_content.splitlines(), label=WSLCONFIG_FILENAME)

    applied = False

    if auto_apply:
        applied = _write_wslconfig(wslconfig_path, new_content)
    else:
        if ui.confirm("Apply this change to .wslconfig now?", default=True):
            applied = _write_wslconfig(wslconfig_path, new_content)
        else:
            ui.info("Skipped — apply manually (see below).")

    if applied:
        ui.ok(f".wslconfig updated: {wslconfig_display}")
        _print_wsl_restart_instructions()
    else:
        _print_wslconfig_manual(wslconfig_path)

    _print_hyperv_firewall_reminder()
    return applied


def _write_wslconfig(wslconfig_path: Path, content: str) -> bool:
    try:
        wslconfig_path.write_text(content, encoding="utf-8")
        return True
    except (OSError, PermissionError) as e:
        ui.error(f"Could not write {wslconfig_path}: {e}")
        return False


def _print_wsl_restart_instructions() -> None:
    ui.section("Restart WSL2 to Apply Changes")
    ui.info("Run the following in a Windows PowerShell / Command Prompt window:")
    ui.cmd_block("wsl --shutdown")
    ui.info("Then reopen your WSL2 terminal. The new networking mode will be active.")
    ui.warn("All running WSL2 instances will be stopped by 'wsl --shutdown'.")


def _print_wslconfig_manual(wslconfig_path: Optional[Path]) -> None:
    """Print manual instructions if automatic config fails."""
    if wslconfig_path:
        win_path = str(wslconfig_path).replace("/mnt/c", "C:").replace("/", "\\")
    else:
        win_path = r"%UserProfile%\.wslconfig"

    ui.section("Manual WSL2 Configuration")
    ui.info(f"Edit (or create) the file: {win_path}")
    ui.info("Add or update the [wsl2] section:")
    ui.code_block([
        "[wsl2]",
        "networkingMode=mirrored",
    ], label=".wslconfig")
    ui.info("Then restart WSL2:")
    ui.cmd_block("wsl --shutdown")


def _print_hyperv_firewall_reminder() -> None:
    """Remind users to also set the Hyper-V firewall rule."""
    ui.section("Windows Firewall — Hyper-V Rule")
    ui.info("Even with mirrored networking, the Windows Hyper-V firewall may block")
    ui.info("inbound traffic from WSL2. Run this in PowerShell as Administrator:")
    ui.cmd_block(
        f"Set-NetFirewallHyperVVMSetting -Name '{WSL2_VM_GUID}' -DefaultInboundAction Allow"
    )
    ui.detail("This command is only available on Windows 11 build 22621 or later.")
    ui.detail("If it fails, use '--fix firewall' for standard firewall rules instead.")


# ---------------------------------------------------------------------------
# Docker note
# ---------------------------------------------------------------------------

def print_docker_note(env: EnvironmentInfo) -> None:
    """Print a note about Docker networking for ROS 2."""
    if not env.in_docker:
        return

    ui.section("Docker Networking Note")
    ui.warn("Running inside a Docker container.")
    ui.info("ROS 2 DDS multicast may fail unless the container is on the host network.")
    ui.info("To use host networking (Linux only):")
    ui.cmd_block("docker run --network host <your-ros2-image>")
    ui.info("To use a custom bridge network with multicast support:")
    ui.code_block([
        "docker network create \\",
        "  --driver bridge \\",
        "  --opt com.docker.network.driver.mtu=1500 \\",
        "  ros2_net",
        "",
        "docker run --network ros2_net <your-ros2-image>",
    ], label="Docker bridge setup")
    ui.info("Alternatively, configure Discovery Server mode (--fix discovery) which")
    ui.info("works across containers without requiring host networking.")
