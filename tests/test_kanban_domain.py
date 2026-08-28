import pytest

from kanban_domain import (
    KanbanSettingsError,
    KanbanStandard,
    Materiaaltype,
    effective_kanban_settings,
    normalized_position_overrides,
    normalize_material_type,
)


def test_new_article_standard_starts_at_one_and_one():
    assert KanbanStandard.from_values() == KanbanStandard(1, 1)


@pytest.mark.parametrize(
    ("minimum", "refill", "message"),
    [
        (-1, 1, "Min"),
        (0, 0, "Aanvulhoeveelheid"),
        ("1.5", 1, "Min"),
    ],
)
def test_standard_rejects_values_outside_the_domain(minimum, refill, message):
    with pytest.raises(KanbanSettingsError, match=message):
        KanbanStandard(minimum, refill)


def test_position_inherits_the_article_standard_when_no_override_exists():
    standard = KanbanStandard(3, 4)

    assert effective_kanban_settings(Materiaaltype.KANBAN, standard) == standard


def test_position_can_override_min_and_refill_independently():
    standard = KanbanStandard(3, 4)

    assert effective_kanban_settings(
        Materiaaltype.KANBAN,
        standard,
        min_override=5,
    ) == KanbanStandard(5, 4)
    assert effective_kanban_settings(
        Materiaaltype.KANBAN,
        standard,
        refill_override=2,
    ) == KanbanStandard(3, 2)


def test_equal_position_values_are_stored_as_inheritance():
    standard = KanbanStandard(3, 4)

    assert normalized_position_overrides(
        Materiaaltype.KANBAN,
        standard,
        min_override=3,
        refill_override=2,
    ) == (None, 2)


def test_standard_material_has_no_effective_or_stored_kanban_settings():
    standard = KanbanStandard(3, 4)

    assert effective_kanban_settings(
        Materiaaltype.STANDAARD,
        standard,
        min_override=8,
        refill_override=9,
    ) is None
    assert normalized_position_overrides(
        Materiaaltype.STANDAARD,
        standard,
        min_override=8,
        refill_override=9,
    ) == (None, None)


def test_old_two_bin_value_is_read_as_kanban_during_expand():
    assert normalize_material_type("TWO_BIN") is Materiaaltype.KANBAN


def test_new_kanban_contract_has_no_absolute_target_field():
    settings = effective_kanban_settings(
        Materiaaltype.KANBAN,
        KanbanStandard(3, 4),
    )

    assert set(settings.__dict__) == {"min_level", "refill_quantity"}
    assert not hasattr(settings, "max_level")
    assert not hasattr(settings, "target_max")
