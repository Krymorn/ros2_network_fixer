"""
rmw.py — ROS 2 middleware (RMW) detection and configuration.

ROS 2 supports multiple DDS implementations via the RMW abstraction layer:
  - rmw_fastrtps_cpp       (Fast DDS)   — default in most distros
  - rmw_cyclonedds_cpp     (Cyclone DDS) — default in some distros, popular with Nav2
  - rmw_connextdds         (RTI Connext) — commercial, used in safety-critical systems
  - rmw_gurumdds_cpp       (Gurum DDS)   — commercial
  - rmw_opensplice_cpp     (OpenSplice)  — legacy/EOL

Mixing RMW implementations across machines or containers causes complete
communication blackouts with no error message. This module detects active
RMW, warns on likely mismatches, and generates correct configuration XML
for whichever middleware is in use.
"""

from __future__ import annotations

import os
import re
import shutil
import textwrap
from pathlib import Path
from typing import Optional

from .platform_utils import EnvironmentInfo, _run
from . import ui


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".ros2_network_fixer"

# Known RMW implementations: env value → human name
RMW_KNOWN = {
    "rmw_fastrtps_cpp":    "Fast DDS (eProsima)",
    "rmw_fastrtps_dynamic_cpp": "Fast DDS Dynamic (eProsima)",
    "rmw_cyclonedds_cpp":  "Cyclone DDS (Eclipse)",
    "rmw_connextdds":      "RTI Connext DDS",
    "rmw_gurumdds_cpp":    "Gurum DDS",
    "rmw_opensplice_cpp":  "OpenSplice (legacy)",
    "rmw_zenoh_cpp":       "Zenoh (micro-ROS / edge)",
}

