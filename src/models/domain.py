"""
Domain models — the canonical representation of legal entities.
These are pure data classes; no business logic lives here.
"""
from __future__ import annotations
from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# -----------------------------------------
# Enumerations
# -----------------------------------------

class AmendmentType(str, Enum):
    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    DELETION = "deletion"
    RENUMBERING = "renumbering"


class ProvisionType(str, Enum):
    DEFINITION = "definition"
    OBLIGATION = "obligation"
    PROHIBITION = "prohibition"
    PENALTY = "penalty"
    PROCEDURAL = "procedural"


# -----------------------------------------
# Core Domain Models
# -----------------------------------------

class Act(BaseModel):
    id: str
    title: str
    year: int
    number: str
    short_title: Optional[str] = None
    effective_date: Optional[date] = None
    description: Optional[str] = None

    class Config:
        frozen = True


class Clause(BaseModel):
    id: str
    identifier: str          # e.g., "a", "i", "ii"
    content: str
    section_id: str
    subsection_id: Optional[str] = None

    class Config:
        frozen = True


class Subsection(BaseModel):
    id: str
    number: str              # e.g., "1", "2", "a"
    content: str
    section_id: str
    clauses: list[Clause] = Field(default_factory=list)

    class Config:
        frozen = True


class Section(BaseModel):
    id: str
    number: str              # e.g., "5", "12A"
    title: Optional[str] = None
    original_content: str
    effective_content: str   # Updated as amendments apply
    act_id: str
    subsections: list[Subsection] = Field(default_factory=list)
    order: int = 0

    class Config:
        frozen = True


class Amendment(BaseModel):
    id: str
    number: str
    year: int
    title: str
    effective_date: Optional[date] = None
    description: Optional[str] = None
    act_id: str              # Which Act this amends

    class Config:
        frozen = True


class AmendmentAction(BaseModel):
    """A single change operation within an Amendment."""
    id: str
    amendment_id: str
    amendment_type: AmendmentType
    target_section_id: str
    target_subsection_id: Optional[str] = None
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    position: Optional[str] = None   # e.g., "after subsection (2)"
    effective_date: Optional[date] = None

    class Config:
        frozen = True


class Rule(BaseModel):
    id: str
    number: str
    title: str
    content: str
    act_id: str
    section_id: Optional[str] = None   # Section that grants rule-making power
    effective_date: Optional[date] = None

    class Config:
        frozen = True


class CrossReference(BaseModel):
    """Represents a REFERS_TO relationship between sections."""
    source_section_id: str
    target_section_id: str
    context: str             # e.g., "as defined in", "subject to"

    class Config:
        frozen = True
