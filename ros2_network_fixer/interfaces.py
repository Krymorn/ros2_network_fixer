"""
interfaces.py — Network interface detection, selection, and DDS binding.

When a machine has multiple network interfaces (WiFi, Ethernet, VPN, Docker
bridge, WSL virtual adapter, etc.), DDS often binds to the wrong one.
Nodes "exist" but never exchange data because they're on different interfaces.

This module:
  - Enumerates all interfaces with metadata (type, IP, multicast capability)
  - Detects ambiguous multi-interface situations
  - Generates interface-pinned Fast DDS and Cyclone DDS XML configs
  - Generates CYCLONEDDS_URI / FASTRTPS env vars for the chosen interface
"""

from __future__ import annotations

import re
import socket
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .platform_utils import EnvironmentInfo, NetworkInterface, _run
from . import ui


CONFIG_DIR = Path.home() / ".ros2_network_fixer"


# ---------------------------------------------------------------------------
# Extended interface info
# ---------------------------------------------------------------------------

@dataclass
class InterfaceDetail:
    name: str
    ip: str
    netmask: Optional[str] = None
    broadcast: Optional[str] = None
    is_loopback: bool = False
    is_up: bool = True
    is_wireless: bool = False
    is_docker: bool = False
    is_vpn: bool = False
    is_virtual: bool = False
    multicast_flags: bool = True
    speed_mbps: Optional[int] = None

    @property
    def label(self) -> str:
        tags = []
        if self.is_loopback:  tags.append("loopback")
        if self.is_wireless:  tags.append("wireless")
        if self.is_docker:    tags.append("docker")
        if self.is_vpn:       tags.append("vpn")
        if self.is_virtual:   tags.append("virtual")
        if not self.is_up:    tags.append("DOWN")
        return f"{self.ip}" + (f" [{', '.join(tags)}]" if tags else "")

    @property
    def is_usable(self) -> bool:
        """True if this interface is suitable for ROS 2 traffic."""
        return self.is_up and not self.is_loopback and not self.is_docker and not self.is_vpn


# ---------------------------------------------------------------------------
# Interface enumeration
# ---------------------------------------------------------------------------

def _classify_interface(name: str, ip: str) -> dict:
    """Classify an interface based on its name and IP."""
    nl = name.lower()
    return {
        "is_loopback": nl.startswith("lo") or ip.startswith("127."),
        "is_wireless": any(nl.startswith(p) for p in ["wl", "wlan", "wifi", "wlp", "wlx"]),
        "is_docker":   any(nl.startswith(p) for p in ["docker", "br-", "veth"]),
        "is_vpn":      any(nl.startswith(p) for p in ["tun", "tap", "vpn", "wg", "ppp", "utun"]),
        "is_virtual":  any(nl.startswith(p) for p in [
            "vbox", "vmnet", "virbr", "hyperv", "vnet", "dummy", "bond", "team"
        ]) or nl.startswith("eth") is False and not any(
            nl.startswith(p) for p in ["en", "em", "eth", "eno", "ens", "enp", "lo", "wl"]
        ),
    }


