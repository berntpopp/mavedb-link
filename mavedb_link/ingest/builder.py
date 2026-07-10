"""Build the local SQLite mirror from a MaveDB bulk-dump archive.

Streams ``main.json`` (nested experimentSets -> experiments -> scoreSets) and the
per-set ``csv/`` members into a fresh SQLite database, then atomically swaps it
into place (``os.replace``) so readers never observe a half-built file. Records
are stored as the upstream camelCase JSON (the shapers consume them unchanged);
nested score sets/experiments are enriched with their parent URNs to match the
live record shape.

The dump container is format-agnostic: MaveDB shipped a ``.zip`` through v4 and
switched to ``.tar.gz`` for the 2026-06-24 dump, so :func:`build_database`
auto-detects either (by extension, falling back to magic bytes) and reads members
uniformly. The schema still accepts ``annotations`` CSV members when present, but
some dumps omit them; in that case the mirror's mapped-variant index is empty and
HybridClient lazily backfills VRS/ClinGen rows from the live API into the
mapped-variant cache.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from mavedb_link.constants import MIRROR_SCHEMA_VERSION as SCHEMA_VERSION
from mavedb_link.exceptions import DataUnavailableError
from mavedb_link.ingest.parsing import (
    compute_distribution,
    denamespace_csv,
    extract_hgvs_rows,
    extract_scores,
    parse_annotations,
)


@dataclass(frozen=True)
class ArchiveLimits:
    """Entry and expanded-byte bounds for a bulk-dump archive."""

    max_entries: int
    max_member_bytes: int
    max_expanded_bytes: int


class _DumpReader(Protocol):
    """Uniform read access to a dump archive, regardless of container format."""

    names: set[str]

    def read(self, name: str) -> bytes:
        """Return the raw bytes of one member."""
        ...


class _ZipArchive:
    """A ``.zip`` dump: members are read on demand via the central directory."""

    def __init__(
        self, zf: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], limits: ArchiveLimits
    ) -> None:
        self._zf = zf
        self._members = members
        self._limits = limits
        self.names = set(members)

    def read(self, name: str) -> bytes:
        info = self._members[name]
        output = bytearray()
        with self._zf.open(info) as source:
            while chunk := source.read(
                min(1 << 20, self._limits.max_member_bytes - len(output) + 1)
            ):
                output.extend(chunk)
                if len(output) > self._limits.max_member_bytes:
                    raise DataUnavailableError(
                        f"archive member {name} exceeds {self._limits.max_member_bytes} bytes"
                    )
        return bytes(output)


class _DirArchive:
    """A dump extracted to disk (the ``.tar.gz`` path): members are files.

    Tarballs don't support cheap random access on a gzip stream, so we extract
    once (streaming, low peak memory) and then read members as plain files --
    matching the zip path's per-member, one-CSV-at-a-time access pattern.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self.names = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

    def read(self, name: str) -> bytes:
        return (self._root / name).read_bytes()


def _is_tar_dump(dump_path: Path) -> bool:
    """True if the dump is a tarball (``.tar.gz``/``.tgz``/...), else a zip.

    Prefer the extension; fall back to magic bytes for an unknown suffix so a
    mislabelled download is still read correctly.
    """
    name = dump_path.name.lower()
    if name.endswith((".tar.gz", ".tgz", ".tar", ".tar.bz2", ".tar.xz")):
        return True
    if name.endswith(".zip"):
        return False
    with open(dump_path, "rb") as fh:
        head = fh.read(512)
    if head[:4] == b"PK\x03\x04":  # zip local-file signature
        return False
    if head[:2] == b"\x1f\x8b":  # gzip container (assume a gzipped tar)
        return True
    return head[257:262] == b"ustar"  # uncompressed tar header magic


def _dump_root(extracted: Path) -> Path:
    """The directory holding ``main.json`` (descend one wrapping dir if present)."""
    if (extracted / "main.json").exists():
        return extracted
    subdirs = [p for p in extracted.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "main.json").exists():
        return subdirs[0]
    return extracted


