"""STT/TTS provider routing table with a verified-quirks registry (roadmap M7).

Per-language routing (primary → fallback chain, plus the reserved `mixed`
code-switching chain) over provider adapters that each carry capability
metadata. Passenger state only: the live turn loop (M7) is the consumer;
this module owns the routing decision and its honesty, not the audio.

The verified-quirks registry adopts the roadmap honesty pattern: competing
implementations document exact per-language provider failures WITH dates;
an unverified (provider, modality, language) combination is labeled
UNVERIFIED in status output and is never silently attempted.

Registry starts EMPTY — deliberately. Nothing in this repository documents
competitor per-language provider failures with dates (grep `docs/` for
STT/TTS failure notes: none exist), so fabricating "verified" entries would
be dishonest. Verified entries may only be injected with observed field
data: an exact failure note, an ISO verification date, and explicit
provenance text. Until then every combination reads UNVERIFIED.

Refusal contract (tested exactly in tests/test_voice_providers.py):
- `resolve(language)` is pure routing: the configured chain (exact
  language match, else `mixed`) or None when honestly unroutable — never a
  fabricated provider, never a silent fallback beyond the configured ones.
- `effective_adapter(language, modality)` returns the primary adapter for
  the modality ONLY when (provider, modality, language) has a verified
  quirk entry; otherwise it returns None — the unverified combination is
  refused, not attempted.
- `attempt_report(language, modality)` always returns the resolved chain
  (the caller keeps the information needed for an explicit operator
  decision) together with per-step statuses where every entry without a
  verified observation carries the literal `UNVERIFIED` label, plus an
  explicit refusal record when effective_adapter would return None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Settings

MIXED_LANGUAGE = "mixed"
MODALITIES = ("stt", "tts")
VERIFIED = "verified"
UNVERIFIED = "UNVERIFIED"  # literal roadmap label, never lowercased
NO_ROUTE = "no_route"
NO_STEP_FOR_MODALITY = "no_step_for_modality"


def adapter_id(modality: str, name: str) -> str:
    """Canonical unique id for one (modality, provider-name) adapter."""
    return f"{modality}:{name}"


def normalize_language(language: str) -> str:
    """Normalize a language code for table keys (exact-match, fail honest)."""
    if not isinstance(language, str) or not language.strip():
        raise ValueError("language must be a non-empty string")
    return language.strip().lower()


@dataclass(frozen=True)
class ProviderAdapter:
    """One STT or TTS provider handle plus its capability metadata."""

    name: str
    modality: str
    capabilities: Dict[str, Any] = field(default_factory=dict)
    handle: Optional[Any] = None  # transport/client object; fakes in tests

    @property
    def id(self) -> str:
        return adapter_id(self.modality, self.name)


@dataclass(frozen=True)
class VerifiedQuirk:
    """One observed per-provider per-language behavior, dated and sourced."""

    provider: str
    modality: str
    language: str
    note: str
    date: str  # ISO date of the observation (verification anchor)
    provenance: str  # explicit provenance text — never "we think"


class VerifiedQuirks:
    """Registry of verified quirk observations.

    Keyed by (provider, modality, normalized language); the most recent
    observation (max `date`) answers lookups. Empty by default: every
    combination therefore labels UNVERIFIED until dated, provenanced field
    data is injected. Entries without a non-empty date/provenance are
    rejected — an unverifiable note MUST not masquerade as verified.
    """

    def __init__(self, entries: Optional[Iterable[VerifiedQuirk]] = None) -> None:
        self._entries: Dict[Tuple[str, str, str], List[VerifiedQuirk]] = {}
        for entry in entries or ():
            self.add(entry)

    def add(self, entry: VerifiedQuirk) -> None:
        if entry.modality not in MODALITIES:
            raise ValueError(
                f"quirk modality must be 'stt' or 'tts', got {entry.modality!r}"
            )
        if not entry.provider or not entry.note:
            raise ValueError("quirk requires non-empty provider and note")
        if not entry.date or not entry.provenance:
            raise ValueError("quirk requires a verification date and provenance")
        key = (entry.provider, entry.modality, normalize_language(entry.language))
        self._entries.setdefault(key, []).append(entry)
        self._entries[key].sort(key=lambda item: item.date)

    def lookup(
        self, provider: str, modality: str, language: str
    ) -> Optional[VerifiedQuirk]:
        """Most recent verified observation, or None when only UNVERIFIED."""
        bucket = self._entries.get((provider, modality, normalize_language(language)))
        if not bucket:
            return None
        return bucket[-1]

    def label(self, provider: str, modality: str, language: str) -> str:
        return VERIFIED if self.lookup(provider, modality, language) else UNVERIFIED

    def entries(self) -> List[VerifiedQuirk]:
        """All registry observations, chronologically flattened."""
        flat: List[VerifiedQuirk] = []
        for bucket in self._entries.values():
            flat.extend(bucket)
        return sorted(flat, key=lambda item: item.date)


_EMPTY_QUIRKS = VerifiedQuirks()


class LanguageRoutingTable:
    """Per-language provider chains plus the reserved `mixed` chain."""

    def __init__(
        self,
        adapters: Iterable[ProviderAdapter],
        chains: Optional[Dict[str, List[str]]] = None,
        quirks: Optional[VerifiedQuirks] = None,
    ) -> None:
        self._adapters: Dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            if not isinstance(adapter, ProviderAdapter):
                raise ValueError("adapters must be ProviderAdapter records")
            if adapter.id in self._adapters:
                raise ValueError(f"duplicate adapter id {adapter.id!r}")
            self._adapters[adapter.id] = adapter
        self._chains: Dict[str, List[str]] = {}
        for language, steps in dict(chains or {}).items():
            key = normalize_language(language)
            step_ids: List[str] = []
            for step in steps or ():
                if not isinstance(step, str) or not step.strip():
                    raise ValueError(f"chain {key!r} steps must be adapter ids")
                if step not in self._adapters:
                    raise ValueError(
                        f"chain {key!r} references unknown adapter {step!r}"
                    )
                step_ids.append(step)
            self._chains[key] = step_ids
        self._quirks = quirks if quirks is not None else _EMPTY_QUIRKS

    @property
    def quirks(self) -> VerifiedQuirks:
        return self._quirks

    @property
    def languages(self) -> List[str]:
        """Configured table keys, `mixed` last."""
        exact = [key for key in self._chains if key != MIXED_LANGUAGE]
        if MIXED_LANGUAGE in self._chains:
            return exact + [MIXED_LANGUAGE]
        return exact

    def resolve(self, language: str) -> Optional[List[ProviderAdapter]]:
        """Pure routing: exact-language chain, else `mixed`, else None.

        An explicitly EMPTY chain for a language is honest None as well —
        an operator configuring `[]` disables routing for that language and
        MUST NOT silently inherit the `mixed` chain.
        """
        key = normalize_language(language)
        ids = self._chains[key] if key in self._chains else self._chains.get(MIXED_LANGUAGE, [])
        if not ids:
            return None
        return [self._adapters[step] for step in ids]

    def effective_adapter(
        self, language: str, modality: str
    ) -> Optional[ProviderAdapter]:
        """Primary adapter for (language, modality) — or an honest refusal.

        Returns None unless the first chain step matching the modality has
        a verified quirk observation for THIS language; unverified
        combinations are never silently attempted. Resolved-chain access
        for explicit caller decisions stays available via resolve() and
        attempt_report(), which carry the UNVERIFIED labels.
        """
        report = self.attempt_report(language, modality)
        for step in report["steps"]:
            if step["verified"]:
                return self._adapters[step["adapterId"]]
        return None

    def attempt_report(self, language: str, modality: str) -> Dict[str, Any]:
        """JSON-able attempt report: chain + per-step labels + refusal."""
        language_key = normalize_language(language)
        if modality not in MODALITIES:
            raise ValueError(f"modality must be 'stt' or 'tts', got {modality!r}")
        chain = self.resolve(language)
        steps: List[Dict[str, Any]] = []
        for position, adapter in enumerate(chain or ()):
            quirk = self._quirks.lookup(adapter.name, adapter.modality, language_key)
            status = self._quirks.label(adapter.name, adapter.modality, language_key)
            steps.append(
                {
                    "position": position,
                    "provider": adapter.name,
                    "modality": adapter.modality,
                    "adapterId": adapter.id,
                    "capabilities": dict(adapter.capabilities),
                    "status": status,
                    "verified": status == VERIFIED,
                    "date": quirk.date if quirk else None,
                    "provenance": quirk.provenance if quirk else None,
                    "note": quirk.note if quirk else None,
                }
            )
        refusal: Optional[Dict[str, Any]] = None
        if chain is None:
            refusal = {
                "reason": NO_ROUTE,
                "label": NO_ROUTE,
                "message": (
                    f"no configured chain for language {language_key!r} and no"
                    f" {MIXED_LANGUAGE!r} fallback — refusing honestly"
                ),
            }
        else:
            matching = [step for step in steps if step["modality"] == modality]
            if not matching:
                refusal = {
                    "reason": NO_STEP_FOR_MODALITY,
                    "label": NO_STEP_FOR_MODALITY,
                    "message": (
                        f"chain for {language_key!r} has no {modality!r} step"
                    ),
                }
            elif not matching[0]["verified"]:
                refusal = {
                    "reason": "unverified",
                    "label": UNVERIFIED,
                    "message": (
                        f"{matching[0]['provider']!r} {modality} for"
                        f" {language_key!r} is UNVERIFIED — not attempted"
                    ),
                }
        return {
            "language": language_key,
            "modality": modality,
            "resolved": chain is not None,
            "steps": steps,
            "attemptable": refusal is None,
            "refusal": refusal,
        }

    def status(self) -> Dict[str, Any]:
        """Full JSON-able status: every unverified combo labeled UNVERIFIED."""
        quirks = self._quirks.entries()
        return {
            "quirksRegistry": {
                "verifiedEntryCount": len(quirks),
                "entries": [
                    {
                        "provider": entry.provider,
                        "modality": entry.modality,
                        "language": normalize_language(entry.language),
                        "note": entry.note,
                        "date": entry.date,
                        "provenance": entry.provenance,
                    }
                    for entry in quirks
                ],
                "note": (
                    "Registry starts empty of fabricated entries: no doc in this"
                    " repository records competitor per-language provider"
                    " failures with dates. Entries require observed field data"
                    " (exact note + ISO date + explicit provenance)."
                ),
            },
            "chains": [
                {
                    "language": language,
                    "mixed": language == MIXED_LANGUAGE,
                    "steps": self.attempt_report(language, step_modality)["steps"],
                }
                for language in self.languages
                for step_modality in MODALITIES
            ],
        }


def provider_status(table: LanguageRoutingTable) -> Dict[str, Any]:
    """Module-level status facade over LanguageRoutingTable.status()."""
    status = table.status()
    chains: List[Dict[str, Any]] = []
    for language in table.languages:
        for modality in MODALITIES:
            report = table.attempt_report(language, modality)
            if not report["steps"]:
                continue
            chains.append(
                {
                    "language": language,
                    "modality": modality,
                    "mixed": language == MIXED_LANGUAGE,
                    "steps": report["steps"],
                    "attemptable": report["attemptable"],
                    "refusal": report["refusal"],
                }
            )
    status["chains"] = chains
    return status


def adapter_from_spec(spec: Dict[str, Any]) -> ProviderAdapter:
    """Build a ProviderAdapter from a Settings/env spec object, fail-closed.

    The spec carries no live handle (that is runtime wiring, not config);
    `handle` may still ride along for tests to inject fakes.
    """
    if not isinstance(spec, dict):
        raise ValueError("voice provider adapter spec must be an object")
    name = spec.get("name")
    modality = spec.get("modality")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("voice provider adapter spec requires a non-empty 'name'")
    if modality not in MODALITIES:
        raise ValueError(
            f"voice provider adapter modality must be 'stt' or 'tts', got {modality!r}"
        )
    capabilities = spec.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise ValueError("voice provider adapter 'capabilities' must be an object")
    return ProviderAdapter(
        name=name.strip(),
        modality=modality,
        capabilities=dict(capabilities),
        handle=spec.get("handle"),
    )


def validate_routing_config(
    adapters: Dict[str, Dict[str, Any]], chains: Dict[str, List[str]]
) -> None:
    """Validate the raw Settings dicts; raise ValueError on any dishonesty.

    Adapter map keys MUST be the canonical `<modality>:<name>` id; chain
    steps MUST reference configured adapter ids; language keys must be
    non-empty strings. Empty dicts pass (feature defaults off).
    """
    for key, spec in adapters.items():
        adapter = adapter_from_spec(spec)
        if key != adapter.id:
            raise ValueError(
                f"voice provider adapter key {key!r} must be canonical id"
                f" {adapter.id!r}"
            )
    for language, steps in chains.items():
        if not isinstance(language, str) or not language.strip():
            raise ValueError("voice provider chain language keys must be non-empty")
        for step in steps or ():
            if step not in adapters:
                raise ValueError(
                    f"voice provider chain {language.strip()!r} references"
                    f" unknown adapter {step!r}"
                )


def routing_table_from_settings(settings: Settings) -> LanguageRoutingTable:
    """Build the routing table from Settings, rejecting malformed config."""
    raw_adapters = dict(settings.voice_provider_adapters or {})
    chains = dict(settings.voice_provider_chains or {})
    validate_routing_config(raw_adapters, chains)
    adapters = [adapter_from_spec(spec) for spec in raw_adapters.values()]
    return LanguageRoutingTable(adapters=adapters, chains=chains)


