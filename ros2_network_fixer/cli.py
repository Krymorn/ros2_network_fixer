"""
cli.py — Main command-line interface for ros2_network_fixer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import ui
from .platform_utils import detect_environment, EnvironmentInfo
from .diagnostics import run_diagnostics, print_report
from .discovery import (
    setup_discovery_server,
    stop_discovery_server,
    show_discovery_status,
    DISCOVERY_SERVER_PORT,
)
from .firewall import fix_firewall, print_firewall_info
from .wsl2 import fix_wsl2_networking, print_docker_note
from .security import setup_security, print_security_status, DEFAULT_KEYSTORE
from .domain_id import setup_domain_id, get_current_domain_id
from .rmw import setup_rmw, print_rmw_info
from .interfaces import setup_interface_binding, print_interface_info
from .multihost import setup_multihost, test_peer_connectivity
from .qos import run_qos_check
from .export import run_export, print_doctor_summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ros2_network_fixer",
        description=(
            "ROS 2 Cross-Platform Network Fixer\n"
            "Automates DDS discovery, firewall, security, QoS, RMW, and\n"
            "multi-machine configuration across all platforms and distros."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(),
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--diagnose", "-d", action="store_true",
        help="Run full diagnostic report — no changes made.")
    mode.add_argument("--fix", "-f", metavar="TARGET",
        choices=["all","discovery","firewall","wsl2","security",
                 "domain-id","rmw","interfaces","multihost"],
        help="Apply a fix: all|discovery|firewall|wsl2|security|domain-id|rmw|interfaces|multihost")
    mode.add_argument("--info", "-i", action="store_true",
        help="Print full environment summary.")
    mode.add_argument("--export", action="store_true",
        help="Export .env, VS Code settings, Docker Compose, and firewall scripts.")
    mode.add_argument("--qos-check", action="store_true",
        help="Check for QoS mismatches across all active topics.")
    mode.add_argument("--doctor", action="store_true",
        help="Run and parse ros2 doctor --report.")
    mode.add_argument("--test-peer", metavar="IP",
        help="Test connectivity to a remote ROS 2 host.")
    mode.add_argument("--server-stop", action="store_true",
        help="Stop a previously started Discovery Server.")

    # Discovery Server
    ds = p.add_argument_group("Discovery Server")
    ds.add_argument("--server-start", action="store_true",
        help="Start a Fast DDS Discovery Server after configuring.")
    ds.add_argument("--server-ip", metavar="IP",
        help="IP address for Discovery Server (default: auto-detected).")
    ds.add_argument("--server-port", metavar="PORT", type=int,
        default=DISCOVERY_SERVER_PORT,
        help=f"Port for Discovery Server (default: {DISCOVERY_SERVER_PORT}).")

    # Security
    sec = p.add_argument_group("Security (SROS2)")
    sec.add_argument("--security-strategy", metavar="STRATEGY",
        choices=["Enforce","Permissive"], default="Enforce",
        help="Enforce: reject unsigned nodes. Permissive: allow but log. Default: Enforce.")
    sec.add_argument("--security-keystore", metavar="PATH", default=None,
        help=f"Path for SROS2 keystore (default: {DEFAULT_KEYSTORE}).")
    sec.add_argument("--security-enclaves", metavar="ENCLAVES", default=None,
        help="Comma-separated enclave paths, e.g. '/talker,/listener'.")

    # Domain ID
    dom = p.add_argument_group("Domain ID")
    dom.add_argument("--domain-id", metavar="N", type=int, default=None,
        help="ROS_DOMAIN_ID to configure (0-101). Suggests one if omitted.")

    # RMW
    rmw_g = p.add_argument_group("RMW / Middleware")
    rmw_g.add_argument("--rmw", metavar="IMPL", default=None,
        help="RMW implementation to configure, e.g. rmw_cyclonedds_cpp.")

    # Interfaces
    iface_g = p.add_argument_group("Interface Binding")
    iface_g.add_argument("--interface", metavar="NAME", default=None,
        help="Network interface to bind DDS to, e.g. eth0 or wlan0.")

    # Export
    exp = p.add_argument_group("Export")
    exp.add_argument("--export-dir", metavar="PATH", default=None,
        help="Directory for exported files (default: ~/.ros2_network_fixer).")
    exp.add_argument("--docker-image", metavar="IMAGE", default=None,
        help="Docker image for docker-compose.yml (default: ros:<distro>).")
    exp.add_argument("--docker-services", metavar="NAMES", default=None,
        help="Comma-separated service names for docker-compose.yml.")
    exp.add_argument("--docker-host-network", action="store_true",
        help="Use Docker host networking in docker-compose.yml.")

    # General
    p.add_argument("--yes", "-y", action="store_true",
        help="Non-interactive: auto-confirm all prompts.")
    p.add_argument("--verbose", "-v", action="store_true",
        help="Show additional diagnostic detail.")
    p.add_argument("--version", action="version", version="ros2_network_fixer 1.1.0")

    return p


def _epilog() -> str:
    return """
