"""Constrained wire contracts for generation and outbound workers."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Difficulty = Literal['Easy', 'Normal', 'Hard', 'Expert', 'ExpertPlus']


class SectionRevision(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    idempotencyKey: str = Field(min_length=1, max_length=120)
    difficulty: Difficulty
    baseHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    startBeat: float = Field(ge=0)
    endBeat: float = Field(gt=0)
    seed: int = Field(default=42, ge=0, le=2147483647)
    mappingPlan: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def increasing_range(self):
        if self.endBeat <= self.startBeat:
            raise ValueError('The end beat must follow the start beat')
        return self


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    uploadId: str = Field(pattern=r'^[a-f0-9]{32}$')
    idempotencyKey: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(default='Unknown Artist', max_length=200)
    mapper: str = Field(default='BeatForge', max_length=120)
    seed: int = Field(default=42, ge=-2147483648, le=2147483647)
    difficulties: list[Difficulty] = Field(default_factory=lambda: ['Easy', 'Normal', 'Hard', 'Expert', 'ExpertPlus'], min_length=1, max_length=5)
    mappingPlan: dict = Field(default_factory=dict)
    allowUnconfirmed: bool = False


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['playtest_candidate', 'invalid', 'needs_anchors', 'corpus_incomplete', 'needs_palette', 'error']
    message: str = Field(default='', max_length=1000)
    # Paths, pipeline logs, corpus data and tokens must never leave the local worker.
    humanPlaytestRequired: Literal[True] = True