# Default RMW per distro (best-effort — can change between releases)
RMW_DISTRO_DEFAULTS = {
    "jazzy":    "rmw_fastrtps_cpp",
    "iron":     "rmw_fastrtps_cpp",
    "humble":   "rmw_fastrtps_cpp",
    "rolling":  "rmw_fastrtps_cpp",
    "galactic": "rmw_cyclonedds_cpp",
    "foxy":     "rmw_fastrtps_cpp",
    "eloquent": "rmw_fastrtps_cpp",
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_rmw(env: EnvironmentInfo) -> dict:
    """
    Detect the active RMW implementation.

    Returns a dict:
      rmw_impl   : str  — e.g. "rmw_fastrtps_cpp"
      rmw_name   : str  — human-readable name
      source     : str  — "env_var" | "ros2_cli" | "distro_default" | "unknown"
      is_default : bool — True if it's the expected default for this distro
    """
    # 1. Environment variable (most authoritative)
    env_rmw = os.environ.get("RMW_IMPLEMENTATION", "")
    if env_rmw:
        return {
            "rmw_impl":   env_rmw,
            "rmw_name":   RMW_KNOWN.get(env_rmw, env_rmw),
            "source":     "env_var",
            "is_default": _is_default_rmw(env_rmw, env.ros2_distro),
        }

    # 2. Ask ros2 CLI
    if shutil.which("ros2"):
        rc, out, _ = _run(["ros2", "doctor", "--report"], timeout=10)
        if rc == 0:
            m = re.search(r"middleware\s*:\s*(\S+)", out, re.IGNORECASE)
            if m:
                rmw = m.group(1)
                return {
                    "rmw_impl":   rmw,
                    "rmw_name":   RMW_KNOWN.get(rmw, rmw),
                    "source":     "ros2_cli",
                    "is_default": _is_default_rmw(rmw, env.ros2_distro),
                }

        # Try `ros2 wtf` (older alias for ros2 doctor)
        rc2, out2, _ = _run(["ros2", "wtf", "--report"], timeout=10)
        if rc2 == 0:
            m2 = re.search(r"middleware\s*:\s*(\S+)", out2, re.IGNORECASE)
            if m2:
                rmw = m2.group(1)
                return {
                    "rmw_impl":   rmw,
                    "rmw_name":   RMW_KNOWN.get(rmw, rmw),
                    "source":     "ros2_cli",
                    "is_default": _is_default_rmw(rmw, env.ros2_distro),
                }

    # 3. Infer from distro default
    if env.ros2_distro and env.ros2_distro in RMW_DISTRO_DEFAULTS:
        rmw = RMW_DISTRO_DEFAULTS[env.ros2_distro]
        return {
            "rmw_impl":   rmw,
            "rmw_name":   RMW_KNOWN.get(rmw, rmw),
            "source":     "distro_default",
            "is_default": True,
        }

    return {
        "rmw_impl":   "unknown",
        "rmw_name":   "Unknown",
        "source":     "unknown",
        "is_default": False,
    }


def _is_default_rmw(rmw: str, distro: Optional[str]) -> bool:
    if not distro:
        return rmw == "rmw_fastrtps_cpp"
    return RMW_DISTRO_DEFAULTS.get(distro, "rmw_fastrtps_cpp") == rmw


# ---------------------------------------------------------------------------
# Cyclone DDS XML generation
# ---------------------------------------------------------------------------

def _cyclone_network_interface_xml(interface_name: Optional[str] = None) -> str:
    """
    Generate cyclonedds.xml for network interface binding and discovery.

    If interface_name is given, binds to that interface.
    Otherwise uses auto-discovery across all interfaces.
    """
    if interface_name:
        iface_element = f"<NetworkInterface name=\"{interface_name}\" />"
        comment = f"Bound to interface: {interface_name}"
    else:
        iface_element = "<!-- Auto-select interface (use NetworkInterface name= to pin) -->"
        comment = "Auto-selects best available interface"

    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8" ?>
        <!--
            Cyclone DDS Configuration
            Generated by ros2_network_fixer.
            {comment}

            Activate by setting:
              CYCLONEDDS_URI=file://{CONFIG_DIR}/cyclonedds.xml
              RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

            Reference:
              https://github.com/eclipse-cyclonedds/cyclonedds/blob/master/docs/manual/config.rst
        -->
        <CycloneDDS>
          <Domain>
            <General>
              <!-- Network interface selection -->
              {iface_element}

              <!-- Allow multicast on the local network segment -->
              <AllowMulticast>default</AllowMulticast>

              <!-- Maximum transmission unit — lower if you see fragmentation -->
              <MaxMessageSize>65500B</MaxMessageSize>

              <!-- Fragment size for large messages -->
              <FragmentSize>4000B</FragmentSize>
            </General>

            <Discovery>
              <!-- Participant index: auto-assign unique IDs -->
              <ParticipantIndex>auto</ParticipantIndex>

              <!-- Maximum number of participants to support -->
              <MaxAutoParticipantIndex>32</MaxAutoParticipantIndex>

              <!-- Peer list: uncomment and add IPs for networks without multicast -->
              <!--
              <Peers>
                <Peer address="192.168.1.10"/>
                <Peer address="192.168.1.20"/>
              </Peers>
              -->
            </Discovery>

            <Internal>
              <!-- Watermarks for receive queue — increase for high-throughput topics -->
              <Watermarks>
                <WhcHigh>500kB</WhcHigh>
              </Watermarks>
            </Internal>

            <Tracing>
              <!-- Set to "warning" or "info" for debugging; "none" for production -->
              <Verbosity>warning</Verbosity>
              <!-- <OutputFile>stderr</OutputFile> -->
            </Tracing>
          </Domain>
        </CycloneDDS>
    """)


def _cyclone_discovery_server_xml(server_ip: str, server_port: int) -> str:
    """
    Cyclone DDS config for Discovery Server (peer-based, no multicast).
    Used when multicast is unavailable.
    """
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8" ?>
        <!--
            Cyclone DDS — Discovery Server / Static Peer Configuration
            Generated by ros2_network_fixer.

            Disables multicast; uses static peer list for discovery.
            Set CYCLONEDDS_URI=file://{CONFIG_DIR}/cyclonedds_peers.xml
        -->
        <CycloneDDS>
          <Domain>
            <General>
              <AllowMulticast>false</AllowMulticast>
            </General>
            <Discovery>
              <ParticipantIndex>auto</ParticipantIndex>
              <Peers>
                <Peer address="{server_ip}:{server_port}"/>
              </Peers>
            </Discovery>
            <Tracing>
              <Verbosity>warning</Verbosity>
            </Tracing>
          </Domain>
        </CycloneDDS>
    """)


