"""
api/schemas/profile_recovery.py — pydantic models for the profile-
recovery flow: one endpoint this module's own addition
(RegisterRecoveryEmail*, see routes/profile_recovery.py's module
docstring for why), and two matching Engineering Architecture Section
11.6 (RecoverInitiate*, and the GET step reuses LinkResponse directly
— see that route for why).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# Plain str + a permissive pattern, not pydantic's EmailStr — that
# type needs the optional email-validator package, which is present
# in this sandbox only as an untracked transitive dependency (not
# pinned in requirements.txt/requirements.lock), so relying on it here
# risks an ImportError on the actual Railway deploy. Same light-
# validation level check_draft.py's own schema already uses for its
# constrained string fields (Field(pattern=...)), not a new approach.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRecoveryEmailRequest(BaseModel):
    email: str = Field(..., pattern=_EMAIL_PATTERN)


class RegisterRecoveryEmailResponse(BaseModel):
    registered: bool


class RecoverInitiateRequest(BaseModel):
    email: str = Field(..., pattern=_EMAIL_PATTERN)


class RecoverInitiateResponse(BaseModel):
    # Always populated, whether or not the email actually matched a
    # profile — Section 2.5's non-enumeration posture (same as the
    # existing request_subscription_restore precedent: "never reveals
    # whether the email matched"). See routes/profile_recovery.py.
    request_id: str
