"""Dice formulas, injectable random sources, and d20 test resolution."""

from dataclasses import dataclass
from enum import StrEnum
import re
import secrets
from typing import Protocol

from .errors import ValidationError

MAX_DICE_COUNT = 100
MIN_DIE_SIDES = 2
MAX_DIE_SIDES = 1000

_FORMULA_RE = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$")


class D20Mode(StrEnum):
    """How a d20 test picks its kept die."""

    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"


@dataclass(frozen=True)
class DiceFormula:
    """A validated NdS+M dice expression."""

    count: int
    sides: int
    modifier: int = 0

    def __post_init__(self) -> None:
        for field_name, value, low, high in (
            ("count", self.count, 1, MAX_DICE_COUNT),
            ("sides", self.sides, MIN_DIE_SIDES, MAX_DIE_SIDES),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"{field_name} must be an integer")
            if not low <= value <= high:
                raise ValidationError(
                    f"{field_name} {value} outside range [{low}, {high}]"
                )
        if isinstance(self.modifier, bool) or not isinstance(self.modifier, int):
            raise ValidationError("modifier must be an integer")

    @classmethod
    def parse(cls, text: str) -> "DiceFormula":
        """Parse ``NdS`` / ``NdS+M`` / ``NdS-M``; raise ValidationError otherwise."""
        match = _FORMULA_RE.match(text.strip()) if isinstance(text, str) else None
        if match is None:
            raise ValidationError(f"invalid dice formula {text!r}")
        count, sides, modifier = match.groups()
        return cls(int(count), int(sides), int(modifier) if modifier else 0)

    def __str__(self) -> str:
        base = f"{self.count}d{self.sides}"
        if self.modifier > 0:
            return f"{base}+{self.modifier}"
        if self.modifier < 0:
            return f"{base}{self.modifier}"
        return base


class DiceRoller(Protocol):
    """Source of random faces; implementations must be deterministic when seeded."""

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        """Return ``count`` faces, each in ``1..sides``."""


class SecureDiceRoller:
    """Production roller backed by the OS cryptographic random source."""

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        return tuple(secrets.randbelow(sides) + 1 for _ in range(count))


class SequenceDiceRoller:
    """Deterministic roller for tests; raises RuntimeError when the queue runs out."""

    def __init__(self, values: list[int] | tuple[int, ...]):
        self._values = list(values)

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        if len(self._values) < count:
            raise RuntimeError(
                f"scripted sequence exhausted: need {count}, have {len(self._values)}"
            )
        faces = tuple(self._values[:count])
        del self._values[:count]
        for face in faces:
            if not 1 <= face <= sides:
                raise ValidationError(
                    f"scripted face {face} outside d{sides} range"
                )
        return faces

    @property
    def remaining(self) -> int:
        return len(self._values)


@dataclass(frozen=True)
class RollOutcome:
    """The faces and total of one rolled formula."""

    formula: DiceFormula
    faces: tuple[int, ...]
    total: int


def roll_formula(roller: DiceRoller, formula: DiceFormula) -> RollOutcome:
    faces = roller.roll(formula.count, formula.sides)
    return RollOutcome(formula, faces, sum(faces) + formula.modifier)


@dataclass(frozen=True)
class D20Outcome:
    """The kept/dropped faces and total of one d20 test."""

    kept: int
    dropped: int | None
    modifier: int
    total: int
    natural: int
    is_natural_20: bool
    is_natural_1: bool


def resolve_d20(roller: DiceRoller, mode: D20Mode, modifier: int) -> D20Outcome:
    """Roll a d20 test; advantage keeps the higher of two, disadvantage the lower."""
    if mode is D20Mode.NORMAL:
        kept = roller.roll(1, 20)[0]
        dropped = None
    else:
        faces = roller.roll(2, 20)
        if mode is D20Mode.ADVANTAGE:
            kept, dropped = max(faces), min(faces)
        else:
            kept, dropped = min(faces), max(faces)
    return D20Outcome(
        kept=kept,
        dropped=dropped,
        modifier=modifier,
        total=kept + modifier,
        natural=kept,
        is_natural_20=kept == 20,
        is_natural_1=kept == 1,
    )


def combine_modes(advantages: int, disadvantages: int) -> D20Mode:
    """2024 rule: any advantage plus any disadvantage cancel to a straight roll."""
    if advantages > 0 and disadvantages > 0:
        return D20Mode.NORMAL
    if advantages > 0:
        return D20Mode.ADVANTAGE
    if disadvantages > 0:
        return D20Mode.DISADVANTAGE
    return D20Mode.NORMAL
