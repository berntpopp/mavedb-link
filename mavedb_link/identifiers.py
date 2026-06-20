"""MaveDB identifier parsing, classification, and validation.

MaveDB accessions are URNs under the ``urn:mavedb:`` namespace with an 8-digit
zero-padded base block:

- experiment set ``urn:mavedb:00000001``
- experiment     ``urn:mavedb:00000001-a``       (a letter; ``-0`` marks a meta-analysis)
- score set      ``urn:mavedb:00000001-a-1``
- variant        ``urn:mavedb:00000001-a-1#2044``  (score-set URN + ``#<index>``)

Unpublished/temporary records use ``tmp:<uuid>`` URNs. These are pure functions
with no I/O so they unit-test in isolation and are reused by services + tools.
"""

from __future__ import annotations

import re

from mavedb_link.exceptions import InvalidInputError

_BASE = r"urn:mavedb:\d{8}"
_EXPERIMENT_SET_RE = re.compile(rf"^{_BASE}$")
_EXPERIMENT_RE = re.compile(rf"^{_BASE}-[a-z0-9]+$")
_SCORE_SET_RE = re.compile(rf"^{_BASE}-[a-z0-9]+-\d+$")
_VARIANT_RE = re.compile(rf"^{_BASE}-[a-z0-9]+-\d+#\d+$")
_TMP_RE = re.compile(r"^tmp:[0-9a-fA-F-]{8,}$")

#: A loose HGNC-style gene symbol (uppercase letters/digits/hyphen, e.g. BRCA1,
#: HBB, TP53, C1orf127). Not authoritative — just enough to route input.
_GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,19}$")


def normalize_urn(value: str) -> str:
    """Trim surrounding whitespace from a candidate URN/identifier."""
    return value.strip()


def is_experiment_set_urn(value: str) -> bool:
    """Whether ``value`` is an experiment-set URN (``urn:mavedb:00000001``)."""
    return bool(_EXPERIMENT_SET_RE.match(value.strip()))


def is_experiment_urn(value: str) -> bool:
    """Whether ``value`` is an experiment URN (``urn:mavedb:00000001-a``)."""
    return bool(_EXPERIMENT_RE.match(value.strip()))


def is_score_set_urn(value: str) -> bool:
    """Whether ``value`` is a score-set URN (``urn:mavedb:00000001-a-1``)."""
    return bool(_SCORE_SET_RE.match(value.strip()))


def is_variant_urn(value: str) -> bool:
    """Whether ``value`` is a variant URN (``urn:mavedb:00000001-a-1#2044``)."""
    return bool(_VARIANT_RE.match(value.strip()))


def is_tmp_urn(value: str) -> bool:
    """Whether ``value`` is a temporary/unpublished URN (``tmp:<uuid>``)."""
    return bool(_TMP_RE.match(value.strip()))


def looks_like_mavedb_urn(value: str) -> bool:
    """Whether ``value`` looks like any MaveDB URN form."""
    return classify_urn(value) is not None


def classify_urn(value: str) -> str | None:
    """Return the entity kind for a URN, or ``None`` if it is not a MaveDB URN.

    One of ``experiment_set`` | ``experiment`` | ``score_set`` | ``variant`` |
    ``tmp``. Order matters: the most specific pattern wins.
    """
    candidate = value.strip()
    if is_variant_urn(candidate):
        return "variant"
    if is_score_set_urn(candidate):
        return "score_set"
    if is_experiment_urn(candidate):
        return "experiment"
    if is_experiment_set_urn(candidate):
        return "experiment_set"
    if is_tmp_urn(candidate):
        return "tmp"
    return None


def looks_like_gene_symbol(value: str) -> bool:
    """Whether ``value`` looks like an HGNC gene symbol (e.g. BRCA1)."""
    return bool(_GENE_SYMBOL_RE.match(value.strip()))


def score_set_urn_of_variant(value: str) -> str | None:
    """Return the parent score-set URN of a variant URN, or ``None``."""
    candidate = value.strip()
    if is_variant_urn(candidate):
        return candidate.split("#", 1)[0]
    return None


def variant_index_of(value: str) -> int | None:
    """Return the trailing ``#<index>`` of a variant URN as an int, or ``None``.

    The score table (``get_variant_scores``) and the genome mapping
    (``get_mapped_variants``) both key on this index, but a *string* sort of the
    variant URN orders ``#1, #10, #100, … #2`` (lexically), mispairing rows when
    the two are zipped. Parsing the index to an int lets callers sort/join
    numerically so the alignment actually holds.
    """
    candidate = value.strip()
    if is_variant_urn(candidate):
        return int(candidate.rsplit("#", 1)[1])
    return None


def validate_score_set_urn(value: str) -> str:
    """Return the trimmed score-set URN, or raise ``InvalidInputError``."""
    candidate = value.strip()
    if not is_score_set_urn(candidate):
        raise InvalidInputError(
            "Not a score-set URN. Expected 'urn:mavedb:00000001-a-1'.",
            field="urn",
            hint="Find a score-set URN via search_score_sets or get_gene_score_sets.",
        )
    return candidate