def enumerate_interfaces(env: EnvironmentInfo) -> list[InterfaceDetail]:
    """Return detailed interface info for all interfaces on the system."""
    details: list[InterfaceDetail] = []

    # ── Linux / macOS: ip addr ────────────────────────────────────────────
    rc, out, _ = _run(["ip", "-4", "addr", "show"])
    if rc == 0:
        current_name = ""
        current_flags = ""
        for line in out.splitlines():
            # Interface line: "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ..."
            m = re.match(r"^\d+:\s+(\S+):\s+<([^>]*)>", line)
            if m:
                current_name = m.group(1).rstrip(":").split("@")[0]
                current_flags = m.group(2)
                continue
            # IP line: "    inet 192.168.1.5/24 brd 192.168.1.255 ..."
            m2 = re.match(r"\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)(?:\s+brd\s+(\S+))?", line)
            if m2 and current_name:
                ip = m2.group(1)
                prefix = int(m2.group(2))
                brd = m2.group(3)
                netmask = _prefix_to_netmask(prefix)
                classification = _classify_interface(current_name, ip)
                details.append(InterfaceDetail(
                    name=current_name,
                    ip=ip,
                    netmask=netmask,
                    broadcast=brd,
                    is_up="UP" in current_flags,
                    multicast_flags="MULTICAST" in current_flags,
                    **classification,
                ))
        if details:
            return details

    # ── macOS: ifconfig fallback ───────────────────────────────────────────
    rc2, out2, _ = _run(["ifconfig"])
    if rc2 == 0:
        current_name = ""
        for line in out2.splitlines():
            m = re.match(r"^(\S+):", line)
            if m:
                current_name = m.group(1)
                continue
            m2 = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(\S+)", line)
            if m2 and current_name:
                ip = m2.group(1)
                nm_raw = m2.group(2)
                netmask = _hex_netmask(nm_raw) if nm_raw.startswith("0x") else nm_raw
                classification = _classify_interface(current_name, ip)
                details.append(InterfaceDetail(
                    name=current_name,
                    ip=ip,
                    netmask=netmask,
                    is_up="UP" in line,
                    **classification,
                ))
        if details:
            return details

    # ── Windows: ipconfig ─────────────────────────────────────────────────
    rc3, out3, _ = _run(["ipconfig", "/all"])
    if rc3 == 0:
        adapter = "unknown"
        for line in out3.splitlines():
            am = re.match(r"^(\S.*?)\s*:$", line)
            if am:
                adapter = am.group(1)
                continue
            ipm = re.search(r"IPv4 Address[^:]*:\s*([\d.]+)", line)
            if ipm:
                ip = ipm.group(1).rstrip("(Preferred)")
                nm_m = re.search(r"Subnet Mask[^:]*:\s*([\d.]+)", line)
                nm = nm_m.group(1) if nm_m else None
                classification = _classify_interface(adapter, ip)
                details.append(InterfaceDetail(
                    name=adapter, ip=ip, netmask=nm, **classification,
                ))
        if details:
            return details

    # ── Fallback: basic socket ────────────────────────────────────────────
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        details.append(InterfaceDetail(name="default", ip=ip,
                                       is_loopback=ip.startswith("127.")))
    except OSError:
        pass

    return details


def _prefix_to_netmask(prefix: int) -> str:
    mask = (0xFFFFFFFF >> (32 - prefix)) << (32 - prefix)
    return socket.inet_ntoa(struct.pack(">I", mask))


def _hex_netmask(hex_str: str) -> str:
    try:
        val = int(hex_str, 16)
        return socket.inet_ntoa(struct.pack(">I", val))
    except Exception:
        return hex_str


# ---------------------------------------------------------------------------
# Ambiguity detection
# ---------------------------------------------------------------------------

def detect_interface_ambiguity(interfaces: list[InterfaceDetail]) -> dict:
    """
    Detect situations where DDS might bind to the wrong interface.

    Returns: {ambiguous: bool, usable: list, recommended: Optional[str], reason: str}
    """
    usable = [i for i in interfaces if i.is_usable]

    if not usable:
        return {
            "ambiguous": False,
            "usable": [],
            "recommended": None,
            "reason": "No usable interfaces found — check network configuration.",
        }

    if len(usable) == 1:
        return {
            "ambiguous": False,
            "usable": usable,
            "recommended": usable[0].name,
            "reason": f"Single usable interface: {usable[0].name} ({usable[0].ip})",
        }

    # Multiple usable interfaces — DDS may pick the wrong one
    # Prefer wired Ethernet over wireless
    wired = [i for i in usable if not i.is_wireless]
    wireless = [i for i in usable if i.is_wireless]

    recommended = (wired[0] if wired else usable[0]).name

    return {
        "ambiguous": True,
        "usable": usable,
        "recommended": recommended,
        "reason": (
            f"{len(usable)} usable interfaces detected — DDS may bind to the wrong one. "
            f"Recommended: {recommended}"
        ),
    }


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

