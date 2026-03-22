"""
cli.py — Main command-line interface for ros2_network_fixer.

Usage:
  ros2_network_fixer                     # Interactive wizard
  ros2_network_fixer --diagnose          # Run diagnostics only
  ros2_network_fixer --fix all           # Apply all fixes
  ros2_network_fixer --fix discovery     # Discovery server only
  ros2_network_fixer --fix firewall      # Firewall rules only
  ros2_network_fixer --fix wsl2          # WSL2 networking mode
  ros2_network_fixer --info              # Print environment info
  ros2_network_fixer --server-start      # Start discovery server
  ros2_network_fixer --server-stop       # Stop discovery server
  ros2_network_fixer --server-ip <IP>    # Override server IP
  ros2_network_fixer --server-port <N>   # Override server port
  ros2_network_fixer --yes               # Non-interactive (auto-confirm)
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import ui
from .platform_utils import detect_environment, EnvironmentInfo, ros2_sourced
from .diagnostics import run_diagnostics, print_report
from .discovery import (
    setup_discovery_server,
    stop_discovery_server,
    show_discovery_status,
    DISCOVERY_SERVER_PORT,
)
from .firewall import fix_firewall, print_firewall_info
from .wsl2 import fix_wsl2_networking, detect_wsl2_status, print_docker_note


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ros2_network_fixer",
        description=(
            "ROS 2 Cross-Platform Network Fixer — automates DDS discovery,\n"
            "firewall rules, and WSL2/Docker network configuration."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap_epilog(),
    )

    # Primary mode flags (mutually exclusive)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--diagnose", "-d",
        action="store_true",
        help="Run diagnostics and show network health report.",
    )
    mode.add_argument(
        "--fix", "-f",
        metavar="TARGET",
        choices=["all", "discovery", "firewall", "wsl2"],
        help=(
            "Apply a specific fix: all | discovery | firewall | wsl2. "
            "'all' applies every available fix for your environment."
        ),
    )
    mode.add_argument(
        "--info", "-i",
        action="store_true",
        help="Print detected environment information and exit.",
    )
    mode.add_argument(
        "--server-stop",
        action="store_true",
        help="Stop a previously started discovery server.",
    )

    # Options
    p.add_argument(
        "--server-start",
        action="store_true",
        help="Start a Fast DDS Discovery Server after configuring it.",
    )
    p.add_argument(
        "--server-ip",
        metavar="IP",
        help="IP address for the Discovery Server (default: auto-detected).",
    )
    p.add_argument(
        "--server-port",
        metavar="PORT",
        type=int,
        default=DISCOVERY_SERVER_PORT,
        help=f"Port for the Discovery Server (default: {DISCOVERY_SERVER_PORT}).",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Non-interactive mode — auto-confirm all prompts.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show additional diagnostic detail.",
    )
    p.add_argument(
        "--version",
        action="version",
        version="ros2_network_fixer 1.0.0",
    )

    return p


def textwrap_epilog() -> str:
    return """
examples:
  ros2_network_fixer                       Run interactive wizard
  ros2_network_fixer --diagnose            Check network health
  ros2_network_fixer --fix all             Apply all fixes
  ros2_network_fixer --fix discovery       Set up Discovery Server
  ros2_network_fixer --fix firewall        Open DDS firewall ports
  ros2_network_fixer --fix wsl2            Fix WSL2 networking mode
  ros2_network_fixer --fix all --yes       Non-interactive fix-all
  ros2_network_fixer --server-start        Start discovery server process
  ros2_network_fixer --server-stop         Stop discovery server process
  ros2_network_fixer --server-ip 192.168.1.10 --fix discovery
