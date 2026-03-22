"""
firewall.py — Cross-platform firewall configuration for ROS 2 DDS.

Handles:
  - Linux: ufw, iptables, firewalld
  - macOS: pf (packet filter) / Application Firewall
  - Windows: Windows Defender Firewall via netsh / PowerShell
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import textwrap
from typing import Optional

from .platform_utils import EnvironmentInfo, _run
from . import ui


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# RTPS / Fast DDS uses these UDP port ranges:
#   Participant discovery:  7400 + 250*domain  (default domain 0 → 7400)
#   User traffic:          7401 + 250*domain
#   Discovery Server:      11811
MULTICAST_RANGE = "224.0.0.0/4"
DDS_UNICAST_PORTS = [7400, 7401, 7402, 7403, 11811, 11812]
DDS_PORT_RANGE_LO = 7400
DDS_PORT_RANGE_HI = 7500


# ---------------------------------------------------------------------------
# Linux helpers
# ---------------------------------------------------------------------------

def _linux_firewall_backend() -> Optional[str]:
    """Detect active firewall backend."""
    if shutil.which("ufw"):
        rc, out, _ = _run(["sudo", "-n", "ufw", "status"])
        if rc == 0 and "inactive" not in out.lower():
            return "ufw"
    if shutil.which("firewall-cmd"):
        rc, _, _ = _run(["sudo", "-n", "firewall-cmd", "--state"])
        if rc == 0:
            return "firewalld"
    if shutil.which("iptables"):
        return "iptables"
    return None


def _apply_ufw(env: EnvironmentInfo) -> bool:
    rules = [
        ["sudo", "ufw", "allow", "in", "proto", "udp", "to", MULTICAST_RANGE],
        ["sudo", "ufw", "allow", "in", "proto", "udp", "from", MULTICAST_RANGE],
        ["sudo", "ufw", "allow", "in", "proto", "udp",
         "to", "any", "port", f"{DDS_PORT_RANGE_LO}:{DDS_PORT_RANGE_HI}"],
        ["sudo", "ufw", "allow", "in", "proto", "udp",
         "to", "any", "port", "11811"],
    ]
    success = True
    for rule in rules:
        rc, out, err = _run(rule)
        display = " ".join(rule[1:])  # skip 'sudo'
        if rc == 0:
            ui.ok(f"ufw: {display}")
        else:
            ui.warn(f"ufw rule may have failed: {display}")
            if err:
                ui.detail(err)
            success = False
    return success


def _apply_firewalld(env: EnvironmentInfo) -> bool:
    rules = [
        ["sudo", "firewall-cmd", "--permanent", "--add-protocol=udp"],
        ["sudo", "firewall-cmd", "--permanent",
         f"--add-port={DDS_PORT_RANGE_LO}-{DDS_PORT_RANGE_HI}/udp"],
        ["sudo", "firewall-cmd", "--permanent", "--add-port=11811/udp"],
        ["sudo", "firewall-cmd", "--add-rich-rule",
         f"rule family='ipv4' source address='{MULTICAST_RANGE}' accept"],
        ["sudo", "firewall-cmd", "--reload"],
    ]
    success = True
    for rule in rules:
        rc, _, err = _run(rule)
        if rc == 0:
            ui.ok(" ".join(rule[1:]))
        else:
            ui.warn(f"firewalld rule: {' '.join(rule[1:])}")
            if err:
                ui.detail(err)
            success = False
    return success


def _apply_iptables(env: EnvironmentInfo) -> bool:
    rules = [
        ["sudo", "iptables", "-A", "INPUT", "-p", "udp",
         "-d", MULTICAST_RANGE, "-j", "ACCEPT"],
        ["sudo", "iptables", "-A", "INPUT", "-p", "udp",
         "--dport", f"{DDS_PORT_RANGE_LO}:{DDS_PORT_RANGE_HI}", "-j", "ACCEPT"],
        ["sudo", "iptables", "-A", "INPUT", "-p", "udp",
         "--dport", "11811", "-j", "ACCEPT"],
    ]
    success = True
    for rule in rules:
        rc, _, err = _run(rule)
        if rc == 0:
            ui.ok(" ".join(rule[1:]))
        else:
            ui.warn(f"iptables: {' '.join(rule[1:])}")
            if err:
                ui.detail(err)
            success = False
    return success


def _fix_linux(env: EnvironmentInfo) -> bool:
    if not env.has_sudo:
        ui.warn("sudo access not available — printing commands for manual execution.")
        _print_linux_manual_commands()
        return False

    backend = _linux_firewall_backend()
    if not backend:
        ui.warn("No recognized firewall backend detected (ufw/firewalld/iptables).")
        ui.info("Your system may not have a software firewall active — that's OK.")
        ui.info("If you have an external firewall, allow UDP on ports 7400-7500 and 11811.")
        return True

    ui.info(f"Detected firewall backend: {backend}")

    if backend == "ufw":
        return _apply_ufw(env)
    if backend == "firewalld":
        return _apply_firewalld(env)
    if backend == "iptables":
        return _apply_iptables(env)

    return False


def _print_linux_manual_commands() -> None:
    ui.code_block([
        "# ufw",
        f"sudo ufw allow in proto udp to {MULTICAST_RANGE}",
        f"sudo ufw allow in proto udp from {MULTICAST_RANGE}",
        f"sudo ufw allow in proto udp to any port {DDS_PORT_RANGE_LO}:{DDS_PORT_RANGE_HI}",
        "sudo ufw allow in proto udp to any port 11811",
        "",
        "# iptables (alternative)",
        f"sudo iptables -A INPUT -p udp -d {MULTICAST_RANGE} -j ACCEPT",
        f"sudo iptables -A INPUT -p udp --dport {DDS_PORT_RANGE_LO}:{DDS_PORT_RANGE_HI} -j ACCEPT",
        "sudo iptables -A INPUT -p udp --dport 11811 -j ACCEPT",
    ], label="Linux firewall commands")


# ---------------------------------------------------------------------------
# macOS helpers
# ---------------------------------------------------------------------------

def _fix_macos(env: EnvironmentInfo) -> bool:
    ui.info("macOS Application Firewall check...")

    # Check if Application Firewall is enabled
    rc, out, _ = _run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"])
    if "disabled" in out.lower():
        ui.ok("macOS Application Firewall is disabled — no action needed.")
        return True

    ui.warn("macOS Application Firewall is active.")
    ui.info("If you encounter DDS discovery issues, add ROS 2 binaries as firewall exceptions:")
    ui.code_block([
        "# Allow ros2 CLI through macOS firewall:",
        "sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add $(which ros2)",
        "sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp $(which ros2)",
        "",
        "# If using custom DDS middleware, also allow:",
        "sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/local/bin/fastdds",
    ], label="macOS firewall commands")

    # pf multicast rule
    ui.info("For pf-based multicast issues, add to /etc/pf.conf:")
    ui.code_block([
        f"pass in proto udp from any to {MULTICAST_RANGE}",
        f"pass in proto udp from {MULTICAST_RANGE} to any",
        f"pass in proto udp to any port {{{DDS_PORT_RANGE_LO}:<{DDS_PORT_RANGE_HI}}}",
    ], label="/etc/pf.conf additions")

    return True


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------

_WIN_RULES = [
    {
        "name": "ROS2-DDS-Multicast-In",
        "direction": "in",
        "protocol": "udp",
        "remoteip": MULTICAST_RANGE,
        "action": "allow",
        "description": "Allow ROS 2 DDS multicast traffic inbound",
    },
    {
        "name": "ROS2-DDS-Unicast-In",
        "direction": "in",
        "protocol": "udp",
        "localport": f"{DDS_PORT_RANGE_LO}-{DDS_PORT_RANGE_HI}",
        "action": "allow",
        "description": "Allow ROS 2 DDS unicast traffic inbound",
    },
    {
        "name": "ROS2-Discovery-Server-In",
        "direction": "in",
        "protocol": "udp",
        "localport": "11811",
        "action": "allow",
        "description": "Allow ROS 2 Fast DDS Discovery Server",
    },
]


def _netsh_add_rule(rule: dict) -> bool:
    """Add a Windows Firewall rule via netsh."""
    cmd = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={rule['name']}",
        f"dir={rule['direction']}",
        f"action={rule['action']}",
        f"protocol={rule['protocol']}",
        "enable=yes",
        "profile=any",
    ]
    if "remoteip" in rule:
        cmd.append(f"remoteip={rule['remoteip']}")
    if "localport" in rule:
        cmd.append(f"localport={rule['localport']}")

    rc, out, err = _run(cmd)
    return rc == 0


def _powershell_add_rule(rule: dict) -> bool:
    """Add a Windows Firewall rule via PowerShell New-NetFirewallRule."""
    params = [
        f'-DisplayName "{rule["name"]}"',
        f'-Direction {rule["direction"].capitalize()}',
        f'-Action {rule["action"].capitalize()}',
        f'-Protocol {rule["protocol"].upper()}',
        '-Enabled True',
        '-Profile Any',
    ]
    if "remoteip" in rule:
        params.append(f'-RemoteAddress "{rule["remoteip"]}"')
    if "localport" in rule:
        params.append(f'-LocalPort {rule["localport"]}')

    ps_cmd = f"New-NetFirewallRule {' '.join(params)}"
    rc, out, err = _run(["powershell", "-NoProfile", "-Command", ps_cmd])
    return rc == 0


def _wsl2_hyperv_firewall() -> bool:
    """Apply the Hyper-V firewall setting to allow inbound traffic from WSL2."""
    # The WSL2 Hyper-V VM GUID is fixed across Windows versions
    WSL2_VM_GUID = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
    cmd = (
        f"Set-NetFirewallHyperVVMSetting -Name '{WSL2_VM_GUID}' "
        f"-DefaultInboundAction Allow"
    )
    rc, _, err = _run(["powershell", "-NoProfile", "-Command", cmd])
    if rc == 0:
        ui.ok("Hyper-V WSL2 firewall: inbound traffic from WSL2 allowed.")
        return True
    # This cmdlet may not exist on older Windows builds
    ui.warn("Set-NetFirewallHyperVVMSetting not available (older Windows build).")
    ui.detail("This is fine — the WSL2 Hyper-V firewall rule may not be needed.")
    return False


def _fix_windows(env: EnvironmentInfo) -> bool:
    if not env.has_powershell:
        ui.warn("PowerShell not available. Printing netsh commands for manual use.")
        _print_windows_manual_commands()
        return False

    ui.info("Applying Windows Defender Firewall rules for ROS 2 DDS...")
    all_ok = True

    for rule in _WIN_RULES:
        # Try PowerShell first, fall back to netsh
        if _powershell_add_rule(rule):
            ui.ok(f"Firewall rule added: {rule['name']}")
        elif _netsh_add_rule(rule):
            ui.ok(f"Firewall rule added (netsh): {rule['name']}")
        else:
            ui.warn(f"Could not add firewall rule: {rule['name']}")
            all_ok = False

    # WSL2 Hyper-V firewall (only meaningful if WSL2 is present on Windows host)
    ui.info("Applying WSL2/Hyper-V firewall rule (for Windows + WSL2 setups)...")
    _wsl2_hyperv_firewall()

    if not all_ok:
        ui.warn("Some firewall rules could not be applied automatically.")
        ui.info("You may need to run this tool as Administrator.")
        _print_windows_manual_commands()

    return all_ok


def _print_windows_manual_commands() -> None:
    ui.code_block([
        "# Run in PowerShell as Administrator:",
        "",
        "# Allow DDS multicast",
        f'New-NetFirewallRule -DisplayName "ROS2-DDS-Multicast-In" '
        f'-Direction Inbound -Action Allow -Protocol UDP '
        f'-RemoteAddress "{MULTICAST_RANGE}" -Enabled True -Profile Any',
        "",
        "# Allow DDS unicast port range",
        f'New-NetFirewallRule -DisplayName "ROS2-DDS-Unicast-In" '
        f'-Direction Inbound -Action Allow -Protocol UDP '
        f'-LocalPort {DDS_PORT_RANGE_LO}-{DDS_PORT_RANGE_HI} -Enabled True -Profile Any',
        "",
        "# Allow Discovery Server port",
        'New-NetFirewallRule -DisplayName "ROS2-Discovery-Server-In" '
        '-Direction Inbound -Action Allow -Protocol UDP '
        '-LocalPort 11811 -Enabled True -Profile Any',
        "",
        "# Allow inbound from WSL2 (Hyper-V)",
        "Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' "
        "-DefaultInboundAction Allow",
    ], label="Windows PowerShell firewall commands")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fix_firewall(env: EnvironmentInfo) -> bool:
    """Apply the appropriate firewall rules for the detected platform."""
    ui.section("Firewall Configuration")

    if env.os_type == "linux":
        return _fix_linux(env)
    if env.os_type == "macos":
        return _fix_macos(env)
    if env.os_type == "windows":
        return _fix_windows(env)

    ui.warn(f"Unsupported OS type: {env.os_type}")
    return False


def print_firewall_info(env: EnvironmentInfo) -> None:
    """Print current firewall-relevant info without making changes."""
    ui.section("Firewall Information")
    ui.kv("OS", env.os_type, ok_val=True)

    if env.os_type == "linux":
        backend = _linux_firewall_backend()
        ui.kv("Firewall backend", backend or "none detected", ok_val=bool(backend))
        if not env.has_sudo:
            ui.warn("sudo not available — firewall changes will require manual execution.")

    if env.os_type == "windows":
        ui.kv("PowerShell", "available" if env.has_powershell else "not found",
              ok_val=env.has_powershell)

    ui.nl()
    ui.info(f"DDS multicast range: {MULTICAST_RANGE}")
    ui.info(f"DDS unicast ports:   {DDS_PORT_RANGE_LO}–{DDS_PORT_RANGE_HI}")
    ui.info("Discovery Server:    11811 (UDP)")
