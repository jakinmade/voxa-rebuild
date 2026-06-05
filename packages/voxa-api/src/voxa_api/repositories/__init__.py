"""
Voxa Repository Factory
Selects implementation based on VOXA_REPOSITORY environment variable.
  VOXA_REPOSITORY=memory    — InMemory (default, development)
  VOXA_REPOSITORY=supabase  — Supabase (production)
"""
import os
from voxa_api.repositories.base import ProfileRepository, CalibrationRepository, GovernanceRepository

def get_repositories() -> tuple[ProfileRepository, CalibrationRepository, GovernanceRepository]:
    backend = os.environ.get("VOXA_REPOSITORY", "memory").lower()
    if backend == "supabase":
        from voxa_api.repositories.supabase import (
            SupabaseProfileRepository,
            SupabaseCalibrationRepository,
            SupabaseGovernanceRepository,
        )
        return (
            SupabaseProfileRepository(),
            SupabaseCalibrationRepository(),
            SupabaseGovernanceRepository(),
        )
    from voxa_api.repositories.memory import (
        InMemoryProfileRepository,
        InMemoryCalibrationRepository,
        InMemoryGovernanceRepository,
    )
    return (
        InMemoryProfileRepository(),
        InMemoryCalibrationRepository(),
        InMemoryGovernanceRepository(),
    )
