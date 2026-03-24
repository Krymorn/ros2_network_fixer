"""
qos.py — ROS 2 QoS profile mismatch detection.

A publisher and subscriber with incompatible QoS settings silently fail
to connect — the topic exists, nodes are visible, but data never flows.
This is one of the most confusing ROS 2 debugging experiences because
there is no error message by default.

Common incompatible pairs:
  Publisher RELIABLE    ↔  Subscriber BEST_EFFORT    → no data (subscriber rule)
  Publisher VOLATILE    ↔  Subscriber TRANSIENT_LOCAL → no data (subscriber needs history)
  Publisher KEEP_LAST   ↔  Subscriber KEEP_ALL        → may cause backpressure

This module:
  - Lists topics and queries publisher/subscriber QoS via `ros2 topic info -v`
  - Detects incompatible pairs
  - Reports mismatches with plain-language explanations
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from typing import Optional

from .platform_utils import EnvironmentInfo, _run
from . import ui


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EndpointQoS:
    node_name: str
    role: str          # "publisher" | "subscriber"
    reliability: str   # "RELIABLE" | "BEST_EFFORT" | "unknown"
    durability: str    # "VOLATILE" | "TRANSIENT_LOCAL" | "unknown"
    history: str       # "KEEP_LAST" | "KEEP_ALL" | "unknown"
    depth: int = 10


@dataclass
class TopicQoSReport:
    topic: str
    publishers: list[EndpointQoS] = field(default_factory=list)
    subscribers: list[EndpointQoS] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)

    @property
    def has_mismatch(self) -> bool:
        return bool(self.mismatches)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_topic_info(topic: str, raw: str) -> TopicQoSReport:
    """Parse `ros2 topic info -v` output into a TopicQoSReport."""
    report = TopicQoSReport(topic=topic)
    current_role = ""
    current_node = ""
    current_qos: dict = {}

    for line in raw.splitlines():
        line = line.strip()

        # Publisher / Subscriber block header — match "Publisher" or "Publisher count:"
        if re.search(r"^publisher", line, re.IGNORECASE):
            if current_role and current_node:
                _flush_endpoint(report, current_role, current_node, current_qos)
            current_role = "publisher"
            current_node = ""
            current_qos = {}
        elif re.search(r"^subscri", line, re.IGNORECASE):
            if current_role and current_node:
                _flush_endpoint(report, current_role, current_node, current_qos)
            current_role = "subscriber"
            current_node = ""
            current_qos = {}

        # Node name
        m_node = re.search(r"Node name:\s*(\S+)", line)
        if m_node:
            current_node = m_node.group(1)

        # QoS fields
        for field_name, pattern in [
            ("reliability", r"Reliability:\s*(\S+)"),
            ("durability",  r"Durability:\s*(\S+)"),
            ("history",     r"History \(Depth\):\s*(\S+)"),
            ("depth",       r"Queue size:\s*(\d+)"),
        ]:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                current_qos[field_name] = m.group(1).upper()

    # Flush last endpoint
    if current_role and current_node:
        _flush_endpoint(report, current_role, current_node, current_qos)

    # Detect mismatches
    report.mismatches = _detect_mismatches(report)
    return report


def _flush_endpoint(
    report: TopicQoSReport,
    role: str,
    node: str,
    qos: dict,
) -> None:
    ep = EndpointQoS(
        node_name=node,
        role=role,
        reliability=qos.get("reliability", "unknown"),
        durability=qos.get("durability", "unknown"),
        history=qos.get("history", "unknown"),
        depth=int(qos.get("depth", 10)),
    )
    if role == "publisher":
        report.publishers.append(ep)
    else:
        report.subscribers.append(ep)


def _detect_mismatches(report: TopicQoSReport) -> list[str]:
    """Return list of human-readable mismatch descriptions."""
    mismatches = []

    for pub in report.publishers:
        for sub in report.subscribers:
            # Reliability mismatch: RELIABLE publisher + BEST_EFFORT subscriber is OK
            # but BEST_EFFORT publisher + RELIABLE subscriber is NOT
            if (pub.reliability == "BEST_EFFORT" and sub.reliability == "RELIABLE"):
                mismatches.append(
                    f"{pub.node_name} publishes BEST_EFFORT but "
                    f"{sub.node_name} requires RELIABLE — no data will flow."
                )

            # Durability mismatch: publisher VOLATILE + subscriber TRANSIENT_LOCAL
            if (pub.durability == "VOLATILE" and sub.durability == "TRANSIENT_LOCAL"):
                mismatches.append(
                    f"{pub.node_name} uses VOLATILE durability but "
                    f"{sub.node_name} requires TRANSIENT_LOCAL — "
                    f"late-joining subscriber will miss all history."
                )

    return mismatches


# ---------------------------------------------------------------------------
# Diagnostic check
# ---------------------------------------------------------------------------

def check_qos_mismatches(env: EnvironmentInfo) -> list:
    """
    Return CheckResult list for QoS mismatch detection.
    Only runs if ros2 CLI is available and topics are active.
    """
    from .diagnostics import CheckResult

    if not shutil.which("ros2"):
        return []  # Silent skip — ros2 not available, already reported elsewhere

    # Get topic list
    rc, out, _ = _run(["ros2", "topic", "list"], timeout=8)
    if rc != 0 or not out.strip():
        return []  # No topics — nothing to check

    topics = [t.strip() for t in out.splitlines() if t.strip()]
    mismatched_topics: list[str] = []
    mismatch_details: list[str] = []

    for topic in topics[:30]:  # Cap at 30 to avoid long runtime
        rc2, info_out, _ = _run(["ros2", "topic", "info", "-v", topic], timeout=6)
        if rc2 != 0 or not info_out.strip():
            continue
        report = _parse_topic_info(topic, info_out)
        if report.has_mismatch:
            mismatched_topics.append(topic)
            mismatch_details.extend(report.mismatches)

    if not mismatched_topics:
        return [CheckResult(
            name="QoS compatibility",
            passed=True,
            message=f"No QoS mismatches detected across {len(topics)} topic(s).",
        )]

    return [CheckResult(
        name="QoS compatibility",
        passed=False,
        message=f"QoS mismatches on {len(mismatched_topics)} topic(s): {', '.join(mismatched_topics)}",
        detail="\n".join(mismatch_details[:5]),  # Show first 5
        fix_hint=(
            "QoS mismatches cause silent data loss. Fix the publisher or subscriber "
            "to use matching Reliability and Durability settings. "
            "Run '--qos-check' for a full report."
        ),
    )]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_qos_check(env: EnvironmentInfo, topic_filter: Optional[str] = None) -> None:
    """
    Run a full QoS compatibility check across all active topics.
    Prints a detailed report.
    """
    ui.section("QoS Compatibility Check")

    if not shutil.which("ros2"):
        ui.error("ros2 CLI not found — cannot check QoS.")
        ui.info("Source your ROS 2 setup file and re-run.")
        return

    with ui.spinner("Fetching topic list..."):
        rc, out, _ = _run(["ros2", "topic", "list"], timeout=8)

    if rc != 0 or not out.strip():
        ui.warn("No active topics found. Launch some ROS 2 nodes first.")
        return

    topics = [t.strip() for t in out.splitlines() if t.strip()]
    if topic_filter:
        topics = [t for t in topics if topic_filter in t]

    ui.info(f"Checking QoS on {len(topics)} topic(s)...")
    ui.nl()

    all_reports: list[TopicQoSReport] = []

    for topic in topics:
        rc2, info_out, _ = _run(["ros2", "topic", "info", "-v", topic], timeout=6)
        if rc2 != 0:
            continue
        report = _parse_topic_info(topic, info_out)
        all_reports.append(report)

    # Print results
    mismatched = [r for r in all_reports if r.has_mismatch]
    ok_topics   = [r for r in all_reports if not r.has_mismatch]

    if ok_topics:
        ui.ok(f"{len(ok_topics)} topic(s) have compatible QoS settings.")

    if mismatched:
        ui.nl()
        ui.error(f"{len(mismatched)} topic(s) have QoS mismatches:")
        for rep in mismatched:
            ui.nl()
            ui.warn(f"  Topic: {rep.topic}")
            for pub in rep.publishers:
                ui.detail(f"    PUB  {pub.node_name}: {pub.reliability} / {pub.durability}")
            for sub in rep.subscribers:
                ui.detail(f"    SUB  {sub.node_name}: {sub.reliability} / {sub.durability}")
            for mismatch in rep.mismatches:
                ui.error(f"    ✘  {mismatch}")
    else:
        ui.ok("All active topics have compatible QoS — no silent data loss detected.")

    ui.nl()
    ui.section("QoS Quick Reference")
    ui.detail("Reliability:  RELIABLE > BEST_EFFORT (pub must be >= sub)")
    ui.detail("Durability:   TRANSIENT_LOCAL > VOLATILE (pub must be >= sub for late joiners)")
    ui.detail("History:      KEEP_ALL > KEEP_LAST (typically compatible in both directions)")
    ui.detail("")
    ui.detail("Fix: match publisher and subscriber QoS in your node code, e.g.:")
    ui.code_block([
        "from rclpy.qos import QoSProfile, ReliabilityPolicy",
        "qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE, depth=10)",
        "self.publisher = self.create_publisher(MsgType, 'topic', qos)",
        "self.subscriber = self.create_subscription(MsgType, 'topic', cb, qos)",
    ], label="Python example")