def _fastdds_interface_xml(interface_name: str, ip: str) -> str:
    """Fast DDS XML profile pinned to a specific network interface."""
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<!--
    Fast DDS Interface Binding Profile
    Generated by ros2_network_fixer.
    Pins DDS traffic to interface: {interface_name} ({ip})

    Activate by setting:
      FASTRTPS_DEFAULT_PROFILES_FILE={CONFIG_DIR}/fastdds_interface.xml
-->
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <participant profile_name="interface_pinned" is_default_profile="true">
    <rtps>
      <useBuiltinTransports>false</useBuiltinTransports>
      <userTransports>
        <transport_id>udp_pinned</transport_id>
      </userTransports>
    </rtps>
  </participant>

  <transport_descriptors>
    <transport_descriptor>
      <transport_id>udp_pinned</transport_id>
      <type>UDPv4</type>
      <interfaceWhiteList>
        <address>{ip}</address>
      </interfaceWhiteList>
    </transport_descriptor>
  </transport_descriptors>
</profiles>
"""


# ---------------------------------------------------------------------------
# Diagnostic check
# ---------------------------------------------------------------------------

def check_interface_binding(env: EnvironmentInfo) -> list:
    """Return CheckResult list for interface binding issues."""
    from .diagnostics import CheckResult

    interfaces = enumerate_interfaces(env)
    ambiguity = detect_interface_ambiguity(interfaces)

    if not ambiguity["usable"]:
        return [CheckResult(
            name="Network interface binding",
            passed=False,
            message="No usable network interfaces found.",
            fix_hint="Check your network configuration.",
        )]

    if ambiguity["ambiguous"]:
        iface_list = ", ".join(f"{i.name}({i.ip})" for i in ambiguity["usable"])
        return [CheckResult(
            name="Network interface binding",
            passed=False,
            message=f"Multiple usable interfaces — DDS may bind to the wrong one.",
            detail=f"Interfaces: {iface_list}. Recommended: {ambiguity['recommended']}",
            fix_hint="Run '--fix interfaces' to pin DDS to a specific interface.",
        )]

    return [CheckResult(
        name="Network interface binding",
        passed=True,
        message=f"Single usable interface: {ambiguity['reason']}",
    )]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_interface_binding(
    env: EnvironmentInfo,
    interface_name: Optional[str] = None,
    auto_apply: bool = False,
) -> bool:
    """
    Detect interfaces, let user choose one, and generate pinned DDS configs.
    """
    ui.section("Network Interface Binding")

    interfaces = enumerate_interfaces(env)
    ambiguity = detect_interface_ambiguity(interfaces)

    # Display all interfaces
    ui.info("Detected network interfaces:")
    for iface in interfaces:
        icon = "✔" if iface.is_usable else "·"
        ui.detail(f"  {icon}  {iface.name:20s}  {iface.label}")
    ui.nl()

    if not ambiguity["usable"]:
        ui.error("No usable interfaces found.")
        return False

    if not ambiguity["ambiguous"] and not interface_name:
        ui.ok(f"Only one usable interface: {ambiguity['recommended']} — no binding needed.")
        return True

    # Choose interface
    if interface_name:
        chosen = interface_name
    elif auto_apply:
        chosen = ambiguity["recommended"]
        ui.info(f"Auto-selecting recommended interface: {chosen}")
    else:
        usable = ambiguity["usable"]
        options = [f"{i.name}  {i.ip}" + ("  ◄ recommended" if i.name == ambiguity["recommended"] else "")
                   for i in usable]
        idx = ui.choose("Which interface should DDS use?", options)
        chosen = usable[idx].name

    # Find IP for chosen interface
    chosen_iface = next((i for i in interfaces if i.name == chosen), None)
    if not chosen_iface:
        ui.error(f"Interface '{chosen}' not found.")
        return False

    ui.info(f"Pinning DDS to: {chosen} ({chosen_iface.ip})")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Fast DDS XML
    fastdds_xml = _fastdds_interface_xml(chosen, chosen_iface.ip)
    fastdds_path = CONFIG_DIR / "fastdds_interface.xml"
    fastdds_path.write_text(fastdds_xml)
    ui.ok(f"Fast DDS interface profile: {fastdds_path}")

    # Generate env scripts
    _write_interface_scripts(chosen, chosen_iface.ip, fastdds_path)

    # If Cyclone DDS is in use, update cyclonedds.xml too
    try:
        from .rmw import detect_rmw, _cyclone_network_interface_xml
        rmw_info = detect_rmw(env)
        if "cyclone" in rmw_info.get("rmw_impl", "").lower():
            cyclone_xml = _cyclone_network_interface_xml(chosen)
            cyclone_path = CONFIG_DIR / "cyclonedds.xml"
            cyclone_path.write_text(cyclone_xml)
            ui.ok(f"Cyclone DDS config updated: {cyclone_path}")
    except Exception:
        pass

    ui.section("Activate Interface Binding")
    ui.info("Source the appropriate script before starting ROS 2 nodes:")
    ui.detail("Bash / Zsh:")
    ui.cmd_block(f"source {CONFIG_DIR / 'setup_interface.bash'}")
    ui.detail("Fish:")
    ui.cmd_block(f"source {CONFIG_DIR / 'setup_interface.fish'}")
    ui.detail("Windows CMD:")
    ui.cmd_block(str(CONFIG_DIR / "setup_interface.bat"))
    ui.detail("Windows PowerShell:")
    ui.cmd_block(f". '{CONFIG_DIR / 'setup_interface.ps1'}'")

    return True


def _write_interface_scripts(iface_name: str, ip: str, fastdds_path: Path) -> None:
    """Write shell scripts that set FASTRTPS_DEFAULT_PROFILES_FILE for interface pinning."""
    env_vars = {
        "FASTRTPS_DEFAULT_PROFILES_FILE": str(fastdds_path),
        # ROS_IP is used by some older tools; set it for compatibility
        "ROS_IP": ip,
    }
    bash = "#!/usr/bin/env bash\n# Interface pinning: {}\n".format(iface_name)
    bash += "\n".join(f'export {k}="{v}"' for k, v in env_vars.items())
    bash += "\necho '[ros2_network_fixer] Interface binding active.'\n"

    fish = "#!/usr/bin/env fish\n"
    fish += "\n".join(f"set -x {k} {v}" for k, v in env_vars.items())
    fish += "\necho '[ros2_network_fixer] Interface binding active.'\n"

    bat = "@echo off\n"
    bat += "\n".join(f"set {k}={v}" for k, v in env_vars.items())
    bat += "\necho [ros2_network_fixer] Interface binding active.\n"

    ps1 = "# . .\\setup_interface.ps1\n"
    ps1 += "\n".join(f'$env:{k} = "{v}"' for k, v in env_vars.items())
    ps1 += "\nWrite-Host '[ros2_network_fixer] Interface binding active.'\n"

    (CONFIG_DIR / "setup_interface.bash").write_text(bash)
    (CONFIG_DIR / "setup_interface.fish").write_text(fish)
    (CONFIG_DIR / "setup_interface.bat").write_text(bat)
    (CONFIG_DIR / "setup_interface.ps1").write_text(ps1)


def print_interface_info(env: EnvironmentInfo) -> None:
    """Print interface summary for --info."""
    interfaces = enumerate_interfaces(env)
    ambiguity = detect_interface_ambiguity(interfaces)
    ui.section("Network Interfaces (Detailed)")
    for iface in interfaces:
        status = "ok" if iface.is_usable else "info"
        ui.kv(f"{iface.name}", iface.label, ok_val=iface.is_usable)
    if ambiguity["ambiguous"]:
        ui.warn(ambiguity["reason"])
        ui.info("Run '--fix interfaces' to pin DDS to one interface.")
