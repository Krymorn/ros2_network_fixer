"""
diagnostics.py — Network diagnostic checks for ROS 2 DDS.

Tests multicast reachability, lists active nodes/topics,
checks firewall and port accessibility, QoS mismatches, RMW mismatches,
interface binding issues, domain ID conflicts, and security posture.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from .platform_utils import EnvironmentInfo, _run
from . import ui

# Lazy imports to avoid circular dependencies
def _get_security_checks(env):
    try:
        from .security import check_security_posture
        return check_security_posture(env)
    except Exception:
        return []

def _get_domain_id_checks(env):
    try:
        from .domain_id import check_domain_id
        return check_domain_id(env)
    except Exception:
        return []

def _get_rmw_checks(env):
    try:
        from .rmw import check_rmw
        return check_rmw(env)
    except Exception:
        return []

def _get_interface_checks(env):
    try:
        from .interfaces import check_interface_binding
        return check_interface_binding(env)
    except Exception:
        return []

def _get_qos_checks(env):
    try:
        from .qos import check_qos_mismatches
        return check_qos_mismatches(env)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    detail: Optional[str] = None
    fix_hint: Optional[str] = None


@dataclass
class DiagReport:
    results: list[CheckResult] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_ros2_sourced(env: EnvironmentInfo) -> CheckResult:
    if env.ros2_distro:
        return CheckResult(
            name="ROS 2 environment",
            passed=True,
            message=f"ROS 2 '{env.ros2_distro}' is sourced.",
        )
    return CheckResult(
        name="ROS 2 environment",
        passed=False,
        message="ROS 2 environment not sourced (ROS_DISTRO not set).",
        fix_hint=(
            "Source your ROS 2 setup file:\n"
            "  Linux/macOS: source /opt/ros/<distro>/setup.bash\n"
            "  Windows:     C:\\opt\\ros\\<distro>\\setup.bat"
        ),
    )


def _check_ros2_binary(env: EnvironmentInfo) -> CheckResult:
    ros2 = shutil.which("ros2")
    if ros2:
        rc, out, _ = _run(["ros2", "--version"])
        ver = out if rc == 0 else "(version unknown)"
        return CheckResult(
            name="ros2 CLI",
            passed=True,
            message=f"ros2 binary found: {ver}",
            detail=ros2,
        )
    return CheckResult(
        name="ros2 CLI",
        passed=False,
        message="ros2 binary not found in PATH.",
        fix_hint="Ensure ROS 2 is installed and its setup file is sourced.",
    )


def _check_multicast_send_recv(env: EnvironmentInfo, timeout: int = 3) -> CheckResult:
    """
    Test multicast by launching ros2 multicast receive in background,
    then sending a packet, and checking if it was received.
    Falls back to a raw socket test when ros2 is not available.
    """
    # Try raw UDP multicast test first (works even without ros2 CLI)
    MC_GROUP = "239.255.0.1"
    MC_PORT = 49152

    received = False

    def _receiver():
        nonlocal received
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.settimeout(timeout)
            s.bind(("", MC_PORT))
            import struct
            mreq = struct.pack("4sL", socket.inet_aton(MC_GROUP), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            data, _ = s.recvfrom(1024)
            if data == b"ros2_netfixer_probe":
                received = True
            s.close()
        except Exception:
            pass

    import threading
    t = threading.Thread(target=_receiver, daemon=True)
    t.start()
    time.sleep(0.2)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.sendto(b"ros2_netfixer_probe", (MC_GROUP, MC_PORT))
        s.close()
    except Exception:
        pass

    t.join(timeout + 0.5)

    if received:
        return CheckResult(
            name="UDP multicast (loopback)",
            passed=True,
            message="Multicast self-test passed (loopback).",
            detail=f"Group {MC_GROUP}:{MC_PORT}",
        )
    return CheckResult(
        name="UDP multicast (loopback)",
        passed=False,
        message="Multicast loopback probe failed.",
        detail=(
            "Packets sent to the multicast group were not received locally. "
            "This indicates the network stack or firewall is blocking multicast."
        ),
        fix_hint=(
            "Use Discovery Server mode (recommended) or:\n"
            "  Linux:   sudo ufw allow proto udp to/from 224.0.0.0/4\n"
            "  Windows: run this tool's firewall fix with --fix firewall"
        ),
    )


def _check_dds_ports(env: EnvironmentInfo) -> CheckResult:
    """Check that DDS default ports are not already blocked by binding."""
    DDS_PORTS = [7400, 7401, 11811, 11812]
    blocked = []
    for port in DDS_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.bind(("0.0.0.0", port))
            s.close()
        except OSError:
            blocked.append(port)

    if not blocked:
        return CheckResult(
            name="DDS port availability",
            passed=True,
            message=f"DDS ports available: {DDS_PORTS}",
        )
    return CheckResult(
        name="DDS port availability",
        passed=False,
        message=f"DDS ports in use or blocked: {blocked}",
        fix_hint=(
            "Another DDS instance may be running, or a firewall is blocking these ports.\n"
            "Stop other ROS 2 processes or use 'ROS_DOMAIN_ID' to switch domain."
        ),
    )


def _check_ros2_nodes(env: EnvironmentInfo) -> tuple[CheckResult, list[str]]:
    if not shutil.which("ros2"):
        return CheckResult(
            name="ROS 2 node discovery",
            passed=False,
            message="Cannot check: ros2 CLI not found.",
        ), []

    rc, out, err = _run(["ros2", "node", "list"], timeout=8)
    if rc == 0:
        nodes = [n.strip() for n in out.splitlines() if n.strip()]
        if nodes:
            return CheckResult(
                name="ROS 2 node discovery",
                passed=True,
                message=f"Discovered {len(nodes)} node(s).",
            ), nodes
        return CheckResult(
            name="ROS 2 node discovery",
            passed=True,
            message="No nodes currently running (network discovery OK).",
        ), []

    return CheckResult(
        name="ROS 2 node discovery",
        passed=False,
        message="ros2 node list failed — DDS discovery may be broken.",
        detail=err,
        fix_hint="Run 'ros2_network_fixer --fix all' to repair DDS configuration.",
    ), []


def _check_ros2_topics(env: EnvironmentInfo) -> tuple[CheckResult, list[str]]:
    if not shutil.which("ros2"):
        return CheckResult(
            name="ROS 2 topic list",
            passed=False,
            message="Cannot check: ros2 CLI not found.",
        ), []

    rc, out, err = _run(["ros2", "topic", "list"], timeout=8)
    if rc == 0:
        topics = [t.strip() for t in out.splitlines() if t.strip()]
        return CheckResult(
            name="ROS 2 topic list",
            passed=True,
            message=f"Topic discovery OK — {len(topics)} topic(s) visible.",
        ), topics
    return CheckResult(
        name="ROS 2 topic list",
        passed=False,
        message="ros2 topic list failed.",
        detail=err,
    ), []


def _check_discovery_server_env(env: EnvironmentInfo) -> CheckResult:
    ds = os.environ.get("ROS_DISCOVERY_SERVER", "")
    if ds:
        return CheckResult(
            name="Discovery Server env",
            passed=True,
            message=f"ROS_DISCOVERY_SERVER = {ds}",
        )
    return CheckResult(
        name="Discovery Server env",
        passed=False,
        message="ROS_DISCOVERY_SERVER is not set (multicast mode).",
        fix_hint="Run '--fix discovery' to configure Discovery Server mode.",
    )


def _check_fastdds_config(env: EnvironmentInfo) -> CheckResult:
    cfg = os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE", "")
    if cfg and os.path.isfile(cfg):
        return CheckResult(
            name="Fast DDS XML config",
            passed=True,
            message=f"Profile file in use: {cfg}",
        )
    return CheckResult(
        name="Fast DDS XML config",
        passed=False,
        message="No custom Fast DDS XML profile active.",
        fix_hint="Run '--fix discovery' to generate and activate a Fast DDS config.",
    )


def _check_wsl2_networking(env: EnvironmentInfo) -> Optional[CheckResult]:
    if not env.in_wsl2:
        return None
    if env.wsl2_networking_mode == "mirrored":
        return CheckResult(
            name="WSL2 networking mode",
            passed=True,
            message="WSL2 is in mirrored networking mode — multicast should work.",
        )
    return CheckResult(
        name="WSL2 networking mode",
        passed=False,
        message=f"WSL2 networking mode: {env.wsl2_networking_mode or 'NAT'} — multicast is BLOCKED.",
        fix_hint="Run '--fix wsl2' to switch to mirrored mode.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_diagnostics(env: EnvironmentInfo, verbose: bool = False) -> DiagReport:
    """Run all diagnostic checks and return a DiagReport."""
    report = DiagReport()

    checks_and_extras = [
        _check_ros2_sourced(env),
        _check_ros2_binary(env),
        _check_multicast_send_recv(env),
        _check_dds_ports(env),
        _check_discovery_server_env(env),
        _check_fastdds_config(env),
    ]

    # WSL2-specific
    wsl_check = _check_wsl2_networking(env)
    if wsl_check:
        checks_and_extras.append(wsl_check)

    report.results.extend(checks_and_extras)

    # Node/topic checks (separate because they return extra data)
    node_check, nodes = _check_ros2_nodes(env)
    topic_check, topics = _check_ros2_topics(env)
    report.results.append(node_check)
    report.results.append(topic_check)
    report.nodes = nodes
    report.topics = topics

    # Security posture checks (always included — insecure default is worth surfacing)
    report.results.extend(_get_security_checks(env))

    # Domain ID collision risk
    report.results.extend(_get_domain_id_checks(env))

    # RMW implementation and mismatch risk
    report.results.extend(_get_rmw_checks(env))

    # Network interface binding (multi-interface ambiguity)
    report.results.extend(_get_interface_checks(env))

    # QoS mismatch detection (only if topics are active)
    report.results.extend(_get_qos_checks(env))

    return report


def print_report(report: DiagReport) -> None:
    """Pretty-print a DiagReport to the terminal."""
    ui.section("Diagnostic Results")

    for r in report.results:
        if r.passed:
            ui.ok(r.name + " — " + r.message)
        else:
            ui.error(r.name + " — " + r.message)
        if r.detail:
            ui.detail(r.detail)
        if not r.passed and r.fix_hint:
            ui.detail("Fix: " + r.fix_hint.replace("\n", "\n           "))

    ui.hr()

    if report.nodes:
        ui.section("Discovered Nodes")
        for n in report.nodes:
            ui.info(n)

    if report.topics:
        ui.section("Discovered Topics")
        for t in report.topics[:20]:
            ui.info(t)
        if len(report.topics) > 20:
            ui.detail(f"… and {len(report.topics) - 20} more")

    ui.nl()
    if report.all_passed:
        ui.ok("All checks passed — your ROS 2 network appears healthy.")
    else:
        failed_names = [r.name for r in report.failed]
        ui.warn(f"{len(report.failed)} check(s) failed: {', '.join(failed_names)}")
        ui.info("Run with '--fix all' to attempt automatic repair.")
