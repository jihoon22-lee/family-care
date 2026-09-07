"""Lossless local page storage without whole-document serialization for identity.

Payloads contain protected source text. They are for local persistence only;
errors deliberately contain neither payload values nor source identifiers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import fields, is_dataclass
from typing import Any
from uuid import UUID

from familycare_worker.document_structure import (
    ChunkPlan,
    DocumentComponent,
    DocumentStructure,
    StructureNode,
)

_STRUCTURE_LIMIT = 512 * 1024 * 1024
_PLAN_LIMIT = 64 * 1024 * 1024
_STORAGE_KEYS = {"storage_layout", "stored_page_count", "structure_digest_sha256"}
_STRUCTURE_KEYS = {item.name for item in fields(DocumentStructure)}
_PAGE_KEYS = {
    "page",
    "page_position",
    "nodes",
    "node_positions",
    "components",
    "component_positions",
    "source_native",
    "source_ocr",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StructureStorageError(ValueError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_STRUCTURE_STORAGE_INVALID")


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise StructureStorageError
    return value


def _sequence(value: object) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise StructureStorageError
    return value


def _integer(value: object, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise StructureStorageError
    return value


def _shallow(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: getattr(value, item.name) for item in fields(value)}
    if isinstance(value, Mapping):
        return dict(_mapping(value))
    raise StructureStorageError


class _Encoder(json.JSONEncoder):
    def default(self, value: Any) -> Any:
        return _shallow(value)


def _encoded(value: object, maximum_bytes: int) -> Iterator[bytes]:
    _integer(maximum_bytes)
    encoder = _Encoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    size = 0
    try:
        for text in encoder.iterencode(value):
            encoded = text.encode("utf-8")
            size += len(encoded)
            if size > maximum_bytes:
                raise StructureStorageError
            yield encoded
    except ValueError, TypeError, OverflowError, RecursionError:
        raise StructureStorageError from None


def structure_identity(
    structure: DocumentStructure,
    plan: ChunkPlan,
    *,
    maximum_structure_bytes: int = _STRUCTURE_LIMIT,
    maximum_plan_bytes: int = _PLAN_LIMIT,
) -> tuple[str, str, int, int]:
    """Hash the exact legacy canonical structure JSON, NUL, and plan JSON stream."""
    identity, structure_digest = hashlib.sha256(), hashlib.sha256()
    structure_bytes = plan_bytes = 0
    for encoded in _encoded(structure, maximum_structure_bytes):
        identity.update(encoded)
        structure_digest.update(encoded)
        structure_bytes += len(encoded)
    identity.update(b"\0")
    logical_plan = {**_mapping(_shallow(plan)), "complete": plan.complete}
    for encoded in _encoded(logical_plan, maximum_plan_bytes):
        identity.update(encoded)
        plan_bytes += len(encoded)
    return identity.hexdigest(), structure_digest.hexdigest(), structure_bytes, plan_bytes


def _plain(value: object) -> Any:
    """Copy only one page or the small header into ordinary JSON-compatible values."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in _mapping(value).items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return _plain(_shallow(value))


def structure_header(structure: DocumentStructure, structure_digest: str) -> dict[str, Any]:
    """Keep logical metadata and empty array placeholders beside the page manifest."""
    try:
        if not isinstance(structure_digest, str) or _SHA256.fullmatch(structure_digest) is None:
            raise StructureStorageError
        return {
            "lineage": _plain(structure.lineage),
            "pages": [],
            "nodes": [],
            "components": [],
            "unresolved": _plain(structure.unresolved),
            "source_extraction": {
                **_plain(
                    {
                        key: value
                        for key, value in structure.source_extraction.items()
                        if key != "pages"
                    }
                ),
                "pages": [],
            },
            "source_ocr_pages": [],
            "storage_layout": "page-v1",
            "stored_page_count": len(structure.pages),
            "structure_digest_sha256": structure_digest,
        }
    except ValueError, TypeError, AttributeError, OverflowError, RecursionError:
        raise StructureStorageError from None


def _sources(value: object, page_numbers: set[int]) -> dict[int, tuple[int, Mapping[str, Any]]]:
    result: dict[int, tuple[int, Mapping[str, Any]]] = {}
    for position, raw in enumerate(_sequence(value)):
        page = _mapping(raw)
        number = _integer(page.get("page_number"), minimum=1, maximum=500)
        if number not in page_numbers or number in result:
            raise StructureStorageError
        result[number] = position, page
    return result


