import pytest

from sillytavern_rpg_engine.domain.dice import (
    D20Mode,
    DiceFormula,
    SecureDiceRoller,
    SequenceDiceRoller,
    combine_modes,
    resolve_d20,
    roll_formula,
)
from sillytavern_rpg_engine.domain.errors import ValidationError


def test_formula_parse_and_roundtrip():
    assert DiceFormula.parse("2d6+3") == DiceFormula(2, 6, 3)
    assert DiceFormula.parse("1d20-1") == DiceFormula(1, 20, -1)
    assert DiceFormula.parse("8d8") == DiceFormula(8, 8, 0)
    assert str(DiceFormula(2, 6, 3)) == "2d6+3"
    assert str(DiceFormula(1, 20, -1)) == "1d20-1"
    assert str(DiceFormula(8, 8)) == "8d8"


def test_formula_rejects_garbage_and_bounds():
    for text in ("", "d6", "2D6", "2d6+1x", "0d6", "101d6", "1d1", "1d1001"):
        with pytest.raises(ValidationError):
            DiceFormula.parse(text)
    with pytest.raises(ValidationError, match="integer"):
        DiceFormula(True, 6)


def test_sequence_roller_is_deterministic_and_exhausts():
    roller = SequenceDiceRoller([4, 2])
    assert roller.roll(2, 6) == (4, 2)
    with pytest.raises(RuntimeError, match="exhausted"):
        roller.roll(1, 6)
    with pytest.raises(ValidationError, match="outside"):
        SequenceDiceRoller([7]).roll(1, 6)


def test_secure_roller_faces_within_range():
    roller = SecureDiceRoller()
    for _ in range(200):
        faces = roller.roll(3, 20)
        assert len(faces) == 3
        assert all(1 <= face <= 20 for face in faces)


def test_roll_formula_sums_faces_plus_modifier():
    outcome = roll_formula(SequenceDiceRoller([3, 5]), DiceFormula(2, 6, 2))
    assert outcome.faces == (3, 5)
    assert outcome.total == 10


def test_resolve_d20_modes_and_naturals():
    normal = resolve_d20(SequenceDiceRoller([12]), D20Mode.NORMAL, 5)
    assert (normal.kept, normal.dropped, normal.total) == (12, None, 17)
    adv = resolve_d20(SequenceDiceRoller([7, 18]), D20Mode.ADVANTAGE, 0)
    assert (adv.kept, adv.dropped) == (18, 7)
    assert adv.is_natural_20 is False
    dis = resolve_d20(SequenceDiceRoller([20, 3]), D20Mode.DISADVANTAGE, 1)
    assert (dis.kept, dis.dropped, dis.total) == (3, 20, 4)
    crit = resolve_d20(SequenceDiceRoller([20]), D20Mode.NORMAL, -1)
    assert crit.is_natural_20 and crit.total == 19
    fumble = resolve_d20(SequenceDiceRoller([1]), D20Mode.NORMAL, 10)
    assert fumble.is_natural_1 and fumble.total == 11


def test_combine_modes_cancels_any_advantage_with_any_disadvantage():
    assert combine_modes(1, 1) is D20Mode.NORMAL
    assert combine_modes(3, 1) is D20Mode.NORMAL
    assert combine_modes(2, 0) is D20Mode.ADVANTAGE
    assert combine_modes(0, 2) is D20Mode.DISADVANTAGE
    assert combine_modes(0, 0) is D20Mode.NORMAL
