# ROS 2 Cross-Platform Network Fixer

[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![ROS 2](https://img.shields.io/badge/ROS%202-Jazzy%20%7C%20Humble%20%7C%20Iron%20%7C%20Rolling-brightgreen.svg)](https://docs.ros.org/en/jazzy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS%20%7C%20WSL2%20%7C%20Docker-lightgrey.svg)](#)

Automates the complex network configuration that makes ROS 2 DDS discovery work across **WSL2**, **Docker**, **corporate/school networks**, and **multi-machine setups** — without requiring you to read hours of DDS documentation.

---

## The Problem

ROS 2's default DDS discovery relies on **UDP multicast**, which silently breaks in many real-world scenarios:

| Scenario | Why It Breaks |
|---|---|
| **WSL2 (default NAT mode)** | WSL2 gets a different IP from Windows; NAT blocks multicast |
| **Corporate / school networks** | IT departments disable multicast to reduce traffic |
| **Docker on Windows/macOS** | Container networking layers block multicast |
| **Cloud VMs / VPNs** | Multicast is not routed across most cloud and VPN networks |

The result: nodes appear invisible, topics don't show up, and hours are lost debugging "phantom" DDS issues.

---

## What This Tool Does

| Feature | Description |
|---|---|
| **Discovery Server setup** | Configures Fast DDS Discovery Server mode (unicast-based) — works everywhere multicast doesn't |
| **WSL2 network fix** | Detects NAT mode and switches `.wslconfig` to `networkingMode=mirrored` |
| **Firewall automation** | Adds the correct DDS/multicast rules on Linux (ufw/iptables/firewalld) and Windows |
| **Diagnostic suite** | Tests multicast, port availability, node discovery, and topic visibility |
| **Multi-shell support** | Generates setup scripts for bash, zsh, fish, PowerShell, and cmd.exe |

---

## Requirements

- **Python 3.8 or newer** (no other dependencies)
- Works with ROS 2 **Jazzy**, **Humble**, **Iron**, **Rolling**, and older distros
- Works on **Linux**, **Windows**, **macOS**, **WSL2**, and **Docker**

---

## Installation

### Option A — From source

```bash
git clone https://github.com/Krymorn/ros2_network_fixer.git
cd ros2_network_fixer
ros2_network_fixer
```

### Option B — Quick-start script (once cloned)

**Linux / macOS / WSL2:**
```bash
bash scripts/quickstart.sh
```

**Windows (PowerShell):**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\quickstart.ps1
```

---

## Usage

### Interactive wizard (recommended for first-time use)

```bash
ros2_network_fixer
```

Runs diagnostics, shows what's broken, and offers guided fixes.

---

### Diagnose only

```bash
ros2_network_fixer --diagnose
```

Prints a health report without making any changes.

---

### Fix everything

```bash
ros2_network_fixer --fix all
```

Applies every applicable fix: WSL2 mode, firewall rules, and Discovery Server configuration.

---

### Fix specific issues

```bash
# Discovery Server only (works on ANY network — even corporate/VPN)
ros2_network_fixer --fix discovery

# Firewall rules only
ros2_network_fixer --fix firewall

# WSL2 networking mode only
ros2_network_fixer --fix wsl2
```

---

### Discovery Server — advanced options

```bash
# Start the server process automatically after configuring
ros2_network_fixer --fix discovery --server-start

# Use a specific IP / port (e.g. for multi-machine setups)
ros2_network_fixer --fix discovery --server-ip 192.168.1.100 --server-port 11811

# Stop a running discovery server
ros2_network_fixer --server-stop
```

---

### Non-interactive mode (for scripts / CI)

```bash
ros2_network_fixer --fix all --yes
```

---

### Show environment info

```bash
ros2_network_fixer --info
```

---

## How Discovery Server Mode Works

Instead of broadcasting via multicast, each ROS 2 node registers with a central **Discovery Server**:

```
  [Node A]  ──┐
               ├──→  [Discovery Server :11811]
  [Node B]  ──┘
  
  Nodes learn about each other through the server,
  not via multicast. Works across NAT, VPNs, and firewalls.
```

After running `--fix discovery`, source the generated script in **every terminal** where you launch ROS 2 nodes:

```bash
source ~/.ros2_network_fixer/setup_discovery.bash   # bash/zsh
source ~/.ros2_network_fixer/setup_discovery.fish    # fish
~\.ros2_network_fixer\setup_discovery.bat            # Windows CMD
. ~/.ros2_network_fixer/setup_discovery.ps1          # PowerShell
```

The server can run on any machine reachable by all participants. Point all nodes at the same `ROS_DISCOVERY_SERVER=<IP>:<port>`.

---

## WSL2 Specific Guide

### Problem
WSL2's default NAT networking gives the WSL2 environment a different IP from Windows, blocking multicast.

### Fix (automatic)
```bash
# Run inside WSL2:
ros2_network_fixer --fix wsl2
```

This updates `%UserProfile%\.wslconfig` on the Windows host and guides you through restarting WSL2.

### What it changes
`.wslconfig` on Windows host:
```ini
[wsl2]
networkingMode=mirrored
```

Plus a Hyper-V firewall rule to allow inbound traffic from WSL2 (run in Windows PowerShell as Administrator):
```powershell
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
```

---

## Docker Guide

For Docker containers, the recommended approach is Discovery Server mode:

```bash
# 1. Run the fixer on the host to configure and start a server
ros2_network_fixer --fix discovery --server-start

# 2. When running containers, pass the discovery server address
docker run -e ROS_DISCOVERY_SERVER=<host-ip>:11811 \
           -e FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/fastdds_client.xml \
           your-ros2-image

# Or use host networking (Linux only)
docker run --network host your-ros2-image
```

---

## Generated Files

All configuration is written to `~/.ros2_network_fixer/`:

```
~/.ros2_network_fixer/
├── fastdds_client.xml       # Fast DDS client profile
├── fastdds_server.xml       # Fast DDS server profile
├── setup_discovery.bash     # Source in bash/zsh
├── setup_discovery.fish     # Source in fish
├── setup_discovery.bat      # Run in Windows CMD
├── setup_discovery.ps1      # Dot-source in PowerShell
└── server.pid               # PID of running server (if started)
```

---

## Troubleshooting

**"ros2 node list shows nothing after fix"**
- Make sure you sourced `setup_discovery.bash` in *every* terminal running ROS 2 nodes.
- Verify the Discovery Server is running: `ros2_network_fixer --info`

**"WSL2 fix says it can't find .wslconfig"**
- The tool looks for the Windows user profile at `/mnt/c/Users/<username>`. If your drive isn't mounted at `/mnt/c`, mount it manually: `sudo mount -t drvfs C: /mnt/c`

**"Firewall fix failed / permission denied"**
- On Linux: re-run with `sudo ros2_network_fixer --fix firewall`
- On Windows: run PowerShell as Administrator

**"I'm on a corporate network and nothing works"**
- Use `--fix discovery` with a server IP your machines can all reach.
- Multicast is almost certainly blocked — Discovery Server is the right solution.

---

## ROS 2 Compatibility

| ROS 2 Distro | Status |
|---|---|
| Jazzy (Ubuntu 24.04) | ✅ Fully tested |
| Humble (Ubuntu 22.04) | ✅ Fully tested |
| Iron | ✅ Compatible |
| Rolling | ✅ Compatible |
| Foxy / Galactic | ⚠️ Compatible (EOL distros) |

This tool works with the **default Fast DDS (eProsima)** middleware. If you're using a custom DDS implementation (Cyclone DDS, Connext), Discovery Server configuration will differ — the firewall and WSL2 fixes still apply.

---

## License

MIT — see [LICENSE](LICENSE).
