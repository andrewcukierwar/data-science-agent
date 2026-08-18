"""Typed output contract for the fair single-agent baseline."""

from pydantic import BaseModel, ConfigDict

from schemas.audit import AuditResult
from schemas.lead import LeadResult
from schemas.validation import ValidationResult


class GeneralistResult(BaseModel):
    """Complete observable output of one generalist analysis run.

    The three components deliberately reuse the contracts consumed by the
    multi-agent lifecycle.  The generalist produces them in one model request;
    the application still persists and validates each component separately so
    offline evaluation sees the same audit, final metric, evidence, and report
    shapes for both architectures.
    """

    model_config = ConfigDict(extra="forbid")

    audit: AuditResult
    candidate: LeadResult
    validation: ValidationResult


__all__ = ["GeneralistResult"]
