"""Shared editorial contracts without local Studio or audio dependencies."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Difficulty = Literal["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
FeedbackTag = Literal["too_dense", "too_sparse", "awkward", "tiring", "repetitive", "off_beat", "good_flow"]
Tester = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Rating = Annotated[int, Field(strict=True, ge=1, le=5)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Ratings(Request):
    overall: Rating | None = None
    flow: Rating | None = None
    readability: Rating | None = None
    musicality: Rating | None = None
    variety: Rating | None = None


class FeedbackRequest(Request):
    jobId: str = Field(min_length=1, max_length=80)
    difficulty: Difficulty
    tester: Tester = "local"
    ratings: Ratings = Field(default_factory=Ratings)
    tags: list[FeedbackTag] = Field(default_factory=list, max_length=7)
    notes: str = Field(default="", max_length=2000)
    startBeat: float | None = Field(default=None, ge=0)
    endBeat: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def meaningful_feedback(self):
        if (self.startBeat is None) != (self.endBeat is None):
            raise ValueError("Provide both startBeat and endBeat for passage feedback")
        if self.startBeat is not None and self.endBeat <= self.startBeat:
            raise ValueError("endBeat must follow startBeat")
        if not self.ratings.model_dump(exclude_none=True) and not self.tags and not self.notes.strip():
            raise ValueError("Add a rating, tag, or note")
        return self


class ComparisonRequest(Request):
    preferredJob: str = Field(min_length=1, max_length=80)
    alternateJob: str = Field(min_length=1, max_length=80)
    difficulty: Difficulty
    tester: Tester = "local"
    notes: str = Field(default="", max_length=2000)


class PresetRequest(Request):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    mappingPlan: dict[str, Any]


class SuggestionsRequest(Request):
    mappingPlan: dict[str, Any] = Field(default_factory=dict)
    tester: Tester = "local"
    difficulty: Difficulty | None = None


class AcceptanceRequest(Request):
    tester: Tester = "local"
    notes: str = Field(default="", max_length=2000)
