"""Domain models for assigning reusable AI entities to bodies.

This module intentionally has no knowledge of FastAPI, databases, or hardware
drivers.  It defines the configuration boundary that those adapters can use
later, including simulated bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class ConfigurationError(ValueError):
    """Raised when an entity/body deployment configuration is unsafe."""


class Capability(str, Enum):
    SPEAKER = "speaker"
    MICROPHONE = "microphone"
    BUTTON = "button"
    CAMERA = "camera"
    MOTION = "motion"


class BodyKind(str, Enum):
    PHYSICAL = "physical"
    SIMULATION = "simulation"


def _required_id(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ConfigurationError(f"{field} must be a non-empty string")
    return result


def _capabilities(values: Iterable[Any], field: str) -> frozenset[Capability]:
    try:
        return frozenset(
            value
            if isinstance(value, Capability)
            else Capability(str(value).strip().lower())
            for value in values
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field} contains an unknown capability") from exc


@dataclass(frozen=True)
class EntityProfile:
    """Reusable AI identity, independent of any body or deployment."""

    id: str
    display_name: str

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> EntityProfile:
        entity_id = _required_id(config.get("id"), "entity.id")
        return cls(
            id=entity_id,
            display_name=_required_id(
                config.get("display_name", entity_id), "entity.display_name"
            ),
        )


@dataclass(frozen=True)
class BodyProfile:
    """A physical or simulated body and the capabilities it actually has."""

    id: str
    display_name: str
    kind: BodyKind
    capabilities: frozenset[Capability]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> BodyProfile:
        body_id = _required_id(config.get("id"), "body.id")
        raw_kind = config.get("kind", BodyKind.PHYSICAL)
        try:
            kind = (
                raw_kind
                if isinstance(raw_kind, BodyKind)
                else BodyKind(str(raw_kind).strip().lower())
            )
        except ValueError as exc:
            raise ConfigurationError("body.kind must be physical or simulation") from exc
        return cls(
            id=body_id,
            display_name=_required_id(
                config.get("display_name", body_id), "body.display_name"
            ),
            kind=kind,
            capabilities=_capabilities(
                config.get("capabilities", ()), "body.capabilities"
            ),
        )


@dataclass(frozen=True)
class DeploymentBinding:
    """Assignment of an entity to a body with least-privilege capability grants."""

    id: str
    entity_id: str
    body_id: str
    capability_grants: frozenset[Capability]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> DeploymentBinding:
        return cls(
            id=_required_id(config.get("id"), "binding.id"),
            entity_id=_required_id(config.get("entity_id"), "binding.entity_id"),
            body_id=_required_id(config.get("body_id"), "binding.body_id"),
            capability_grants=_capabilities(
                config.get("capability_grants", ()),
                "binding.capability_grants",
            ),
        )

    def validate(self, entity: EntityProfile, body: BodyProfile) -> None:
        if self.entity_id != entity.id:
            raise ConfigurationError(
                f"binding {self.id!r} refers to unknown entity {self.entity_id!r}"
            )
        if self.body_id != body.id:
            raise ConfigurationError(
                f"binding {self.id!r} refers to unknown body {self.body_id!r}"
            )
        unavailable = self.capability_grants - body.capabilities
        if unavailable:
            names = ", ".join(sorted(item.value for item in unavailable))
            raise ConfigurationError(
                f"binding {self.id!r} grants capabilities absent from body "
                f"{body.id!r}: {names}"
            )


@dataclass(frozen=True)
class DeploymentCatalog:
    """Validated, lookup-friendly configuration snapshot."""

    entities: Mapping[str, EntityProfile]
    bodies: Mapping[str, BodyProfile]
    bindings: Mapping[str, DeploymentBinding]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> DeploymentCatalog:
        entities = _indexed(
            (EntityProfile.from_config(item) for item in config.get("entities", ())),
            "entity",
        )
        bodies = _indexed(
            (BodyProfile.from_config(item) for item in config.get("bodies", ())),
            "body",
        )
        bindings = _indexed(
            (
                DeploymentBinding.from_config(item)
                for item in config.get("bindings", ())
            ),
            "binding",
        )
        for binding in bindings.values():
            try:
                entity = entities[binding.entity_id]
            except KeyError as exc:
                raise ConfigurationError(
                    f"binding {binding.id!r} refers to unknown entity "
                    f"{binding.entity_id!r}"
                ) from exc
            try:
                body = bodies[binding.body_id]
            except KeyError as exc:
                raise ConfigurationError(
                    f"binding {binding.id!r} refers to unknown body "
                    f"{binding.body_id!r}"
                ) from exc
            binding.validate(entity, body)
        return cls(entities=entities, bodies=bodies, bindings=bindings)


def _indexed(items: Iterable[Any], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if item.id in result:
            raise ConfigurationError(f"duplicate {label} id: {item.id!r}")
        result[item.id] = item
    return result