"""


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------

def _print_env_info(env: EnvironmentInfo) -> None:
    ui.section("Environment Information")
    rows = [
        ("OS",            f"{env.os_type} {env.os_version[:40]}",    "info"),
        ("Python",        env.python_version,                         "info"),
        ("Hostname",      env.hostname,                               "info"),
        ("ROS 2 distro",  env.ros2_distro or "not sourced",
         "ok" if env.ros2_distro else "warn"),
        ("ROS 2 home",    str(env.ros2_home) if env.ros2_home else "—", "info"),
        ("In WSL2",       str(env.in_wsl2),
         "warn" if env.in_wsl2 else "info"),
        ("WSL2 net mode", env.wsl2_networking_mode or "N/A",
         "ok" if env.wsl2_networking_mode == "mirrored" else
         "warn" if env.in_wsl2 else "info"),
        ("In Docker",     str(env.in_docker),
         "warn" if env.in_docker else "info"),
        ("sudo",          "available" if env.has_sudo else "not available",
         "ok" if env.has_sudo else "warn"),
        ("PowerShell",    "available" if env.has_powershell else "not available",
         "ok" if env.has_powershell else "info"),
    ]
    ui.summary_table(rows)

    if env.interfaces:
        ui.nl()
        ui.section("Network Interfaces")
        for iface in env.interfaces:
            flag = "loopback" if iface.is_loopback else "OK"
            status = "info" if iface.is_loopback else "ok"
            ui.kv(f"{iface.name} ({iface.ip})", flag, ok_val=not iface.is_loopback)

    show_discovery_status()
    print_firewall_info(env)


def _do_fix_discovery(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return setup_discovery_server(
        env,
        server_ip=args.server_ip,
        server_port=args.server_port,
        start_server=args.server_start,
    )


def _do_fix_firewall(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return fix_firewall(env)


def _do_fix_wsl2(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return fix_wsl2_networking(env, auto_apply=args.yes)


def _do_fix_all(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    results = []
    steps = []

    # Build ordered step list based on environment
    if env.in_wsl2:
        steps.append(("WSL2 Networking Mode", lambda: _do_fix_wsl2(env, args)))
    steps.append(("Firewall Rules", lambda: _do_fix_firewall(env, args)))
    steps.append(("Discovery Server Configuration", lambda: _do_fix_discovery(env, args)))

    total = len(steps)
    for i, (label, fn) in enumerate(steps, 1):
        ui.step(f"Step {i}/{total} — {label}")
        results.append(fn())

    # Docker note (informational only)
    if env.in_docker:
        print_docker_note(env)

    return all(results)



# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def _run_wizard(env: EnvironmentInfo, args: argparse.Namespace) -> int:
    """
    Interactive guided mode — run diagnostics, then offer targeted fixes.
    """
    ui.section("Running Diagnostics")
    with ui.spinner("Probing network stack and DDS environment..."):
        report = run_diagnostics(env, verbose=args.verbose)
    print_report(report)

    if report.all_passed:
        ui.ok("Your ROS 2 network configuration looks healthy!")
        ui.info("If you still have discovery issues, try '--fix discovery' "
                "to use Discovery Server mode as a more reliable alternative to multicast.")
        return 0

    # Offer fixes
    ui.nl()
    ui.section("Recommended Actions")

    failed_names = {r.name for r in report.failed}
    fixes_to_offer = []

    if "WSL2 networking mode" in failed_names and env.in_wsl2:
        fixes_to_offer.append(("Fix WSL2 networking mode (switch to mirrored)", "wsl2"))
    if any("multicast" in n.lower() or "firewall" in n.lower() or "port" in n.lower()
           for n in failed_names):
        fixes_to_offer.append(("Open firewall ports for DDS / multicast", "firewall"))
    fixes_to_offer.append(("Configure Discovery Server mode (recommended for all setups)", "discovery"))
    fixes_to_offer.append(("Apply all of the above", "all"))
    fixes_to_offer.append(("Exit without fixing", None))

    options = [label for label, _ in fixes_to_offer]
    choice_idx = ui.choose("Which fix would you like to apply?", options)
    _, fix_key = fixes_to_offer[choice_idx]

    if fix_key is None:
        ui.info("No fix applied. Goodbye.")
        return 0

    args.fix = fix_key
    return _run_fix(env, args)


# ---------------------------------------------------------------------------
# Fix dispatcher
# ---------------------------------------------------------------------------

def _run_fix(env: EnvironmentInfo, args: argparse.Namespace) -> int:
    target = args.fix
    if target == "all":
        ok = _do_fix_all(env, args)
    elif target == "discovery":
        ok = _do_fix_discovery(env, args)
    elif target == "firewall":
        ok = _do_fix_firewall(env, args)
    elif target == "wsl2":
        ok = _do_fix_wsl2(env, args)
    else:
        ui.error(f"Unknown fix target: {target}")
        return 1

    ui.nl()
    if ok:
        ui.ok("Fix applied. Run '--diagnose' to verify the result.")
    else:
        ui.warn("Some steps encountered issues — review the output above.")
        ui.info("If automatic fixes failed, use '--diagnose' and follow the manual instructions.")

    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    ui.banner()

    # Detect environment (always)
    with ui.spinner("Detecting environment..."):
        env = detect_environment()

    # Dispatch
    if args.server_stop:
        stop_discovery_server()
        return 0

    if args.info:
        _print_env_info(env)
        return 0

    if args.diagnose:
        ui.section("Running Diagnostics")
        with ui.spinner("Probing network stack and DDS environment..."):
            report = run_diagnostics(env, verbose=args.verbose)
        print_report(report)
        return 0 if report.all_passed else 1

    if args.fix:
        return _run_fix(env, args)

    # Default: interactive wizard
    return _run_wizard(env, args)


if __name__ == "__main__":
    sys.exit(main())