examples:
  ros2_network_fixer                              Interactive wizard
  ros2_network_fixer --diagnose                   Full health report
  ros2_network_fixer --info                       Environment summary

  ros2_network_fixer --fix all                    Apply all fixes
  ros2_network_fixer --fix all --yes              Non-interactive
  ros2_network_fixer --fix discovery              Discovery Server
  ros2_network_fixer --fix firewall               Firewall rules
  ros2_network_fixer --fix wsl2                   WSL2 NAT fix
  ros2_network_fixer --fix security               SROS2 encryption
  ros2_network_fixer --fix domain-id              Unique domain ID
  ros2_network_fixer --fix rmw                    RMW config
  ros2_network_fixer --fix interfaces             Interface pinning
  ros2_network_fixer --fix multihost              Multi-machine wizard

  ros2_network_fixer --export                     .env, VS Code, Docker
  ros2_network_fixer --qos-check                  QoS mismatch scan
  ros2_network_fixer --doctor                     ros2 doctor report
  ros2_network_fixer --test-peer 192.168.1.50     Remote host test

  ros2_network_fixer --fix discovery --server-ip 192.168.1.10
  ros2_network_fixer --fix security --security-strategy Permissive
  ros2_network_fixer --fix rmw --rmw rmw_cyclonedds_cpp
  ros2_network_fixer --fix interfaces --interface eth0
  ros2_network_fixer --fix domain-id --domain-id 42