# ---------------------------------------------------------------------------
# RMW env setup scripts
# ---------------------------------------------------------------------------

def _rmw_env_scripts(
    rmw_impl: str,
    cyclone_xml_path: Optional[Path] = None,
) -> dict[str, str]:
    """Generate shell scripts that set RMW_IMPLEMENTATION and related vars."""
    env_vars: dict[str, str] = {"RMW_IMPLEMENTATION": rmw_impl}
    if cyclone_xml_path and "cyclone" in rmw_impl:
        env_vars["CYCLONEDDS_URI"] = f"file://{cyclone_xml_path}"

    bash_lines = [f'export {k}="{v}"' for k, v in env_vars.items()]
    fish_lines  = [f"set -x {k} {v}"  for k, v in env_vars.items()]
    win_lines   = [f"set {k}={v}"      for k, v in env_vars.items()]
    ps_lines    = [f'$env:{k} = "{v}"' for k, v in env_vars.items()]

    note = f"# RMW: {rmw_impl} — generated by ros2_network_fixer\n"

    return {
        "setup_rmw.bash": (
            "#!/usr/bin/env bash\n" + note
            + "\n".join(bash_lines)
            + "\necho '[ros2_network_fixer] RMW environment set.'\n"
        ),
        "setup_rmw.fish": (
            "#!/usr/bin/env fish\n" + note
            + "\n".join(fish_lines)
            + "\necho '[ros2_network_fixer] RMW environment set.'\n"
        ),
        "setup_rmw.bat": (
            "@echo off\nREM " + note
            + "\n".join(win_lines)
            + "\necho [ros2_network_fixer] RMW environment set.\n"
        ),
        "setup_rmw.ps1": (
            "# Run: . .\\setup_rmw.ps1\n# " + note
            + "\n".join(ps_lines)
            + "\nWrite-Host '[ros2_network_fixer] RMW environment set.'\n"
        ),
    }


# ---------------------------------------------------------------------------
# Diagnostic check
# ---------------------------------------------------------------------------

