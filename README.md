# ROS 2 Cross-Platform Network Fixer

[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![ROS 2](https://img.shields.io/badge/ROS%202-Jazzy%20%7C%20Humble%20%7C%20Iron%20%7C%20Rolling-brightgreen.svg)](https://docs.ros.org/en/jazzy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS%20%7C%20WSL2%20%7C%20Docker-lightgrey.svg)](#)

Automates the complex network configuration that makes ROS 2 DDS discovery work across **WSL2**, **Docker**, **corporate/school networks**, **multi-machine setups**, and any environment where the default multicast-based discovery silently fails.

**Zero dependencies** — pure Python 3.8+ standard library. Works in any environment.

---

## The Problem

ROS 2's default DDS discovery relies on **UDP multicast**, which silently breaks in many real-world scenarios:

| Scenario | Why It Breaks |
|---|---|
| **WSL2 (default NAT mode)** | WSL2 gets a different IP from Windows — NAT blocks multicast |
| **Corporate / school networks** | IT departments disable multicast to reduce congestion |
| **Docker on Windows/macOS** | Container networking layers block multicast |
| **Multiple network interfaces** | DDS binds to the wrong interface — nodes exist but can't communicate |
| **Shared lab networks** | All teams default to `ROS_DOMAIN_ID=0` — nodes collide silently |
| **Mixed RMW environments** | Fast DDS and Cyclone DDS nodes are completely invisible to each other |
| **Cloud VMs / VPNs** | Multicast is not routed across most cloud and VPN networks |

The result: nodes appear invisible, topics don't flow, and QoS mismatches cause silent data loss — all with no error message.

---

## What This Tool Does

| Feature | Command |
|---|---|
| **Full diagnostic report** | `--diagnose` |
| **Discovery Server setup** | `--fix discovery` |
| **Firewall rules** | `--fix firewall` |
| **WSL2 NAT → mirrored mode** | `--fix wsl2` |
| **SROS2 encryption + auth** | `--fix security` |
| **Domain ID management** | `--fix domain-id` |
| **RMW / middleware config** | `--fix rmw` |
| **Interface binding** | `--fix interfaces` |
| **Multi-machine wizard** | `--fix multihost` |
| **Export .env / VS Code / Docker Compose** | `--export` |
| **QoS mismatch detection** | `--qos-check` |
| **ros2 doctor integration** | `--doctor` |
| **Remote peer connectivity test** | `--test-peer <IP>` |

---

## Requirements

- **Python 3.8 or newer** (no third-party dependencies)
- Works with ROS 2 **Jazzy**, **Humble**, **Iron**, **Rolling**, and older distros
- Works on **Linux**, **Windows**, **macOS**, **WSL2**, and **Docker**

---

## Installation

### Option A — pip

```bash
pip install ros2-network-fixer
```

### Option B — From source

```bash
git clone https://github.com/YOUR_USERNAME/ros2-network-fixer.git
cd ros2-network-fixer
pip install .
```

### Option C — Quick-start (no pre-install needed)

**Linux / macOS / WSL2:**
```bash
bash scripts/quickstart.sh
```

**Windows PowerShell:**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\quickstart.ps1
```

---

## Usage

### Interactive wizard (recommended first-time)

```bash
ros2_network_fixer
```

Runs diagnostics, shows what's broken, and offers guided fixes.

### Full diagnostic report

```bash
ros2_network_fixer --diagnose
```

Checks: ROS 2 environment, multicast, firewall ports, Discovery Server, WSL2 mode, security posture, Domain ID collision risk, RMW mismatch, interface binding, and QoS compatibility.

### Apply all fixes at once

```bash
ros2_network_fixer --fix all
ros2_network_fixer --fix all --yes     # non-interactive
```

### Fix specific issues

```bash
ros2_network_fixer --fix discovery         # Discovery Server (works on ALL networks)
ros2_network_fixer --fix firewall          # Open DDS/multicast ports
ros2_network_fixer --fix wsl2              # Fix WSL2 NAT networking
ros2_network_fixer --fix security          # Enable SROS2 encryption
ros2_network_fixer --fix domain-id         # Set unique ROS_DOMAIN_ID
ros2_network_fixer --fix rmw               # Configure RMW middleware
ros2_network_fixer --fix interfaces        # Pin DDS to the right interface
ros2_network_fixer --fix multihost         # Multi-machine setup wizard
```

### Export configuration files

```bash
ros2_network_fixer --export
```

Generates:
- `ros2.env` — for Docker Compose and CI
- `vscode_settings_snippet.json` — for VS Code terminal integration
- `docker-compose.yml` — pre-configured for ROS 2 networking
- `fix_firewall.sh` / `fix_firewall.ps1` — run with elevated privileges

### Utilities

```bash
ros2_network_fixer --qos-check                     # Detect silent QoS mismatches
ros2_network_fixer --doctor                        # Parse ros2 doctor output
ros2_network_fixer --test-peer 192.168.1.50        # Test remote host reachability
ros2_network_fixer --info                          # Full environment summary
```

---

## Feature Details

### Discovery Server Mode

Instead of multicast, nodes register with a central server. Works everywhere — corporate networks, VPNs, Docker, cloud VMs.

```bash
ros2_network_fixer --fix discovery --server-ip 192.168.1.100
```

Source the generated script before starting nodes:

```bash
source ~/.ros2_network_fixer/setup_discovery.bash   # bash/zsh
source ~/.ros2_network_fixer/setup_discovery.fish    # fish
~\.ros2_network_fixer\setup_discovery.bat            # Windows CMD
. ~/.ros2_network_fixer/setup_discovery.ps1          # PowerShell
```

### Domain ID Management

Prevents node collisions on shared networks. Generates a stable, deterministic ID based on your hostname.

```bash
ros2_network_fixer --fix domain-id                  # suggests an ID
ros2_network_fixer --fix domain-id --domain-id 42   # use specific ID
```

### RMW Configuration

Detects your middleware and generates the correct XML config. Supports **Fast DDS** and **Cyclone DDS** (and warns when mixed).

```bash
ros2_network_fixer --fix rmw                                    # interactive
ros2_network_fixer --fix rmw --rmw rmw_cyclonedds_cpp           # specific RMW
ros2_network_fixer --fix rmw --rmw rmw_cyclonedds_cpp --interface eth0
```

### Network Interface Binding

When you have WiFi + Ethernet + VPN + Docker bridge, DDS picks the wrong one. This pins it to the right interface for both Fast DDS and Cyclone DDS.

```bash
ros2_network_fixer --fix interfaces                 # interactive
ros2_network_fixer --fix interfaces --interface eth0
```

### Multi-Machine Setup

Generates per-host setup scripts and a deployment README for your entire network.

```bash
ros2_network_fixer --fix multihost
```

Generated files in `~/.ros2_network_fixer/multihost/`:
- `<hostname>_setup.bash/.fish/.bat/.ps1` — per-machine scripts
- `fastdds_client.xml` — shared DDS profile
- `README.md` — step-by-step deployment guide

### SROS2 Security

Enables DDS-Security: X.509 authentication, topic-level access control, and AES-128-GCM encryption.

```bash
ros2_network_fixer --fix security
ros2_network_fixer --fix security --security-strategy Permissive
ros2_network_fixer --fix security --security-enclaves /talker,/listener
```

### QoS Mismatch Detection

A `BEST_EFFORT` publisher paired with a `RELIABLE` subscriber causes silent data loss. This scans all active topics.

```bash
ros2_network_fixer --qos-check
```

### Export for IDEs and Docker

```bash
ros2_network_fixer --export --server-ip 10.0.0.1 --domain-id 42
```

**VS Code:** Copy `vscode_settings_snippet.json` contents into `.vscode/settings.json` — IDE-launched nodes will automatically join the ROS 2 network.

**Docker Compose:** Use the generated `docker-compose.yml` directly, or reference `ros2.env` from your own compose file:
```yaml
services:
  my_node:
    env_file:
      - ~/.ros2_network_fixer/ros2.env
```

### WSL2 Guide

```bash
# Run inside WSL2:
ros2_network_fixer --fix wsl2
```

Updates `%UserProfile%\.wslconfig` on the Windows host to `networkingMode=mirrored`, then guides you through restarting WSL2 and applying the Hyper-V firewall rule.

---

## Generated Files

All configuration lives in `~/.ros2_network_fixer/`:

```
~/.ros2_network_fixer/
├── fastdds_client.xml           Fast DDS Discovery Server client profile
├── fastdds_server.xml           Fast DDS Discovery Server server profile
├── fastdds_security.xml         Fast DDS DDS-Security profile
├── fastdds_interface.xml        Fast DDS interface-pinned profile
├── cyclonedds.xml               Cyclone DDS configuration
├── cyclonedds_peers.xml         Cyclone DDS static peer list
├── setup_discovery.bash/fish/bat/ps1    Discovery Server env
├── setup_security.bash/fish/bat/ps1     SROS2 security env
├── setup_domain.bash/fish/bat/ps1       Domain ID env
├── setup_rmw.bash/fish/bat/ps1          RMW env
├── setup_interface.bash/fish/bat/ps1    Interface binding env
├── fix_firewall.sh              Linux firewall script (run with sudo)
├── fix_firewall.ps1             Windows firewall script (run as Admin)
├── ros2.env                     .env file for Docker Compose / CI
├── vscode_settings_snippet.json VS Code terminal env snippet
├── docker-compose.yml           Docker Compose with ROS 2 networking
├── server.pid                   PID of running Discovery Server
└── multihost/
    ├── fastdds_client.xml
    ├── fastdds_server.xml
    ├── <host>_setup.bash/fish/bat/ps1   Per-machine setup scripts
    └── README.md                Deployment guide
```

---

## ROS 2 Compatibility

| Distro | Status |
|---|---|
| Jazzy (Ubuntu 24.04) | ✅ Fully supported |
| Humble (Ubuntu 22.04) | ✅ Fully supported |
| Iron | ✅ Compatible |
| Rolling | ✅ Compatible |
| Foxy / Galactic | ⚠️ Compatible (EOL distros) |

Supports **Fast DDS** (default) and **Cyclone DDS**. RTI Connext and Gurum DDS are detected but not configured automatically.

---

## Troubleshooting

**"Nodes still invisible after fix"**
→ Source the setup script in *every* terminal running ROS 2 nodes.
→ Verify all machines use the same `ROS_DOMAIN_ID` and `RMW_IMPLEMENTATION`.

**"WSL2 fix can't find .wslconfig"**
→ Mount the Windows drive: `sudo mount -t drvfs C: /mnt/c` then retry.

**"Firewall fix failed / permission denied"**
→ Linux: `sudo bash ~/.ros2_network_fixer/fix_firewall.sh`
→ Windows: run `fix_firewall.ps1` as Administrator.

**"Corporate network — nothing works"**
→ Use `--fix discovery` with a server IP all machines can reach. Multicast is blocked at the router — Discovery Server is the correct solution.

**"QoS mismatch found — how do I fix it?"**
→ Match `reliability` and `durability` in both publisher and subscriber. See the Quick Reference printed by `--qos-check`.

---

## Contributing

```bash
git clone https://github.com/YOUR_USERNAME/ros2-network-fixer.git
cd ros2-network-fixer
pip install -e .
ros2_network_fixer --diagnose
```

Open an issue before submitting large PRs. Platform-specific fixes and new RMW support are especially welcome.

---

## License

MIT — see [LICENSE](LICENSE).