@contextmanager
def _open_dump(dump_path: Path, *, limits: ArchiveLimits) -> Iterator[_DumpReader]:
    """Open a dump archive (zip or tar.gz) as a uniform member reader."""
    if _is_tar_dump(dump_path):
        with (
            tempfile.TemporaryDirectory(dir=dump_path.parent, suffix=".dump") as tmp,
            tarfile.open(dump_path, mode="r:*") as tf,
        ):
            extracted = Path(tmp)
            tar_members = _preflight_tar(tf, limits)
            _extract_tar_bounded(tf, tar_members, extracted, limits)
            yield _DirArchive(_dump_root(extracted))
    else:
        with zipfile.ZipFile(dump_path) as zf:
            zip_members = _preflight_zip(zf, limits)
            yield _ZipArchive(zf, zip_members, limits)


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\x00" in name:
        raise DataUnavailableError(f"unsafe archive member: {name}")
    return path.as_posix()


def _check_archive_limits(entries: list[tuple[str, int]], limits: ArchiveLimits) -> None:
    if len(entries) > limits.max_entries:
        raise DataUnavailableError(f"archive has more than {limits.max_entries} entries")
    total = 0
    main_count = 0
    for name, size in entries:
        if size > limits.max_member_bytes:
            raise DataUnavailableError(
                f"archive member {name} exceeds {limits.max_member_bytes} bytes"
            )
        total += size
        if total > limits.max_expanded_bytes:
            raise DataUnavailableError(
                f"archive expanded size exceeds {limits.max_expanded_bytes} bytes"
            )
        if PurePosixPath(name).name == "main.json":
            main_count += 1
    if main_count != 1:
        raise DataUnavailableError("archive must contain exactly one main.json")


def _add_unique(name: str, seen: set[str]) -> str:
    normalized = _safe_member_name(name)
    if normalized in seen:
        raise DataUnavailableError(f"duplicate archive member: {normalized}")
    seen.add(normalized)
    return normalized


def _preflight_tar(
    archive: tarfile.TarFile, limits: ArchiveLimits
) -> list[tuple[str, tarfile.TarInfo]]:
    approved: list[tuple[str, tarfile.TarInfo]] = []
    entries: list[tuple[str, int]] = []
    seen: set[str] = set()
    for info in archive.getmembers():
        name = _add_unique(info.name, seen)
        if info.issym() or info.islnk():
            raise DataUnavailableError(f"archive links are not allowed: {name}")
        if not (info.isfile() or info.isdir()):
            raise DataUnavailableError(f"archive special files are not allowed: {name}")
        entries.append((name, info.size if info.isfile() else 0))
        approved.append((name, info))
    _check_archive_limits(entries, limits)
    return approved


def _extract_tar_bounded(
    archive: tarfile.TarFile,
    approved: list[tuple[str, tarfile.TarInfo]],
    destination: Path,
    limits: ArchiveLimits,
) -> None:
    total = 0
    for name, info in approved:
        target = destination.joinpath(*PurePosixPath(name).parts)
        if info.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        source = archive.extractfile(info)
        if source is None:
            raise DataUnavailableError(f"archive member {name} could not be read")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with source, target.open("wb") as output:
            while chunk := source.read(min(1 << 20, limits.max_member_bytes - written + 1)):
                written += len(chunk)
                total += len(chunk)
                if written > limits.max_member_bytes:
                    raise DataUnavailableError(
                        f"archive member {name} exceeds {limits.max_member_bytes} bytes"
                    )
                if total > limits.max_expanded_bytes:
                    raise DataUnavailableError(
                        f"archive expanded size exceeds {limits.max_expanded_bytes} bytes"
                    )
                output.write(chunk)
        if written != info.size:
            raise DataUnavailableError(
                f"archive member {name} size mismatch: expected {info.size}, received {written}"
            )


