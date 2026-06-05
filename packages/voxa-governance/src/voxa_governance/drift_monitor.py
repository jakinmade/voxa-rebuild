"""
Voxa — Voice Drift Monitor
Layer 5 addition — Sprint 3.

Architecture Spec v9.2.0, Section 9.5.

Monitors profile health across five signals:
1. Rule volatility — how frequently individual rules change
2. Calibration frequency — rate of calibration events over time
3. Contradiction frequency — how often conflicting candidates arise
4. Override usage — how often context overrides are invoked
5. Stability decay — rules trending downward in stability

Drift threshold breach:
  profile freeze → user notification with change summary
  → confirmation required before calibration resumes
  Enterprise accounts additionally notify the admin role.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

# Drift thresholds — starting hypotheses, instrumented for adjustment
# Architecture spec Section 15: "at what volatility level does a freeze trigger"
VOLATILITY_THRESHOLD = 5        # Rule changes per 7-day window
CALIBRATION_RATE_THRESHOLD = 20  # Calibration events per day
CONTRADICTION_THRESHOLD = 3     # Contradicting candidates per session
OVERRIDE_USAGE_THRESHOLD = 10   # Override invocations per day
STABILITY_DECAY_THRESHOLD = 0.3  # Average stability below this triggers freeze


@dataclass
class DriftReadings:
    rule_volatility: float
    calibration_frequency: float
    contradiction_frequency: float
    override_usage: float
    stability_decay: float
    profile_frozen: bool = False
    freeze_triggered_by: str | None = None
    measured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DriftNotification:
    user_id: UUID
    trigger: str
    readings: DriftReadings
    change_summary: list[str]
    is_admin_notification: bool = False
    sent_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Per-user drift state
_rule_change_log: dict[UUID, list[datetime]] = {}
_calibration_event_log: dict[UUID, list[datetime]] = {}
_contradiction_log: dict[UUID, list[datetime]] = {}
_override_log: dict[UUID, list[datetime]] = {}
_freeze_state: dict[UUID, bool] = {}
_pending_notifications: list[DriftNotification] = []


def record_rule_change(user_id: UUID) -> None:
    _rule_change_log.setdefault(user_id, []).append(datetime.now(timezone.utc))


def record_calibration_event(user_id: UUID) -> None:
    _calibration_event_log.setdefault(user_id, []).append(datetime.now(timezone.utc))


def record_contradiction(user_id: UUID) -> None:
    _contradiction_log.setdefault(user_id, []).append(datetime.now(timezone.utc))


def record_override_usage(user_id: UUID) -> None:
    _override_log.setdefault(user_id, []).append(datetime.now(timezone.utc))


def is_profile_frozen(user_id: UUID) -> bool:
    return _freeze_state.get(user_id, False)


def confirm_unfreeze(user_id: UUID) -> bool:
    """
    User confirmation required before calibration resumes after a freeze.
    Returns True if profile was frozen and is now unfrozen.
    """
    if _freeze_state.get(user_id, False):
        _freeze_state[user_id] = False
        logger.info("profile_unfrozen", user_id=str(user_id))
        return True
    return False


def _count_recent(events: list[datetime], window_days: int = 7) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    return sum(1 for e in events if e > cutoff)


def _compute_stability_average(profile) -> float:
    """Computes average stability across all non-boundary rules."""
    from voxa_core.enums import LifecycleStage
    stabilities = []
    for cat in [
        profile.identity, profile.cognitive, profile.linguistic,
        profile.stylistic, profile.interaction,
    ]:
        for field_name in type(cat).model_fields:
            rule = getattr(cat, field_name)
            if rule is not None and rule.lifecycle_stage != LifecycleStage.BOUNDARY:
                stabilities.append(rule.stability)
    if not stabilities:
        return 1.0  # No rules yet — no decay concern
    return sum(stabilities) / len(stabilities)


def take_readings(user_id: UUID, profile) -> DriftReadings:
    """
    Takes current drift monitor readings for a user.
    """
    volatility = _count_recent(_rule_change_log.get(user_id, []), window_days=7)
    calibration_rate = _count_recent(_calibration_event_log.get(user_id, []), window_days=1)
    contradiction_freq = _count_recent(_contradiction_log.get(user_id, []), window_days=1)
    override_usage = _count_recent(_override_log.get(user_id, []), window_days=1)
    avg_stability = _compute_stability_average(profile)

    readings = DriftReadings(
        rule_volatility=float(volatility),
        calibration_frequency=float(calibration_rate),
        contradiction_frequency=float(contradiction_freq),
        override_usage=float(override_usage),
        stability_decay=avg_stability,
        profile_frozen=_freeze_state.get(user_id, False),
    )

    logger.info(
        "drift_readings_taken",
        user_id=str(user_id),
        rule_volatility=volatility,
        calibration_frequency=calibration_rate,
        contradiction_frequency=contradiction_freq,
        override_usage=override_usage,
        avg_stability=avg_stability,
    )

    return readings


def evaluate_drift(
    user_id: UUID,
    profile,
    is_enterprise: bool = False,
    admin_user_id: UUID | None = None,
) -> tuple[DriftReadings, bool]:
    """
    Evaluates drift readings against thresholds.
    If any threshold breached:
      - Profile is frozen
      - User notification queued
      - Admin notification queued (enterprise only)
      - Calibration blocked until user confirms

    Returns (readings, freeze_triggered).
    """
    readings = take_readings(user_id, profile)

    breaches: list[str] = []

    if readings.rule_volatility >= VOLATILITY_THRESHOLD:
        breaches.append(f"rule_volatility={readings.rule_volatility} >= {VOLATILITY_THRESHOLD}")

    if readings.calibration_frequency >= CALIBRATION_RATE_THRESHOLD:
        breaches.append(f"calibration_frequency={readings.calibration_frequency} >= {CALIBRATION_RATE_THRESHOLD}")

    if readings.contradiction_frequency >= CONTRADICTION_THRESHOLD:
        breaches.append(f"contradiction_frequency={readings.contradiction_frequency} >= {CONTRADICTION_THRESHOLD}")

    if readings.override_usage >= OVERRIDE_USAGE_THRESHOLD:
        breaches.append(f"override_usage={readings.override_usage} >= {OVERRIDE_USAGE_THRESHOLD}")

    if readings.stability_decay < STABILITY_DECAY_THRESHOLD:
        breaches.append(f"avg_stability={readings.stability_decay:.3f} < {STABILITY_DECAY_THRESHOLD}")

    if not breaches:
        return readings, False

    # Freeze profile
    _freeze_state[user_id] = True
    readings.profile_frozen = True
    readings.freeze_triggered_by = "; ".join(breaches)

    logger.warning(
        "drift_threshold_breached_profile_frozen",
        user_id=str(user_id),
        breaches=breaches,
    )

    # Queue user notification
    user_notification = DriftNotification(
        user_id=user_id,
        trigger="; ".join(breaches),
        readings=readings,
        change_summary=breaches,
        is_admin_notification=False,
    )
    _pending_notifications.append(user_notification)

    # Queue admin notification for enterprise
    if is_enterprise and admin_user_id:
        admin_notification = DriftNotification(
            user_id=user_id,
            trigger="; ".join(breaches),
            readings=readings,
            change_summary=breaches,
            is_admin_notification=True,
        )
        _pending_notifications.append(admin_notification)
        logger.warning(
            "admin_drift_notification_queued",
            user_id=str(user_id),
            admin_user_id=str(admin_user_id),
        )

    return readings, True


def get_pending_notifications(user_id: UUID) -> list[DriftNotification]:
    """Returns pending notifications for a user (or admin)."""
    return [n for n in _pending_notifications if n.user_id == user_id]


def get_drift_status(user_id: UUID, profile) -> dict:
    """Returns current drift monitor readings as a dict for the API."""
    readings = take_readings(user_id, profile)
    return {
        "rule_volatility": readings.rule_volatility,
        "calibration_frequency": readings.calibration_frequency,
        "contradiction_frequency": readings.contradiction_frequency,
        "override_usage": readings.override_usage,
        "stability_decay": readings.stability_decay,
        "profile_frozen": readings.profile_frozen,
        "freeze_triggered_by": readings.freeze_triggered_by,
        "thresholds": {
            "rule_volatility": VOLATILITY_THRESHOLD,
            "calibration_frequency": CALIBRATION_RATE_THRESHOLD,
            "contradiction_frequency": CONTRADICTION_THRESHOLD,
            "override_usage": OVERRIDE_USAGE_THRESHOLD,
            "stability_decay": STABILITY_DECAY_THRESHOLD,
        },
    }
