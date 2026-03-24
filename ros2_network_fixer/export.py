"""
export.py — Environment export for IDEs, Docker Compose, and ros2 doctor.

Handles:
  1. ros2 doctor --report integration: parses output and surfaces actionable items
  2. .env file generation for IDE / Docker Compose use
  3. VS Code settings.json snippet with all env vars
  4. Docker Compose YAML with correct ROS 2 network configuration
  5. Manual firewall script files (fix_firewall.sh / fix_firewall.ps1)
     for users without sudo/admin access at runtime
"""

from __future__ import annotations

import json
import os
import re
import shutil
import textwrap
from pathlib import Path
from typing import Optional

from .platform_utils import EnvironmentInfo, _run
from . import ui


CONFIG_DIR = Path.home() / ".ros2_network_fixer"


# ---------------------------------------------------------------------------
# ros2 doctor integration
# ---------------------------------------------------------------------------

def run_ros2_doctor(env: EnvironmentInfo) -> dict:
    """
    Run `ros2 doctor --report` and parse the output into structured data.
    Returns a dict with sections and actionable items.
    """
    if not shutil.which("ros2"):
        return {"available": False, "sections": {}, "warnings": [], "errors": []}

    rc, out, err = _run(["ros2", "doctor", "--report"], timeout=20)
    if rc not in (0, 1):  # ros2 doctor exits 1 when it finds issues
        return {"available": False, "sections": {}, "warnings": [], "errors": []}

    sections: dict[str, list[str]] = {}
    warnings: list[str] = []
    errors:   list[str] = []
    current_section = "general"

    for line in out.splitlines():
        # Section headers like "=== network ===" or "--- RMW ---"
        m = re.match(r"^[=\-]+\s+(.+?)\s+[=\-]+$", line.strip())
        if m:
            current_section = m.group(1).lower().replace(" ", "_")
            sections.setdefault(current_section, [])
            continue

        stripped = line.strip()
        if not stripped:
            continue

        sections.setdefault(current_section, []).append(stripped)

        # Extract warnings and errors
        low = stripped.lower()
        if any(w in low for w in ["warn", "not found", "missing", "error", "fail", "mismatch"]):
            if "error" in low or "fail" in low:
                errors.append(stripped)
            else:
                warnings.append(stripped)

    return {
        "available": True,
        "sections":  sections,
        "warnings":  warnings,
        "errors":    errors,
        "raw":       out,
    }


def print_doctor_report(report: dict) -> None:
    """Pretty-print parsed ros2 doctor output."""
    ui.section("ros2 doctor Report")

    if not report.get("available"):
        ui.warn("ros2 doctor not available — ROS 2 may not be sourced.")
        return

    # Show errors first
    if report["errors"]:
        ui.nl()
        ui.error(f"{len(report['errors'])} error(s) found by ros2 doctor:")
        for e in report["errors"][:10]:
            ui.error(f"  {e}")

    if report["warnings"]:
        ui.nl()
        ui.warn(f"{len(report['warnings'])} warning(s) found by ros2 doctor:")
        for w in report["warnings"][:10]:
            ui.warn(f"  {w}")

    if not report["errors"] and not report["warnings"]:
        ui.ok("ros2 doctor found no issues.")

    # Show key sections in a readable format
    key_sections = ["network", "rmw", "middleware", "platform", "ros2"]
    for sec in key_sections:
        if sec in report["sections"] and report["sections"][sec]:
            ui.nl()
            ui.detail(f"[{sec}]")
            for line in report["sections"][sec][:8]:
                ui.detail(f"  {line}")


# ---------------------------------------------------------------------------
# .env file generation
# ---------------------------------------------------------------------------

def _collect_ros2_env_vars() -> dict[str, str]:
    """Collect all currently-set ROS 2 related environment variables."""
    ros_prefixes = [
        "ROS_", "AMENT_", "RMW_", "FASTRTPS_", "CYCLONEDDS_",
        "FASTDDS_", "DDS_", "COLCON_",
    ]
    result = {}
    for key, val in os.environ.items():
        if any(key.startswith(p) for p in ros_prefixes):
            result[key] = val
    return result


