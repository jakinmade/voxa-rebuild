"""
Voxa — Supabase Repository Implementations (stub)
Production persistence layer.

Environment variables required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

To activate:
  Set VOXA_REPOSITORY=supabase in environment.
  The API will use these implementations instead of InMemory.
  All endpoint logic remains unchanged.

Schema (to be applied via Supabase migrations):
  voice_profiles       — VoiceProfile JSON, indexed by user_id
  profile_versions     — VoiceProfileVersion JSON, indexed by user_id + version
  session_counts       — int counter per user_id
  calibration_events   — CalibrationEvent JSON, indexed by user_id
  rule_observations    — RuleObservation JSON, indexed by user_id
  rule_candidates      — RuleCandidate JSON, indexed by user_id
  rendered_outputs     — RenderedOutput JSON, indexed by output_id
  audit_log            — append-only dict entries
  rule_traces          — RuleTrace JSON, indexed by output_id
"""

from __future__ import annotations

import json
import os
from uuid import UUID

import structlog

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

logger = structlog.get_logger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _get_client():
    """Returns a Supabase client. Raises if credentials not configured."""
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        raise RuntimeError(
            "supabase-py not installed. Run: pip install supabase"
        )
    except Exception as e:
        raise RuntimeError(f"Supabase client failed to initialise: {e}")


class SupabaseProfileRepository(ProfileRepository):
    """
    Production profile repository backed by Supabase.
    State survives API restarts. Multiple instances share state.
    Row-level security enforced at Supabase level.
    """

    def get(self, user_id: UUID) -> VoiceProfile | None:
        client = _get_client()
        result = client.table("voice_profiles").select("*").eq("user_id", str(user_id)).execute()
        if not result.data:
            return None
        return VoiceProfile.model_validate_json(result.data[0]["profile_json"])

    def save(self, profile: VoiceProfile) -> None:
        client = _get_client()
        client.table("voice_profiles").upsert({
            "user_id": str(profile.user_id),
            "profile_json": profile.model_dump_json(),
            "version": profile.version,
            "updated_at": profile.updated_at.isoformat(),
        }).execute()

    def exists(self, user_id: UUID) -> bool:
        client = _get_client()
        result = client.table("voice_profiles").select("user_id").eq("user_id", str(user_id)).execute()
        return bool(result.data)

    def get_version(self, user_id: UUID, version: int) -> VoiceProfileVersion | None:
        client = _get_client()
        result = (
            client.table("profile_versions")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("version", version)
            .execute()
        )
        if not result.data:
            return None
        return VoiceProfileVersion.model_validate_json(result.data[0]["version_json"])

    def save_version(self, version: VoiceProfileVersion) -> None:
        client = _get_client()
        client.table("profile_versions").insert({
            "user_id": str(version.user_id),
            "version": version.version,
            "snapshot_id": str(version.snapshot_id),
            "version_json": version.model_dump_json(),
            "timestamp": version.timestamp.isoformat(),
        }).execute()

    def list_versions(self, user_id: UUID) -> list[VoiceProfileVersion]:
        client = _get_client()
        result = (
            client.table("profile_versions")
            .select("*")
            .eq("user_id", str(user_id))
            .order("version")
            .execute()
        )
        return [VoiceProfileVersion.model_validate_json(r["version_json"]) for r in result.data]

    def get_session_count(self, user_id: UUID) -> int:
        client = _get_client()
        result = client.table("session_counts").select("count").eq("user_id", str(user_id)).execute()
        if not result.data:
            return 0
        return result.data[0]["count"]

    def increment_session_count(self, user_id: UUID) -> int:
        client = _get_client()
        current = self.get_session_count(user_id)
        new_count = current + 1
        client.table("session_counts").upsert({
            "user_id": str(user_id),
            "count": new_count,
        }).execute()
        return new_count


class SupabaseCalibrationRepository(CalibrationRepository):

    def save_event(self, event: CalibrationEvent) -> None:
        client = _get_client()
        client.table("calibration_events").insert({
            "event_id": str(event.event_id),
            "user_id": str(event.user_id),
            "event_json": event.model_dump_json(),
        }).execute()

    def list_events(self, user_id: UUID) -> list[CalibrationEvent]:
        client = _get_client()
        result = client.table("calibration_events").select("*").eq("user_id", str(user_id)).execute()
        return [CalibrationEvent.model_validate_json(r["event_json"]) for r in result.data]

    def save_observation(self, obs: RuleObservation) -> None:
        client = _get_client()
        client.table("rule_observations").insert({
            "observation_id": str(obs.observation_id),
            "user_id": str(obs.user_id),
            "obs_json": obs.model_dump_json(),
        }).execute()

    def list_observations(self, user_id: UUID) -> list[RuleObservation]:
        client = _get_client()
        result = client.table("rule_observations").select("*").eq("user_id", str(user_id)).execute()
        return [RuleObservation.model_validate_json(r["obs_json"]) for r in result.data]

    def save_candidate(self, candidate: RuleCandidate) -> None:
        client = _get_client()
        client.table("rule_candidates").insert({
            "candidate_id": str(candidate.candidate_id),
            "user_id": str(candidate.user_id),
            "candidate_json": candidate.model_dump_json(),
        }).execute()

    def list_candidates(self, user_id: UUID) -> list[RuleCandidate]:
        client = _get_client()
        result = client.table("rule_candidates").select("*").eq("user_id", str(candidate.user_id)).execute()
        return [RuleCandidate.model_validate_json(r["candidate_json"]) for r in result.data]

    def get_rendered_output(self, output_id: UUID) -> object | None:
        from voxa_core.entities import RenderedOutput
        client = _get_client()
        result = client.table("rendered_outputs").select("*").eq("output_id", str(output_id)).execute()
        if not result.data:
            return None
        return RenderedOutput.model_validate_json(result.data[0]["output_json"])

    def save_rendered_output(self, output: object) -> None:
        client = _get_client()
        client.table("rendered_outputs").insert({
            "output_id": str(output.output_id),
            "user_id": str(output.user_id),
            "output_json": output.model_dump_json(),
        }).execute()


class SupabaseGovernanceRepository(GovernanceRepository):

    def append_audit_entry(self, entry: dict) -> None:
        client = _get_client()
        client.table("audit_log").insert(entry).execute()

    def list_audit_entries(self, user_id: UUID | None = None, org_id: str | None = None) -> list[dict]:
        client = _get_client()
        query = client.table("audit_log").select("*")
        if user_id:
            query = query.eq("user_id", str(user_id))
        if org_id:
            query = query.eq("org_id", org_id)
        return query.execute().data

    def save_rule_trace(self, output_id: UUID, trace: object) -> None:
        client = _get_client()
        client.table("rule_traces").upsert({
            "output_id": str(output_id),
            "trace_json": json.dumps(trace.__dict__ if hasattr(trace, '__dict__') else trace),
        }).execute()

    def get_rule_trace(self, output_id: UUID) -> object | None:
        client = _get_client()
        result = client.table("rule_traces").select("*").eq("output_id", str(output_id)).execute()
        if not result.data:
            return None
        return json.loads(result.data[0]["trace_json"])