def _preflight_zip(archive: zipfile.ZipFile, limits: ArchiveLimits) -> dict[str, zipfile.ZipInfo]:
    approved: dict[str, zipfile.ZipInfo] = {}
    entries: list[tuple[str, int]] = []
    seen: set[str] = set()
    for info in archive.infolist():
        name = _add_unique(info.filename, seen)
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise DataUnavailableError(f"archive links are not allowed: {name}")
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise DataUnavailableError(f"archive special files are not allowed: {name}")
        entries.append((name, 0 if info.is_dir() else info.file_size))
        if not info.is_dir():
            approved[name] = info
    _check_archive_limits(entries, limits)
    return approved


def _schema_sql() -> str:
    """Load the bundled schema DDL."""
    return resources.files("mavedb_link.data").joinpath("schema.sql").read_text(encoding="utf-8")


def _csv_member(urn: str, suffix: str) -> str:
    """Dump CSV member name for a score set URN (``urn:`` -> ``-`` in filenames)."""
    return f"csv/{urn.replace(':', '-')}.{suffix}.csv"


def _target_genes(score_set: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a score set's target genes into ``gene_index`` row dicts."""
    rows: list[dict[str, Any]] = []
    for target in score_set.get("targetGenes") or []:
        name = target.get("name")
        if not name:
            continue
        taxonomy = (target.get("targetSequence") or {}).get("taxonomy") or {}
        rows.append(
            {
                "gene_symbol_upper": str(name).upper(),
                "gene_symbol": name,
                "score_set_urn": score_set["urn"],
                "organism": taxonomy.get("organismName"),
                "category": target.get("category"),
            }
        )
    return rows


def _fts_fields(score_set: dict[str, Any]) -> tuple[str, str]:
    """The (genes, authors) free-text blobs for the FTS row."""
    genes = " ".join(
        str(t.get("name")) for t in (score_set.get("targetGenes") or []) if t.get("name")
    )
    authors: list[str] = []
    for pub in score_set.get("primaryPublicationIdentifiers") or []:
        for author in pub.get("authors") or []:
            if author.get("name"):
                authors.append(str(author["name"]))
    return genes, " ".join(authors)


def _empty_mapping_coverage() -> dict[str, int]:
    """Initial mappingState coverage counters for diagnostics."""
    return {"complete": 0, "incomplete": 0, "failed": 0, "none": 0}


def _count_mapping_state(coverage: dict[str, int], score_set: dict[str, Any]) -> None:
    """Increment the mapping coverage bucket for one score set."""
    state = str(score_set.get("mappingState") or "").strip().lower()
    if state not in ("complete", "incomplete", "failed"):
        state = "none"
    coverage[state] += 1


def _insert_score_set(
    con: sqlite3.Connection,
    zf: _DumpReader,
    zip_names: set[str],
    score_set: dict[str, Any],
) -> int:
    """Insert one score set + its CSV blobs/derived rows. Returns mapped-variant count."""
    urn = score_set["urn"]
    has_cal = 1 if score_set.get("scoreCalibrations") else 0
    con.execute(
        "INSERT INTO score_set (urn, experiment_urn, experiment_set_urn, title, "
        "short_description, license, num_variants, published_date, has_calibrations, "
        "record_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            urn,
            (score_set.get("experiment") or {}).get("urn"),
            score_set.get("experimentSetUrn"),
            score_set.get("title"),
            score_set.get("shortDescription"),
            (score_set.get("license") or {}).get("shortName"),
            score_set.get("numVariants"),
            score_set.get("publishedDate"),
            has_cal,
            json.dumps(score_set),
        ),
    )
    genes, authors = _fts_fields(score_set)
    con.execute(
        "INSERT INTO score_set_fts (urn, title, short_description, genes, authors) "
        "VALUES (?,?,?,?,?)",
        (
            urn,
            score_set.get("title") or "",
            score_set.get("shortDescription") or "",
            genes,
            authors,
        ),
    )
    gene_rows = _target_genes(score_set)
    if gene_rows:
        con.executemany(
            "INSERT INTO gene_index (gene_symbol_upper, gene_symbol, score_set_urn, organism, "
            "category) VALUES (:gene_symbol_upper, :gene_symbol, :score_set_urn, :organism, "
            ":category)",
            gene_rows,
        )

    scores_csv = _read_member(zf, zip_names, urn, "scores")
    counts_csv = _read_member(zf, zip_names, urn, "counts")
    annotations_csv = _read_member(zf, zip_names, urn, "annotations")
    if scores_csv is not None or counts_csv is not None or annotations_csv is not None:
        con.execute(
            "INSERT INTO score_set_data (urn, scores_csv, counts_csv, annotations_csv) "
            "VALUES (?,?,?,?)",
            (urn, scores_csv, counts_csv, annotations_csv),
        )
    if scores_csv is not None:
        dist = compute_distribution(extract_scores(scores_csv))
        con.execute(
            "INSERT INTO score_distribution (score_set_urn, n, min, max, mean, histogram_json, "
            "quantiles_json) VALUES (?,?,?,?,?,?,?)",
            (
                urn,
                dist["n"],
                dist["min"],
                dist["max"],
                dist["mean"],
                json.dumps(dist["histogram"]),
                json.dumps(dist["quantiles"]),
            ),
        )
        hgvs_rows = extract_hgvs_rows(scores_csv, urn)
        if hgvs_rows:
            con.executemany(
                "INSERT INTO hgvs_index (score_set_urn, variant_urn, hgvs_nt, hgvs_pro, "
                "hgvs_splice) VALUES (:score_set_urn, :variant_urn, :hgvs_nt, :hgvs_pro, "
                ":hgvs_splice)",
                hgvs_rows,
            )
    mapped_count = 0
    if annotations_csv is not None:
        mapped = parse_annotations(annotations_csv, urn)
        if mapped:
            con.executemany(
                "INSERT INTO mapped_variant (variant_urn, score_set_urn, vrs_id, "
                "clingen_allele_id, post_mapped_hgvs_g, post_mapped_hgvs_p, post_mapped_hgvs_c) "
                "VALUES (:variant_urn, :score_set_urn, :vrs_id, :clingen_allele_id, "
                ":post_mapped_hgvs_g, :post_mapped_hgvs_p, :post_mapped_hgvs_c)",
                mapped,
            )
            mapped_count = len(mapped)
    return mapped_count


