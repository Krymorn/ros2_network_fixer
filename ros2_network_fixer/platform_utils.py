"""
platform_utils.py — Cross-platform environment detection.

Detects OS, WSL2, Docker, ROS 2 installation, and network interfaces
without assuming any particular host system.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NetworkInterface:
    name: str
    ip: str
    is_loopback: bool = False
    is_multicast_capable: bool = False


@dataclass
class EnvironmentInfo:
    os_type: str                          # "windows", "linux", "macos"
    os_version: str
    in_wsl2: bool
    wsl_version: int                      # 1 or 2; 0 if not WSL
    wsl2_networking_mode: Optional[str]   # "nat", "mirrored", None
    in_docker: bool
    ros2_distro: Optional[str]
    ros2_home: Optional[Path]
    python_version: str
    interfaces: list[NetworkInterface] = field(default_factory=list)
    hostname: str = ""
    has_sudo: bool = False
    has_powershell: bool = False
    rmw_impl: Optional[str] = None        # detected RMW implementation
    domain_id: int = 0                    # current ROS_DOMAIN_ID


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return -1, "", ""


def _detect_os() -> tuple[str, str]:
    system = platform.system().lower()
    version = platform.version()
    if system == "windows":
        return "windows", version
    if system == "darwin":
        return "macos", platform.mac_ver()[0]
    if system == "linux":
        return "linux", version
    return system, version


def _detect_wsl2() -> tuple[bool, int, Optional[str]]:
    """
    Return (in_wsl, wsl_version, networking_mode).

    wsl_version: 0 = not WSL, 1 = WSL1, 2 = WSL2
    networking_mode: "nat" | "mirrored" | None
    """
    if platform.system().lower() != "linux":
        return False, 0, None

    # Check /proc/version for Microsoft kernel string
    try:
        proc_version = Path("/proc/version").read_text().lower()
        if "microsoft" not in proc_version:
            return False, 0, None
    except OSError:
        return False, 0, None

    # Confirm it's actually WSL (not just a Microsoft-built kernel)
    is_wsl = bool(
        os.environ.get("WSL_DISTRO_NAME")
        or os.environ.get("WSL_INTEROP")
        or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    )
    if not is_wsl:
        return False, 0, None

    # Distinguish WSL1 vs WSL2
    # WSL2 runs a real Linux kernel with its own network namespace;
    # WSL1 translates syscalls — it does NOT have /dev/kmsg or a real kernel cmdline.
    # The most reliable signal: WSL2 has a non-empty /run/WSL directory or
    # the kernel version contains "microsoft-standard" (WSL2 kernel).
    wsl_version = 1
    try:
        kern = proc_version
        if "microsoft-standard" in kern or "wsl2" in kern:
            wsl_version = 2
        elif Path("/dev/kmsg").exists():
            # WSL2 has /dev/kmsg; WSL1 does not
            wsl_version = 2
    except Exception:
        wsl_version = 2  # safe default — WSL1 is very rare now

    if wsl_version == 1:
        # WSL1 has different (but simpler) network issues — no NAT layer
        return True, 1, "wsl1"

    # WSL2 networking mode detection
    networking_mode = "nat"  # default for WSL2
    try:
        cmdline = Path("/proc/cmdline").read_text().lower()
        if "mirror" in cmdline:
            networking_mode = "mirrored"
    except OSError:
        pass

    # Secondary heuristic: in mirrored mode the WSL2 IP matches the Windows IP
    # We look for the absence of the default 172.x WSL2 NAT gateway
    if networking_mode == "nat":
        try:
            rc, route_out, _ = _run(["ip", "route"])
            if rc == 0 and "172." not in route_out:
                # No 172.x gateway → likely mirrored or custom networking
                # Check if resolv.conf points to a local address (mirrored sign)
                resolv = Path("/etc/resolv.conf").read_text()
                if re.search(r"nameserver\s+(?!172\.)", resolv):
                    networking_mode = "mirrored"
        except Exception:
            pass

    return True, wsl_version, networking_mode


def _detect_docker() -> bool:
    """Heuristic: /.dockerenv exists, or 'docker' in /proc/1/cgroup."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
        return "docker" in cgroup or "containerd" in cgroup
    except OSError:
        return False