def iter_structure_pages(structure: DocumentStructure) -> Iterator[tuple[int, dict[str, Any]]]:
    """Group references once, then serialize each physical page with original positions."""
    try:
        count = _integer(len(structure.pages), minimum=1, maximum=500)
        numbers = {_integer(page.page_number, minimum=1, maximum=count) for page in structure.pages}
        if numbers != set(range(1, count + 1)):
            raise StructureStorageError
        nodes: dict[int, list[tuple[int, StructureNode]]] = defaultdict(list)
        components: dict[int, list[tuple[int, DocumentComponent]]] = defaultdict(list)
        for position, node in enumerate(structure.nodes):
            if node.page_number not in numbers:
                raise StructureStorageError
            nodes[node.page_number].append((position, node))
        for position, component in enumerate(structure.components):
            if component.page_number not in numbers:
                raise StructureStorageError
            components[component.page_number].append((position, component))
        native = _sources(structure.source_extraction.get("pages"), numbers)
        ocr = _sources(structure.source_ocr_pages, numbers)

        def source(layers: Mapping[int, tuple[int, Mapping[str, Any]]], number: int) -> Any:
            item = layers.get(number)
            return None if item is None else {"position": item[0], "page": _plain(item[1])}

        for position, page in enumerate(structure.pages):
            number = page.page_number
            yield (
                number,
                {
                    "page": _plain(page),
                    "page_position": position,
                    "nodes": [_plain(node) for _, node in nodes[number]],
                    "node_positions": [index for index, _ in nodes[number]],
                    "components": [_plain(component) for _, component in components[number]],
                    "component_positions": [index for index, _ in components[number]],
                    "source_native": source(native, number),
                    "source_ocr": source(ocr, number),
                },
            )
    except ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError:
        raise StructureStorageError from None


def _insert(target: dict[int, Any], position: object, value: Any) -> None:
    index = _integer(position)
    if index in target:
        raise StructureStorageError
    target[index] = value


def _ordered(values: dict[int, Any]) -> list[Any]:
    if set(values) != set(range(len(values))):
        raise StructureStorageError
    return [values[position] for position in range(len(values))]


def restore_structure_payload(
    header: Mapping[str, Any], pages: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Reconstruct and verify the exact logical payload, independent of fetch order."""
    try:
        header = _mapping(header)
        if (
            set(header) != _STRUCTURE_KEYS | _STORAGE_KEYS
            or header.get("storage_layout") != "page-v1"
        ):
            raise StructureStorageError
        count = _integer(header.get("stored_page_count"), minimum=1, maximum=500)
        if (
            len(pages) != count
            or any(
                header[key] != [] for key in ("pages", "nodes", "components", "source_ocr_pages")
            )
            or _mapping(header["source_extraction"]).get("pages") != []
        ):
            raise StructureStorageError
        numbers: set[int] = set()
        ordered_pages: dict[int, Any] = {}
        ordered_nodes: dict[int, Any] = {}
        ordered_components: dict[int, Any] = {}
        native: dict[int, Any] = {}
        ocr: dict[int, Any] = {}
        for raw_payload in pages:
            payload = _mapping(raw_payload)
            if set(payload) != _PAGE_KEYS:
                raise StructureStorageError
            page = _mapping(payload["page"])
            number = _integer(page.get("page_number"), minimum=1, maximum=count)
            if number in numbers:
                raise StructureStorageError
            numbers.add(number)
            _insert(ordered_pages, payload["page_position"], page)
            for key, target in (("node", ordered_nodes), ("component", ordered_components)):
                objects = _sequence(payload[f"{key}s"])
                positions = _sequence(payload[f"{key}_positions"])
                if len(objects) != len(positions):
                    raise StructureStorageError
                for position, item in zip(positions, objects, strict=True):
                    if _mapping(item).get("page_number") != number:
                        raise StructureStorageError
                    _insert(target, position, item)
            for key, target in (("source_native", native), ("source_ocr", ocr)):
                item = payload[key]
                if item is not None:
                    item = _mapping(item)
                    if (
                        set(item) != {"position", "page"}
                        or _mapping(item["page"]).get("page_number") != number
                    ):
                        raise StructureStorageError
                    _insert(target, item["position"], item["page"])
        if numbers != set(range(1, count + 1)):
            raise StructureStorageError
        restored = {key: value for key, value in header.items() if key not in _STORAGE_KEYS}
        restored.update(
            pages=_ordered(ordered_pages),
            nodes=_ordered(ordered_nodes),
            components=_ordered(ordered_components),
            source_extraction={**header["source_extraction"], "pages": _ordered(native)},
            source_ocr_pages=_ordered(ocr),
        )
        digest = hashlib.sha256()
        for encoded in _encoded(restored, _STRUCTURE_LIMIT):
            digest.update(encoded)
        if digest.hexdigest() != header["structure_digest_sha256"]:
            raise StructureStorageError
        return restored
    except ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError:
        raise StructureStorageError from None