def check_rmw(env: EnvironmentInfo) -> list:
    """Return CheckResult list for RMW status."""
    from .diagnostics import CheckResult

    rmw_info = detect_rmw(env)
    impl = rmw_info["rmw_impl"]
    name = rmw_info["rmw_name"]
    source = rmw_info["source"]

    results = []

    if impl == "unknown":
        results.append(CheckResult(
            name="RMW implementation",
            passed=False,
            message="Could not detect RMW implementation.",
            fix_hint="Source your ROS 2 setup file or set RMW_IMPLEMENTATION explicitly.",
        ))
        return results

    results.append(CheckResult(
        name="RMW implementation",
        passed=True,
        message=f"{name} ({impl}) — detected via {source}.",
    ))

    # Warn if RMW_IMPLEMENTATION is explicitly set to something non-default
    # (common cause of cross-machine mismatches)
    if source == "env_var" and not rmw_info["is_default"]:
        results.append(CheckResult(
            name="RMW mismatch risk",
            passed=False,
            message=f"RMW_IMPLEMENTATION is manually set to '{impl}' (non-default for this distro).",
            detail=(
                "If other machines/containers use the default RMW, they will be "
                "completely invisible to nodes using this RMW — no error is shown."
            ),
            fix_hint=(
                "Ensure ALL participants set the same RMW_IMPLEMENTATION, "
                "or unset it to use the distro default on all machines."
            ),
        ))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_rmw(
    env: EnvironmentInfo,
    target_rmw: Optional[str] = None,
    interface_name: Optional[str] = None,
    server_ip: Optional[str] = None,
    server_port: int = 7400,
    auto_apply: bool = False,
) -> bool:
    """
    Configure the RMW implementation and generate appropriate XML config.

    - Generates cyclonedds.xml for Cyclone DDS users
    - Generates RMW env scripts for all shells
    - Warns about cross-RMW mismatch risks
    """
    ui.section("RMW (Middleware) Configuration")

    current = detect_rmw(env)
    ui.kv("Detected RMW", f"{current['rmw_name']} ({current['rmw_impl']})",
          ok_val=current["rmw_impl"] != "unknown")
    ui.kv("Source", current["source"], ok_val=True)
    ui.nl()

    # Determine target RMW
    if target_rmw is None:
        if auto_apply:
            target_rmw = current["rmw_impl"] if current["rmw_impl"] != "unknown" else "rmw_fastrtps_cpp"
        else:
            options = list(RMW_KNOWN.keys())
            current_idx = options.index(current["rmw_impl"]) if current["rmw_impl"] in options else 0
            ui.info("Available RMW implementations:")
            for i, (k, v) in enumerate(RMW_KNOWN.items()):
                marker = " ◄ current" if k == current["rmw_impl"] else ""
                ui.detail(f"  {i+1}) {k}  ({v}){marker}")
            raw = ui.prompt("Enter number or RMW name (Enter = keep current)",
                            default=str(current_idx + 1))
            try:
                idx = int(raw) - 1
                target_rmw = options[idx] if 0 <= idx < len(options) else current["rmw_impl"]
            except ValueError:
                target_rmw = raw if raw in RMW_KNOWN else current["rmw_impl"]

    ui.info(f"Configuring: {RMW_KNOWN.get(target_rmw, target_rmw)}")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cyclone_xml_path: Optional[Path] = None

    # Generate Cyclone DDS XML if applicable
    if "cyclone" in target_rmw.lower():
        ui.step("Generating Cyclone DDS configuration")
        cyclone_xml = _cyclone_network_interface_xml(interface_name)
        cyclone_xml_path = CONFIG_DIR / "cyclonedds.xml"
        cyclone_xml_path.write_text(cyclone_xml)
        ui.ok(f"cyclonedds.xml written: {cyclone_xml_path}")

        if server_ip:
            peers_xml = _cyclone_discovery_server_xml(server_ip, server_port)
            peers_path = CONFIG_DIR / "cyclonedds_peers.xml"
            peers_path.write_text(peers_xml)
            ui.ok(f"cyclonedds_peers.xml written: {peers_path}")

    # Generate env scripts
    ui.step("Generating RMW setup scripts")
    scripts = _rmw_env_scripts(target_rmw, cyclone_xml_path)
    for fname, content in scripts.items():
        (CONFIG_DIR / fname).write_text(content)
    ui.ok(f"RMW setup scripts written to: {CONFIG_DIR}")

    # Print activation instructions
    ui.section("Activate RMW")
    ui.info("Source this script before starting any ROS 2 nodes:")
    ui.detail("Bash / Zsh:")
    ui.cmd_block(f"source {CONFIG_DIR / 'setup_rmw.bash'}")
    ui.detail("Fish:")
    ui.cmd_block(f"source {CONFIG_DIR / 'setup_rmw.fish'}")
    ui.detail("Windows CMD:")
    ui.cmd_block(str(CONFIG_DIR / "setup_rmw.bat"))
    ui.detail("Windows PowerShell:")
    ui.cmd_block(f". '{CONFIG_DIR / 'setup_rmw.ps1'}'")

    ui.warn(f"ALL machines/containers must use the same RMW: {target_rmw}")
    ui.info("Install Cyclone DDS: sudo apt install ros-<distro>-rmw-cyclonedds-cpp")
    ui.info("Install Fast DDS:    (included with ROS 2 by default)")

    return True


def print_rmw_info(env: EnvironmentInfo) -> None:
    """Print RMW status summary."""
    rmw = detect_rmw(env)
    ui.section("RMW / Middleware")
    ui.kv("Implementation", f"{rmw['rmw_name']} ({rmw['rmw_impl']})",
          ok_val=rmw["rmw_impl"] != "unknown")
    ui.kv("Detection source", rmw["source"], ok_val=True)
    cyclone_uri = os.environ.get("CYCLONEDDS_URI", "")
    if cyclone_uri:
        ui.kv("CYCLONEDDS_URI", cyclone_uri, ok_val=True)
