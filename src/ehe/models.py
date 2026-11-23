"""Canonical wire types shared by evaluators and reports."""
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .io import digest, now, sha256


@dataclass(frozen=True)
class Evidence:
    id: str
    path: str
    sha256: str
    locator: str
    kind: str
    collected_at: str
    method: str
    version: str | None = None

    @classmethod
    def file(cls, path: Path, locator: str, kind: str, method: str,
             version: str | None = None, *, content_sha256: str | None = None) -> "Evidence":
        content = content_sha256 if content_sha256 is not None else sha256(path)
        identity = digest([content, locator, kind, method, version])[:20]
        return cls(f"ev-{identity}", str(path), content, locator, kind, now(), method, version)


@dataclass(frozen=True)
class Finding:
    id: str
    category: str
    severity: str
    status: str
    scope: dict
    mechanism: str
    recommended_action: str
    verification_method: str
    evidence_ids: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    confidence: str = "medium"
    verified_by: str | None = None


@dataclass(frozen=True)
class GateResult:
    id: str
    status: str
    evidence_ids: list[str]
    reason: str
    verified_by: str
    missing_evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CanonicalProject:
    project: dict
    components: list[dict] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)
    pins: list[dict] = field(default_factory=list)
    nets: list[dict] = field(default_factory=list)
    layers: list[dict] = field(default_factory=list)
    geometry: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