def _detect_ros2() -> tuple[Optional[str], Optional[Path]]:
    """Return (distro_name, ros2_home_path) or (None, None)."""
    # Check environment variable set by sourcing setup.bash
    distro = os.environ.get("ROS_DISTRO")
    ros_root = os.environ.get("AMENT_PREFIX_PATH") or os.environ.get("ROS_ROOT")

    if distro:
        if ros_root:
            # AMENT_PREFIX_PATH may be colon-separated; take first
            first = ros_root.split(os.pathsep)[0]
            return distro, Path(first)
        return distro, None

    # Try to find ros2 executable
    ros2_bin = shutil.which("ros2")
    if ros2_bin:
        rc, out, _ = _run(["ros2", "--version"])
        if rc == 0:
            # "ros2, version X.Y.Z" — distro not always in version string
            return "unknown", Path(ros2_bin).parent.parent

    # Check common install locations
    for d in ["jazzy", "humble", "iron", "rolling", "foxy", "galactic", "eloquent"]:
        for base in [Path("/opt/ros"), Path("C:/opt/ros"), Path("/usr/local/ros")]:
            candidate = base / d
            if candidate.exists():
                return d, candidate

    return None, None


def _detect_interfaces() -> list[NetworkInterface]:
    """Enumerate non-loopback network interfaces with IP addresses."""
    interfaces: list[NetworkInterface] = []

    # Try ip addr (Linux/WSL)
    rc, out, _ = _run(["ip", "-4", "addr", "show"])
    if rc == 0:
        current_name = ""
        for line in out.splitlines():
            m = re.match(r"^\d+:\s+(\S+):", line)
            if m:
                current_name = m.group(1).rstrip("@").split("@")[0]
            m2 = re.match(r"\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m2 and current_name:
                ip = m2.group(1)
                is_lo = current_name.startswith("lo") or ip.startswith("127.")
                interfaces.append(NetworkInterface(
                    name=current_name,
                    ip=ip,
                    is_loopback=is_lo,
                    is_multicast_capable=not is_lo,
                ))
        return interfaces

    # Try ipconfig (Windows)
    rc, out, _ = _run(["ipconfig"])
    if rc == 0:
        current_adapter = "unknown"
        for line in out.splitlines():
            adapter_m = re.match(r"^(\S.*):$", line)
            if adapter_m:
                current_adapter = adapter_m.group(1)
            ip_m = re.search(r"IPv4 Address[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", line)
            if ip_m:
                ip = ip_m.group(1)
                is_lo = ip.startswith("127.")
                interfaces.append(NetworkInterface(
                    name=current_adapter,
                    ip=ip,
                    is_loopback=is_lo,
                    is_multicast_capable=not is_lo,
                ))
        return interfaces

    # Fallback: socket
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        interfaces.append(NetworkInterface(
            name="default",
            ip=ip,
            is_loopback=ip.startswith("127."),
        ))
    except OSError:
        pass

    return interfaces


def _has_sudo() -> bool:
    if platform.system().lower() == "windows":
        return False
    rc, _, _ = _run(["sudo", "-n", "true"])
    return rc == 0


def _has_powershell() -> bool:
    return bool(shutil.which("powershell") or shutil.which("pwsh"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_environment() -> EnvironmentInfo:
    """Probe the current environment and return a populated EnvironmentInfo."""
    import os as _os
    os_type, os_version = _detect_os()
    in_wsl, wsl_version, wsl2_mode = _detect_wsl2()
    in_wsl2 = in_wsl and wsl_version == 2
    in_docker = _detect_docker()
    ros2_distro, ros2_home = _detect_ros2()
    interfaces = _detect_interfaces()

    # ROS_DOMAIN_ID
    try:
        domain_id = int(_os.environ.get("ROS_DOMAIN_ID", "0"))
    except ValueError:
        domain_id = 0

    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = "localhost"

    return EnvironmentInfo(
        os_type=os_type,
        os_version=os_version,
        in_wsl2=in_wsl2,
        wsl_version=wsl_version,
        wsl2_networking_mode=wsl2_mode,
        in_docker=in_docker,
        ros2_distro=ros2_distro,
        ros2_home=ros2_home,
        python_version=sys.version.split()[0],
        interfaces=interfaces,
        hostname=hostname,
        has_sudo=_has_sudo(),
        has_powershell=_has_powershell(),
        rmw_impl=_os.environ.get("RMW_IMPLEMENTATION"),
        domain_id=domain_id,
    )


def get_primary_ip(env: EnvironmentInfo) -> Optional[str]:
    """Return the best non-loopback IP for this host."""
    non_lo = [i for i in env.interfaces if not i.is_loopback]
    if non_lo:
        return non_lo[0].ip
    return "127.0.0.1"


def ros2_sourced(env: EnvironmentInfo) -> bool:
    return env.ros2_distro is not None
