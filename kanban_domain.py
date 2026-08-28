"""Domain rules for material types and effective Kanban settings.

The module exposes one Kanban meaning: an Article standard with Min and refill
quantity, optionally overridden per inventory position.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re


class KanbanSettingsError(ValueError):
    """Raised when a Kanban setting cannot be represented safely."""


class Materiaaltype(str, Enum):
    KANBAN = "KANBAN"
    STANDAARD = "STANDAARD"


class LocatiekaartStatus(str, Enum):
    PENDING_PRINT = "PENDING_PRINT"
    PRINTED = "PRINTED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


DEFAULT_MIN_LEVEL = 1
DEFAULT_REFILL_QUANTITY = 1


def _integer(value, field_name, minimum):
    if isinstance(value, bool):
        raise KanbanSettingsError(f"{field_name} moet een geheel getal zijn.")

    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        result = int(value.strip())
    else:
        raise KanbanSettingsError(f"{field_name} moet een geheel getal zijn.")

    if result < minimum:
        raise KanbanSettingsError(
            f"{field_name} moet minimaal {minimum} zijn."
        )
    return result


def _optional_integer(value, field_name, minimum):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _integer(value, field_name, minimum)


@dataclass(frozen=True)
class KanbanStandard:
    """The company-specific standard belonging to one Article."""

    min_level: int = DEFAULT_MIN_LEVEL
    refill_quantity: int = DEFAULT_REFILL_QUANTITY

    def __post_init__(self):
        object.__setattr__(
            self,
            "min_level",
            _integer(self.min_level, "Min", minimum=0),
        )
        object.__setattr__(
            self,
            "refill_quantity",
            _integer(self.refill_quantity, "Aanvulhoeveelheid", minimum=1),
        )

    @classmethod
    def from_values(cls, min_level=None, refill_quantity=None):
        return cls(
            DEFAULT_MIN_LEVEL if min_level is None else min_level,
            DEFAULT_REFILL_QUANTITY
            if refill_quantity is None
            else refill_quantity,
        )


def _coerce_standard(article_standard):
    if isinstance(article_standard, KanbanStandard):
        return article_standard
    raise TypeError("article_standard moet een KanbanStandard zijn.")


def _parsed_position_overrides(min_override, refill_override):
    return (
        _optional_integer(min_override, "Min", minimum=0),
        _optional_integer(
            refill_override,
            "Aanvulhoeveelheid",
            minimum=1,
        ),
    )


def normalize_material_type(value):
    """Return a canonical material type, treating old empty values as Kanban."""
    if isinstance(value, Materiaaltype):
        return value
    if value is None or not str(value).strip():
        return Materiaaltype.KANBAN

    normalized = str(value).strip().upper()
    if normalized in {Materiaaltype.KANBAN.value, "TWO_BIN"}:
        return Materiaaltype.KANBAN
    if normalized in {Materiaaltype.STANDAARD.value, "STANDARD"}:
        return Materiaaltype.STANDAARD
    raise KanbanSettingsError(f"Onbekend materiaaltype: {value}.")


@dataclass(frozen=True)
class LocatiekaartInhoud:
    """Immutable snapshot of the content that identifies a storage location."""

    artikelnaam: str
    artikel_foto_url: str | None
    bedrijfslogo_url: str | None
    vestiging_naam: str
    ruimte_naam: str
    opslaglocatie_naam: str
    kamertype_naam: str | None
    kamertype_kleur: str | None
    materiaaltype: Materiaaltype
    min_level: int | None
    refill_quantity: int | None

    def __post_init__(self):
        material_type = normalize_material_type(self.materiaaltype)
        object.__setattr__(self, "materiaaltype", material_type)
        if material_type is Materiaaltype.STANDAARD:
            if self.min_level is not None or self.refill_quantity is not None:
                raise KanbanSettingsError(
                    "Standaard materiaal kan geen Kanban-instellingen bevatten."
                )
            return

        settings = KanbanStandard.from_values(
            self.min_level,
            self.refill_quantity,
        )
        object.__setattr__(self, "min_level", settings.min_level)
        object.__setattr__(self, "refill_quantity", settings.refill_quantity)

    @property
    def fingerprint(self):
        values = {
            "artikelnaam": self.artikelnaam,
            "artikel_foto_url": self.artikel_foto_url,
            "bedrijfslogo_url": self.bedrijfslogo_url,
            "vestiging_naam": self.vestiging_naam,
            "ruimte_naam": self.ruimte_naam,
            "opslaglocatie_naam": self.opslaglocatie_naam,
            "kamertype_naam": self.kamertype_naam,
            "kamertype_kleur": self.kamertype_kleur,
            "materiaaltype": self.materiaaltype.value,
            "min_level": self.min_level,
            "refill_quantity": self.refill_quantity,
        }
        encoded = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def normalized_position_overrides(
    material_type,
    article_standard,
    min_override=None,
    refill_override=None,
):
    """Normalize position overrides so equal values are stored as inheritance."""
    material_type = normalize_material_type(material_type)
    article_standard = _coerce_standard(article_standard)

    if material_type is Materiaaltype.STANDAARD:
        return None, None

    min_value, refill_value = _parsed_position_overrides(
        min_override,
        refill_override,
    )
    return (
        None if min_value == article_standard.min_level else min_value,
        None
        if refill_value == article_standard.refill_quantity
        else refill_value,
    )


def effective_kanban_settings(
    material_type,
    article_standard,
    min_override=None,
    refill_override=None,
):
    """Resolve the effective settings or return ``None`` for Standard material."""
    material_type = normalize_material_type(material_type)
    article_standard = _coerce_standard(article_standard)
    if material_type is Materiaaltype.STANDAARD:
        return None

    min_value, refill_value = normalized_position_overrides(
        material_type,
        article_standard,
        min_override,
        refill_override,
    )
    return KanbanStandard(
        article_standard.min_level if min_value is None else min_value,
        article_standard.refill_quantity
        if refill_value is None
        else refill_value,
    )