def _read_member(zf: _DumpReader, zip_names: set[str], urn: str, suffix: str) -> str | None:
    """Read one CSV member on demand (denamespaced), or None if absent.

    Read per-member rather than caching the whole dump, so peak memory stays at
    one CSV -- the real dump is ~1.8 GB uncompressed across thousands of files.
    """
    name = _csv_member(urn, suffix)
    if name not in zip_names:
        return None
    return denamespace_csv(zf.read(name).decode("utf-8"))


def build_database(
    dump_path: Path,
    db_path: Path,
    *,
    source_md5: str | None = None,
    source_sha256: str | None = None,
    source_url: str | None = None,
    zenodo_record: str | None = None,
    zenodo_version: str | None = None,
    archive_limits: ArchiveLimits | None = None,
) -> dict[str, Any]:
    """Build ``db_path`` from the dump archive atomically; return a provenance summary."""
    if archive_limits is None:
        from mavedb_link.config import settings

        archive_limits = ArchiveLimits(
            max_entries=settings.mirror.max_archive_entries,
            max_member_bytes=settings.mirror.max_archive_member_bytes,
            max_expanded_bytes=settings.mirror.max_archive_expanded_bytes,
        )
    started = time.monotonic()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=db_path.parent, suffix=".sqlite.tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with _open_dump(dump_path, limits=archive_limits) as zf:
            zip_names = zf.names
            main = json.loads(zf.read("main.json"))
            summary = _populate(tmp_path, main, zf, zip_names, started)
        _write_meta(
            tmp_path,
            summary,
            source_md5,
            source_sha256,
            source_url,
            zenodo_record,
            zenodo_version,
            started,
        )
        os.replace(tmp_path, db_path)
        return summary
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _populate(
    tmp_path: Path,
    main: dict[str, Any],
    zf: _DumpReader,
    zip_names: set[str],
    started: float,
) -> dict[str, Any]:
    """Create the schema and insert every record; return the counts summary."""
    con = sqlite3.connect(tmp_path)
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.executescript(_schema_sql())
        es_count = exp_count = ss_count = mapped_count = 0
        mapping_coverage = _empty_mapping_coverage()
        for es in main.get("experimentSets") or []:
            es_count += 1
            con.execute(
                "INSERT INTO experiment_set (urn, title, record_json) VALUES (?,?,?)",
                (es.get("urn"), es.get("title"), json.dumps(_strip_children(es, "experiments"))),
            )
            for exp in es.get("experiments") or []:
                exp_count += 1
                exp = {**exp, "experimentSetUrn": es.get("urn")}
                con.execute(
                    "INSERT INTO experiment (urn, experiment_set_urn, title, short_description, "
                    "record_json) VALUES (?,?,?,?,?)",
                    (
                        exp.get("urn"),
                        es.get("urn"),
                        exp.get("title"),
                        exp.get("shortDescription"),
                        json.dumps(_strip_children(exp, "scoreSets")),
                    ),
                )
                for score_set in exp.get("scoreSets") or []:
                    ss_count += 1
                    _count_mapping_state(mapping_coverage, score_set)
                    enriched = {
                        **score_set,
                        "experiment": {"urn": exp.get("urn")},
                        "experimentSetUrn": es.get("urn"),
                    }
                    mapped_count += _insert_score_set(con, zf, zip_names, enriched)
        con.commit()
        con.execute("INSERT INTO score_set_fts (score_set_fts) VALUES ('optimize')")
        con.commit()
    finally:
        con.close()
    return {
        "dump_as_of": main.get("asOf"),
        "experiment_set_count": es_count,
        "experiment_count": exp_count,
        "score_set_count": ss_count,
        "mapped_variant_count": mapped_count,
        "mapping_coverage": mapping_coverage,
        "schema_version": SCHEMA_VERSION,
    }