"""


# ---------------------------------------------------------------------------
# --info
# ---------------------------------------------------------------------------

def _print_env_info(env: EnvironmentInfo) -> None:
    ui.section("Environment Information")

    wsl_label = "no"
    if env.in_wsl2:
        wsl_label = f"yes (WSL2, {env.wsl2_networking_mode or 'NAT'} mode)"
    elif env.wsl_version == 1:
        wsl_label = "yes (WSL1 — upgrade to WSL2 recommended)"

    rows = [
        ("OS",            f"{env.os_type}  {env.os_version[:50]}",         "info"),
        ("Python",        env.python_version,                                "info"),
        ("Hostname",      env.hostname,                                      "info"),
        ("ROS 2 distro",  env.ros2_distro or "not sourced",
         "ok" if env.ros2_distro else "warn"),
        ("ROS 2 home",    str(env.ros2_home) if env.ros2_home else "—",     "info"),
        ("ROS_DOMAIN_ID", str(env.domain_id),
         "ok" if env.domain_id != 0 else "warn"),
        ("WSL",           wsl_label,
         "warn" if env.in_wsl2 and env.wsl2_networking_mode != "mirrored"
         else "info"),
        ("In Docker",     "yes" if env.in_docker else "no",
         "warn" if env.in_docker else "info"),
        ("sudo",          "available" if env.has_sudo else "not available",
         "ok" if env.has_sudo else "warn"),
        ("PowerShell",    "available" if env.has_powershell else "not available",
         "ok" if env.has_powershell else "info"),
    ]
    ui.summary_table(rows)

    print_interface_info(env)
    print_rmw_info(env)
    show_discovery_status()
    print_firewall_info(env)
    print_security_status()


# ---------------------------------------------------------------------------
# Fix handlers
# ---------------------------------------------------------------------------

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


def _do_fix_security(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    keystore = Path(args.security_keystore) if args.security_keystore else None
    enclaves = (
        [e.strip() for e in args.security_enclaves.split(",") if e.strip()]
        if args.security_enclaves else None
    )
    return setup_security(
        env,
        keystore=keystore,
        strategy=args.security_strategy,
        enclaves=enclaves,
        auto_apply=args.yes,
    )


def _do_fix_domain_id(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return setup_domain_id(env, domain_id=args.domain_id, auto_apply=args.yes)


def _do_fix_rmw(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return setup_rmw(
        env,
        target_rmw=args.rmw,
        interface_name=args.interface,
        server_ip=args.server_ip,
        server_port=args.server_port,
        auto_apply=args.yes,
    )


def _do_fix_interfaces(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return setup_interface_binding(
        env,
        interface_name=args.interface,
        auto_apply=args.yes,
    )


def _do_fix_multihost(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    return setup_multihost(
        env,
        server_ip=args.server_ip,
        server_port=args.server_port,
        domain_id=args.domain_id if args.domain_id is not None else get_current_domain_id(),
        auto_apply=args.yes,
    )


def _do_fix_all(env: EnvironmentInfo, args: argparse.Namespace) -> bool:
    results: list[bool] = []
    steps: list[tuple[str, object]] = []

    if env.wsl_version == 1:
        ui.warn("WSL1 detected — upgrade to WSL2 for better networking:")
        ui.cmd_block("wsl --set-version <distro-name> 2")

    if env.in_wsl2:
        steps.append(("WSL2 Networking Mode",     lambda: _do_fix_wsl2(env, args)))

    steps.extend([
        ("Firewall Rules",              lambda: _do_fix_firewall(env, args)),
        ("Discovery Server",            lambda: _do_fix_discovery(env, args)),
        ("Domain ID",                   lambda: _do_fix_domain_id(env, args)),
        ("RMW Configuration",           lambda: _do_fix_rmw(env, args)),
        ("Network Interface Binding",   lambda: _do_fix_interfaces(env, args)),
    ])

    total = len(steps)
    for i, (label, fn) in enumerate(steps, 1):
        ui.step(f"Step {i}/{total} — {label}")
        results.append(fn())
        ui.nl()

    # Security is opt-in — requires per-node cert management
    ui.step(f"Step {total+1}/{total+1} — SROS2 Security (opt-in)")
    if args.yes:
        ui.info("Skipping security in --yes mode. Run '--fix security' separately.")
        ui.info("(Security requires per-node certificate management.)")
    elif ui.confirm("Enable SROS2 encryption and authentication?", default=False):
        results.append(_do_fix_security(env, args))
    else:
        ui.info("Security skipped. Run '--fix security' when ready.")

    if env.in_docker:
        print_docker_note(env)

    return all(results)


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def _run_wizard(env: EnvironmentInfo, args: argparse.Namespace) -> int:
    ui.section("Running Diagnostics")
    with ui.spinner("Probing network stack and DDS environment..."):
        report = run_diagnostics(env, verbose=args.verbose)
    print_report(report)

    if report.all_passed:
        ui.ok("Your ROS 2 network configuration looks healthy!")
        ui.nl()
        ui.info("Optional next steps:")
        ui.detail("  --fix security     Enable SROS2 encryption")
        ui.detail("  --fix domain-id    Set a unique domain ID for shared networks")
        ui.detail("  --fix multihost    Configure multi-machine communication")
        ui.detail("  --export           Generate .env / VS Code / Docker Compose files")
        ui.detail("  --qos-check        Scan for silent QoS mismatches")
        return 0

    ui.nl()
    ui.section("Recommended Actions")

    failed_names = {r.name for r in report.failed}
    fixes: list[tuple[str, str]] = []

    if "WSL2 networking mode" in failed_names and env.in_wsl2:
        fixes.append(("Fix WSL2 NAT mode (switch to mirrored)", "wsl2"))
    if any(k in failed_names for k in ("UDP multicast (loopback)", "DDS port availability")):
        fixes.append(("Fix firewall — open DDS and multicast ports", "firewall"))
    if "Network interface binding" in failed_names:
        fixes.append(("Fix interface binding — pin DDS to correct interface", "interfaces"))
    if "ROS_DOMAIN_ID" in failed_names:
        fixes.append(("Fix domain ID — set a unique ROS_DOMAIN_ID", "domain-id"))
    if any(k in failed_names for k in ("RMW implementation", "RMW mismatch risk")):
        fixes.append(("Fix RMW — configure middleware consistently", "rmw"))
    if "ROS_SECURITY_ENABLE" in failed_names:
        fixes.append(("Enable SROS2 encryption + authentication", "security"))

    fixes.extend([
        ("Set up Discovery Server (recommended for all networks)", "discovery"),
        ("Apply all fixes", "all"),
        ("Export .env / VS Code / Docker Compose files", "_export"),
        ("Exit without changes", None),
    ])

    options = [label for label, _ in fixes]
    idx = ui.choose("Which action would you like to take?", options)
    _, fix_key = fixes[idx]

    if fix_key is None:
        ui.info("No changes made. Goodbye.")
        return 0
    if fix_key == "_export":
        return _run_export(env, args)

    args.fix = fix_key
    return _run_fix(env, args)


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------

def _run_fix(env: EnvironmentInfo, args: argparse.Namespace) -> int:
    DISPATCH = {
        "all":        lambda: _do_fix_all(env, args),
        "discovery":  lambda: _do_fix_discovery(env, args),
        "firewall":   lambda: _do_fix_firewall(env, args),
        "wsl2":       lambda: _do_fix_wsl2(env, args),
        "security":   lambda: _do_fix_security(env, args),
        "domain-id":  lambda: _do_fix_domain_id(env, args),
        "rmw":        lambda: _do_fix_rmw(env, args),
        "interfaces": lambda: _do_fix_interfaces(env, args),
        "multihost":  lambda: _do_fix_multihost(env, args),
    }
    handler = DISPATCH.get(args.fix)
    if not handler:
        ui.error(f"Unknown fix target: {args.fix}")
        return 1
    ok = handler()
    ui.nl()
    if ok:
        ui.ok("Done. Run '--diagnose' to verify.")
    else:
        ui.warn("Some steps had issues — see output above.")
        ui.info("Manual commands have been printed where automatic fixes failed.")
    return 0 if ok else 1


def _run_export(env: EnvironmentInfo, args: argparse.Namespace) -> int:
    distro = env.ros2_distro or "jazzy"
    ros2_image = getattr(args, "docker_image", None) or f"ros:{distro}"
    services_raw = getattr(args, "docker_services", None)
    docker_services = (
        [s.strip() for s in services_raw.split(",") if s.strip()]
        if services_raw else None
    )
    export_dir = Path(args.export_dir) if getattr(args, "export_dir", None) else None
    ok = run_export(
        env,
        output_dir=export_dir,
        ros2_image=ros2_image,
        server_ip=args.server_ip,
        server_port=args.server_port,
        domain_id=args.domain_id if args.domain_id is not None else get_current_domain_id(),
        docker_services=docker_services,
        use_host_network=getattr(args, "docker_host_network", False),
    )
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    ui.banner()

    with ui.spinner("Detecting environment..."):
        env = detect_environment()

    if args.server_stop:
        stop_discovery_server()
        return 0
    if args.test_peer:
        test_peer_connectivity(env, args.test_peer, args.server_port)
        return 0
    if args.doctor:
        print_doctor_summary(env)
        return 0
    if args.qos_check:
        run_qos_check(env)
        return 0
    if args.info:
        _print_env_info(env)
        return 0
    if args.export:
        return _run_export(env, args)
    if args.diagnose:
        ui.section("Running Diagnostics")
        with ui.spinner("Probing network stack and DDS environment..."):
            report = run_diagnostics(env, verbose=args.verbose)
        print_report(report)
        return 0 if report.all_passed else 1
    if args.fix:
        return _run_fix(env, args)

    return _run_wizard(env, args)


if __name__ == "__main__":
    sys.exit(main())
