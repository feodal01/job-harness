"""Source rules a brief keeps instead of a source list; the list is built at each run start.

See docs/brief-parameters/source-rules.md.
"""

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceGroup = Literal["aggregator", "it_board", "company_site", "telegram"]
COMPANY_PREFIX = "company:"
MAX_RULE_ITEMS = 1000


def company_id(item: str) -> str | None:
    """The catalog company UUID of a `company:<uuid>` rule item, or None for a source id."""
    return item.removeprefix(COMPANY_PREFIX) if item.startswith(COMPANY_PREFIX) else None


def unknown_sources(rules: "SourceRules", searchable: Callable[[str], bool]) -> list[str]:
    """Rule items that name no searchable source; companies are checked against the catalog at run start."""
    items = dict.fromkeys([*rules.include, *rules.exclude])
    return [item for item in items if company_id(item) is None and not searchable(item)]


def _clean(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if not item or len(item) > 500 or any(c.isspace() for c in item):
            raise ValueError("Each item is a source id or company:<uuid> without spaces")
        if (company := company_id(item)) is not None:
            try:
                item = COMPANY_PREFIX + str(UUID(company))
            except ValueError:
                raise ValueError(f"{item}: company needs a catalog UUID") from None
        cleaned.append(item)
    return list(dict.fromkeys(cleaned))


class SourceRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "selected"] = Field(
        default="auto",
        description="auto: every supported source of the groups covering the brief's countries; "
        "selected: only include",
    )
    groups: list[SourceGroup] | None = Field(
        default=None, description="Source types for auto; omitted means every type"
    )
    include: list[str] = Field(
        default_factory=list,
        max_length=MAX_RULE_ITEMS,
        description="Added to the rules: a source id (hh_ru, ats:lever:acme) or company:<catalog uuid>",
    )
    exclude: list[str] = Field(
        default_factory=list,
        max_length=MAX_RULE_ITEMS,
        description="Removed after the rules: a source id or company:<catalog uuid>",
    )

    @field_validator("include", "exclude")
    @classmethod
    def clean_items(cls, values: list[str]) -> list[str]:
        return _clean(values)

    @field_validator("groups")
    @classmethod
    def unique_groups(cls, values: list[SourceGroup] | None) -> list[SourceGroup] | None:
        return None if values is None else list(dict.fromkeys(values))

    @model_validator(mode="after")
    def something_to_search(self) -> "SourceRules":
        if self.mode == "selected" and self.groups is not None:
            raise ValueError("groups apply to mode=auto only; selected searches include")
        if not self.include and (self.mode == "selected" or self.groups == []):
            raise ValueError("Choose at least one source")
        both = sorted(set(self.include) & set(self.exclude))
        if both:
            raise ValueError(f"Both included and excluded: {', '.join(both)}")
        return self