def _strip_children(record: dict[str, Any], child_key: str) -> dict[str, Any]:
    """A copy of a parent record without its (separately-stored) child collection."""
    return {k: v for k, v in record.items() if k != child_key}


def _write_meta(
    tmp_path: Path,
    summary: dict[str, Any],
    source_md5: str | None,
    source_sha256: str | None,
    source_url: str | None,
    zenodo_record: str | None,
    zenodo_version: str | None,
    started: float,
) -> None:
    """Write the single provenance row."""
    con = sqlite3.connect(tmp_path)
    try:
        con.execute(
            "INSERT INTO meta (id, schema_version, dump_as_of, zenodo_record, zenodo_version, "
            "source_url, source_md5, source_sha256, experiment_set_count, experiment_count, score_set_count, "
            "mapped_variant_count, mapping_coverage_json, build_utc, build_duration_s) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                SCHEMA_VERSION,
                summary["dump_as_of"],
                zenodo_record,
                zenodo_version,
                source_url,
                source_md5,
                source_sha256,
                summary["experiment_set_count"],
                summary["experiment_count"],
                summary["score_set_count"],
                summary["mapped_variant_count"],
                json.dumps(summary["mapping_coverage"], sort_keys=True),
                datetime.now(UTC).isoformat(),
                round(time.monotonic() - started, 3),
            ),
        )
        con.commit()
    finally:
        con.close()
