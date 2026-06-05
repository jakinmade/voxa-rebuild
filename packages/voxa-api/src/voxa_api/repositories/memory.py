"""
Voxa — In-Memory Repository Implementations
For development and testing only.
API restart = state loss. Multiple instances diverge.

Swap for SupabaseProfileRepository in production
without touching endpoint logic.
"""

from __future__ import annotations

from uuid import UUID

from voxa_core.entities import (
    CalibrationEvent,
    RuleCandidate,
    RuleObservation,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_api.repositories.base import (
    CalibrationRepository,
    GovernanceRepository,
    ProfileRepository,
)


class InMemoryProfileRepository(ProfileRepository):

    def __init__(self):
        self._profiles: dict[UUID, VoiceProfile] = {}
        self._versions: dict[UUID, list[VoiceProfileVersion]] = {}
        self._session_counts: dict[UUID, int] = {}

    def get(self, user_id: UUID) -> VoiceProfile | None:
        return self._profiles.get(user_id)

    def save(self, profile: VoiceProfile) -> None:
        self._profiles[profile.user_id] = profile

    def exists(self, user_id: UUID) -> bool:
        return user_id in self._profiles

    def get_version(self, user_id: UUID, version: int) -> VoiceProfileVersion | None:
        for v in self._versions.get(user_id, []):
            if v.version == version:
                return v
        return None

    def save_version(self, version: VoiceProfileVersion) -> None:
        self._versions.setdefault(version.user_id, []).append(version)

    def list_versions(self, user_id: UUID) -> list[VoiceProfileVersion]:
        return list(self._versions.get(user_id, []))

    def get_session_count(self, user_id: UUID) -> int:
        return self._session_counts.get(user_id, 0)

    def increment_session_count(self, user_id: UUID) -> int:
        count = self._session_counts.get(user_id, 0) + 1
        self._session_counts[user_id] = count
        return count


class InMemoryCalibrationRepository(CalibrationRepository):

    def __init__(self):
        self._events: list[CalibrationEvent] = []
        self._observations: dict[UUID, list[RuleObservation]] = {}
        self._candidates: dict[UUID, list[RuleCandidate]] = {}
        self._rendered_outputs: dict[UUID, object] = {}

    def save_event(self, event: CalibrationEvent) -> None:
        self._events.append(event)

    def list_events(self, user_id: UUID) -> list[CalibrationEvent]:
        return [e for e in self._events if e.user_id == user_id]

    def save_observation(self, obs: RuleObservation) -> None:
        self._observations.setdefault(obs.user_id, []).append(obs)

    def list_observations(self, user_id: UUID) -> list[RuleObservation]:
        return list(self._observations.get(user_id, []))

    def save_candidate(self, candidate: RuleCandidate) -> None:
        self._candidates.setdefault(candidate.user_id, []).append(candidate)

    def list_candidates(self, user_id: UUID) -> list[RuleCandidate]:
        return list(self._candidates.get(user_id, []))

    def get_rendered_output(self, output_id: UUID) -> object | None:
        return self._rendered_outputs.get(output_id)

    def save_rendered_output(self, output: object) -> None:
        self._rendered_outputs[output.output_id] = output


class InMemoryGovernanceRepository(GovernanceRepository):

    def __init__(self):
        self._audit: list[dict] = []
        self._rule_traces: dict[UUID, object] = {}

    def append_audit_entry(self, entry: dict) -> None:
        self._audit.append(entry)

    def list_audit_entries(
        self, user_id: UUID | None = None, org_id: str | None = None
    ) -> list[dict]:
        results = list(self._audit)
        if user_id:
            results = [e for e in results if e.get("user_id") == str(user_id)]
        if org_id:
            results = [e for e in results if e.get("org_id") == org_id]
        return results

    def save_rule_trace(self, output_id: UUID, trace: object) -> None:
        self._rule_traces[output_id] = trace

    def get_rule_trace(self, output_id: UUID) -> object | None:
        return self._rule_traces.get(output_id)