def generate_env_file(
    env: EnvironmentInfo,
    extra_vars: Optional[dict[str, str]] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Generate a .env file containing all ROS 2 environment variables.
    Compatible with Docker Compose, VS Code, and most CI systems.
    """
    ros_vars = _collect_ros2_env_vars()
    if extra_vars:
        ros_vars.update(extra_vars)

    # Also include generated vars from our config files
    generated_setup = CONFIG_DIR / "setup_discovery.bash"
    if generated_setup.exists():
        for line in generated_setup.read_text().splitlines():
            m = re.match(r'^export\s+(\w+)="?([^"]*)"?', line)
            if m:
                ros_vars.setdefault(m.group(1), m.group(2))

    if output_path is None:
        output_path = CONFIG_DIR / "ros2.env"

    lines = [
        "# ROS 2 Environment Variables",
        "# Generated by ros2_network_fixer",
        "# Usage:",
        "#   Docker Compose: env_file: - .ros2.env",
        "#   VS Code:        add to terminal.integrated.env in settings.json",
        "#   Shell:          export $(grep -v '#' ros2.env | xargs)",
        "",
    ]
    for k, v in sorted(ros_vars.items()):
        # Sanitize values for .env format (no quotes needed for simple values)
        safe_v = v.replace('"', '\\"')
        lines.append(f'{k}="{safe_v}"')

    output_path.write_text("\n".join(lines) + "\n")
    return output_path


# ---------------------------------------------------------------------------
# VS Code settings snippet
# ---------------------------------------------------------------------------

def generate_vscode_settings(
    env: EnvironmentInfo,
    extra_vars: Optional[dict[str, str]] = None,
) -> Path:
    """
    Generate a VS Code settings.json snippet with ROS 2 env vars in
    terminal.integrated.env.* so IDE-launched nodes join the ROS 2 network.
    """
    ros_vars = _collect_ros2_env_vars()
    if extra_vars:
        ros_vars.update(extra_vars)

    # Add generated discovery vars
    generated_setup = CONFIG_DIR / "setup_discovery.bash"
    if generated_setup.exists():
        for line in generated_setup.read_text().splitlines():
            m = re.match(r'^export\s+(\w+)="?([^"]*)"?', line)
            if m:
                ros_vars.setdefault(m.group(1), m.group(2))

    # Map OS to VS Code platform key
    os_key = {
        "linux":   "linux",
        "macos":   "osx",
        "windows": "windows",
    }.get(env.os_type, "linux")

    snippet = {
        "// ROS 2 environment — generated by ros2_network_fixer": "",
        "// Add these settings to your VS Code settings.json":    "",
        "// File location: .vscode/settings.json in your workspace": "",
        f"terminal.integrated.env.{os_key}": ros_vars,
        "ros.distro": env.ros2_distro or "jazzy",
    }

    output_path = CONFIG_DIR / "vscode_settings_snippet.json"
    output_path.write_text(json.dumps(snippet, indent=2))
    return output_path


# ---------------------------------------------------------------------------
# Docker Compose generation
# ---------------------------------------------------------------------------

def generate_docker_compose(
    env: EnvironmentInfo,
    ros2_image: str = "ros:jazzy",
    server_ip: Optional[str] = None,
    server_port: int = 11811,
    domain_id: int = 0,
    services: Optional[list[str]] = None,
    use_host_network: bool = False,
) -> Path:
    """
    Generate a docker-compose.yml configured for ROS 2 networking.

    Supports two networking modes:
      - host network (Linux only): simplest, full multicast support
      - bridge with Discovery Server: works on all platforms
    """
    if services is None:
        services = ["ros2_node"]

    distro = env.ros2_distro or "jazzy"
    image = ros2_image if ros2_image != "ros:jazzy" else f"ros:{distro}"

    # Environment variables for containers
    container_env: dict[str, str] = {
        "ROS_DOMAIN_ID": str(domain_id),
    }
    if server_ip:
        container_env["ROS_DISCOVERY_SERVER"] = f"{server_ip}:{server_port}"
        container_env["FASTRTPS_DEFAULT_PROFILES_FILE"] = "/ros2_config/fastdds_client.xml"

    env_block = "\n".join(f"      - {k}={v}" for k, v in container_env.items())

    if use_host_network:
        network_block = textwrap.dedent("""\
            # host network: containers share the host network stack
            # Enables multicast; only works on Linux
            network_mode: host
        """)
        networks_section = ""
        network_ref = ""
    else:
        network_block = textwrap.dedent("""\
            networks:
              - ros2_net
        """)
        networks_section = textwrap.dedent("""\
            networks:
              ros2_net:
                driver: bridge
                driver_opts:
                  com.docker.network.driver.mtu: "1500"
        """)
        network_ref = "    networks:\n      - ros2_net\n"

    # Generate service blocks
    service_blocks = []
    for svc in services:
        block = textwrap.dedent(f"""\
              {svc}:
                image: {image}
                environment:
            {env_block}
                volumes:
                  - {CONFIG_DIR}:/ros2_config:ro
                command: /bin/bash -c "source /opt/ros/{distro}/setup.bash && ros2 run demo_nodes_py talker"
                restart: unless-stopped
            """)
        if not use_host_network:
            block = block.rstrip() + "\n    networks:\n      - ros2_net\n"
        service_blocks.append(block)

    services_yaml = "\n".join(service_blocks)

    compose_content = textwrap.dedent(f"""\
        # docker-compose.yml — ROS 2 Network Configuration
        # Generated by ros2_network_fixer
        #
        # Usage:
        #   docker compose up
        #
        # Discovery Server: {"host network (multicast)" if use_host_network else f"{server_ip}:{server_port}"}
        # ROS 2 Distro: {distro}
        # Domain ID: {domain_id}

        version: "3.8"

        services:
        {services_yaml}
        {networks_section}
    """)

    output_path = CONFIG_DIR / "docker-compose.yml"
    output_path.write_text(compose_content)
    return output_path


# ---------------------------------------------------------------------------
# Firewall script files (for users without runtime sudo/admin)
# ---------------------------------------------------------------------------

def generate_firewall_scripts(env: EnvironmentInfo) -> dict[str, Path]:
    """
    Write standalone firewall scripts the user can run separately with
    elevated privileges, even if the tool itself doesn't have sudo/admin.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    # Linux
    sh_path = CONFIG_DIR / "fix_firewall.sh"
    sh_path.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        # fix_firewall.sh — Open ROS 2 DDS ports
        # Generated by ros2_network_fixer
        # Run: sudo bash fix_firewall.sh

        set -euo pipefail

        echo "[ros2_network_fixer] Applying firewall rules for ROS 2 DDS..."

        if command -v ufw &>/dev/null; then
            echo "  Using ufw..."
            sudo ufw allow in proto udp to 224.0.0.0/4
            sudo ufw allow in proto udp from 224.0.0.0/4
            sudo ufw allow in proto udp to any port 7400:7500
            sudo ufw allow in proto udp to any port 11811
            sudo ufw allow in proto udp to any port 11812
            echo "  ufw rules applied."
        elif command -v firewall-cmd &>/dev/null; then
            echo "  Using firewalld..."
            sudo firewall-cmd --permanent --add-port=7400-7500/udp
            sudo firewall-cmd --permanent --add-port=11811/udp
            sudo firewall-cmd --permanent --add-rich-rule="rule family='ipv4' source address='224.0.0.0/4' accept"
            sudo firewall-cmd --reload
            echo "  firewalld rules applied."
        elif command -v iptables &>/dev/null; then
            echo "  Using iptables..."
            sudo iptables -A INPUT -p udp -d 224.0.0.0/4 -j ACCEPT
            sudo iptables -A INPUT -p udp --dport 7400:7500 -j ACCEPT
            sudo iptables -A INPUT -p udp --dport 11811 -j ACCEPT
            echo "  iptables rules applied."
        else
            echo "  No recognized firewall backend found."
        fi

        echo "[ros2_network_fixer] Done."
    """))
    sh_path.chmod(0o755)
    written["linux"] = sh_path

    # Windows PowerShell
    ps_path = CONFIG_DIR / "fix_firewall.ps1"
    ps_path.write_text(textwrap.dedent("""\
        # fix_firewall.ps1 — Open ROS 2 DDS ports on Windows
        # Generated by ros2_network_fixer
        # Run as Administrator: powershell -ExecutionPolicy Bypass -File fix_firewall.ps1

        Write-Host "[ros2_network_fixer] Applying Windows Firewall rules for ROS 2..."

        $rules = @(
            @{ Name="ROS2-DDS-Multicast-In";       Protocol="UDP"; RemoteAddress="224.0.0.0/4" },
            @{ Name="ROS2-DDS-Unicast-In";          Protocol="UDP"; LocalPort="7400-7500"       },
            @{ Name="ROS2-Discovery-Server-In";     Protocol="UDP"; LocalPort="11811"           },
            @{ Name="ROS2-Discovery-Server-Alt-In"; Protocol="UDP"; LocalPort="11812"           }
        )

        foreach ($r in $rules) {
            $params = @{
                DisplayName = $r.Name
                Direction   = "Inbound"
                Action      = "Allow"
                Protocol    = $r.Protocol
                Enabled     = "True"
                Profile     = "Any"
            }
            if ($r.RemoteAddress) { $params.RemoteAddress = $r.RemoteAddress }
            if ($r.LocalPort)     { $params.LocalPort     = $r.LocalPort     }

            try {
                New-NetFirewallRule @params -ErrorAction Stop | Out-Null
                Write-Host "  OK: $($r.Name)"
            } catch {
                Write-Host "  SKIP (may already exist): $($r.Name)"
            }
        }

        # WSL2 Hyper-V firewall
        try {
            Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' `
                -DefaultInboundAction Allow -ErrorAction Stop
            Write-Host "  OK: WSL2 Hyper-V inbound allowed"
        } catch {
            Write-Host "  SKIP: WSL2 Hyper-V rule not available on this Windows build"
        }

        Write-Host "[ros2_network_fixer] Done."
    """))
    written["windows"] = ps_path

    return written


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_export(
    env: EnvironmentInfo,
    output_dir: Optional[Path] = None,
    ros2_image: str = "ros:jazzy",
    server_ip: Optional[str] = None,
    server_port: int = 11811,
    domain_id: int = 0,
    docker_services: Optional[list[str]] = None,
    use_host_network: bool = False,
) -> bool:
    """Generate all export artifacts: .env, vscode snippet, docker-compose, firewall scripts."""
    ui.section("Export Configuration Files")

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = CONFIG_DIR

    # 1. .env file
    extra = {}
    if server_ip:
        extra["ROS_DISCOVERY_SERVER"] = f"{server_ip}:{server_port}"
    extra["ROS_DOMAIN_ID"] = str(domain_id)

    env_path = generate_env_file(env, extra_vars=extra, output_path=output_dir / "ros2.env")
    ui.ok(f".env file:              {env_path}")

    # 2. VS Code snippet
    vscode_path = generate_vscode_settings(env, extra_vars=extra)
    if output_dir != CONFIG_DIR:
        import shutil as _shutil
        _shutil.copy(vscode_path, output_dir / "vscode_settings_snippet.json")
    ui.ok(f"VS Code snippet:        {output_dir / 'vscode_settings_snippet.json'}")

    # 3. Docker Compose
    docker_path = generate_docker_compose(
        env,
        ros2_image=ros2_image,
        server_ip=server_ip,
        server_port=server_port,
        domain_id=domain_id,
        services=docker_services or ["ros2_node"],
        use_host_network=use_host_network,
    )
    if output_dir != CONFIG_DIR:
        import shutil as _shutil
        _shutil.copy(docker_path, output_dir / "docker-compose.yml")
    ui.ok(f"docker-compose.yml:     {output_dir / 'docker-compose.yml'}")

    # 4. Firewall scripts
    fw_scripts = generate_firewall_scripts(env)
    for platform_name, script_path in fw_scripts.items():
        if output_dir != CONFIG_DIR:
            import shutil as _shutil
            _shutil.copy(script_path, output_dir / script_path.name)
        ui.ok(f"Firewall script ({platform_name}): {output_dir / script_path.name}")

    ui.section("How to Use These Files")

    ui.detail(".env file:")
    ui.code_block([
        "# In docker-compose.yml:",
        "services:",
        "  my_node:",
        "    env_file:",
        "      - ros2.env",
        "",
        "# In shell (Linux/macOS):",
        'export $(grep -v "^#" ros2.env | xargs)',
    ], label="ros2.env usage")

    ui.detail("VS Code:")
    ui.code_block([
        "# Add contents of vscode_settings_snippet.json to:",
        ".vscode/settings.json",
        "",
        "# Or open Command Palette → 'Open User Settings (JSON)'",
        "# and merge the terminal.integrated.env block.",
    ], label="VS Code")

    ui.detail("Docker Compose:")
    ui.cmd_block("docker compose up")

    ui.detail("Firewall scripts (run with elevated privileges):")
    ui.cmd_block("sudo bash fix_firewall.sh        # Linux")
    ui.cmd_block("# Run fix_firewall.ps1 as Administrator on Windows")

    return True


def print_doctor_summary(env: EnvironmentInfo) -> None:
    """Run ros2 doctor and print a compact summary."""
    with ui.spinner("Running ros2 doctor..."):
        report = run_ros2_doctor(env)
    print_doctor_report(report)
