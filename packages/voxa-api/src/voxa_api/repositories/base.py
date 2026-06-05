"""
Voxa — Repository Interfaces
Abstract base classes for all persistence operations.

Current implementations:
  InMemoryProfileRepository     — development / testing
  InMemoryCalibrationRepository — development / testing
  InMemoryGovernanceRepository  — development / testing

Production swap:
  SupabaseProfileRepository, SupabaseCalibrationRepository, SupabaseGovernanceRepository
  — swap without touching endpoint logic.

API restart = state loss with InMemory.
API restart = state preserved with Supabase.
Multiple instances diverge with InMemory.
Multiple instances converge with Supabase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from voxa_core.entities import (
    CalibrationEvent,
    RuleCandidate,
    RuleObservation,
    VoiceProfile,
    VoiceProfileVersion,
)


# ---------------------------------------------------------------------------
# Profile Repository
# ---------------------------------------------------------------------------

class ProfileRepository(ABC):

    @abstractmethod
    def get(self, user_id: UUID) -> VoiceProfile | None: ...

    @abstractmethod
    def save(self, profile: VoiceProfile) -> None: ...

    @abstractmethod
    def exists(self, user_id: UUID) -> bool: ...

    @abstractmethod
    def get_version(self, user_id: UUID, version: int) -> VoiceProfileVersion | None: ...

    @abstractmethod
    def save_version(self, version: VoiceProfileVersion) -> None: ...

    @abstractmethod
    def list_versions(self, user_id: UUID) -> list[VoiceProfileVersion]: ...

    @abstractmethod
    def get_session_count(self, user_id: UUID) -> int: ...

    @abstractmethod
    def increment_session_count(self, user_id: UUID) -> int: ...


# ---------------------------------------------------------------------------
# Calibration Repository
# ---------------------------------------------------------------------------

class CalibrationRepository(ABC):

    @abstractmethod
    def save_event(self, event: CalibrationEvent) -> None: ...

    @abstractmethod
    def list_events(self, user_id: UUID) -> list[CalibrationEvent]: ...

    @abstractmethod
    def save_observation(self, obs: RuleObservation) -> None: ...

    @abstractmethod
    def list_observations(self, user_id: UUID) -> list[RuleObservation]: ...

    @abstractmethod
    def save_candidate(self, candidate: RuleCandidate) -> None: ...

    @abstractmethod
    def list_candidates(self, user_id: UUID) -> list[RuleCandidate]: ...

    @abstractmethod
    def get_rendered_output(self, output_id: UUID) -> object | None: ...

    @abstractmethod
    def save_rendered_output(self, output: object) -> None: ...


# ---------------------------------------------------------------------------
# Governance Repository
# ---------------------------------------------------------------------------

class GovernanceRepository(ABC):

    @abstractmethod
    def append_audit_entry(self, entry: dict) -> None: ...

    @abstractmethod
    def list_audit_entries(self, user_id: UUID | None, org_id: str | None) -> list[dict]: ...

    @abstractmethod
    def save_rule_trace(self, output_id: UUID, trace: object) -> None: ...

    @abstractmethod
    def get_rule_trace(self, output_id: UUID) -> object | None: ...
