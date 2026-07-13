"""``mavedb-link-data`` CLI: acquire / build / refresh / publish the mirror.

Subcommands mirror the GeneFoundry data-CLI convention:

- ``bootstrap`` -- container entrypoint contract: reuse an existing DB, else pull
  a prebuilt artifact, else build locally; on total failure it exits 0 so the
  server still starts live-only (the live API is the backup).
- ``build`` -- download the latest Zenodo dump (or use a local ``--dump``) and
  build the SQLite mirror.
- ``refresh`` -- rebuild only when Zenodo has a newer version than the local DB.
- ``status`` -- print the local mirror's provenance.
- ``pull`` / ``pack`` / ``publish`` -- prebuilt-artifact transport (GitHub Releases).
"""

from __future__ import annotations

from pathlib import Path

import typer

from mavedb_link.config import MirrorConfig
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.exceptions import DataUnavailableError, MaveDBError
from mavedb_link.ingest import bundle
from mavedb_link.ingest.builder import build_database
from mavedb_link.ingest.downloader import DumpRef, download_file, resolve_latest_dump
from mavedb_link.ingest.lock import build_lock

app = typer.Typer(add_completion=False, help="Build and manage the local MaveDB mirror.")


def _config() -> MirrorConfig:
    from mavedb_link.config import settings

    return settings.mirror


def _download_and_build(cfg: MirrorConfig, ref: DumpRef) -> dict[str, object]:
    dump_path = cfg.data_dir / ref.filename
    typer.echo(f"Downloading {ref.filename} (v{ref.version}, {ref.size} bytes) ...")
    source_sha256 = download_file(
        ref.url,
        dump_path,
        expected_md5=ref.md5,
        expected_size=ref.size,
        max_bytes=cfg.max_dump_bytes,
        max_seconds=cfg.max_download_seconds,
    )
    with build_lock(cfg.data_dir / ".build.lock"):
        summary = build_database(
            dump_path,
            cfg.db_path,
            source_md5=ref.md5,
            source_sha256=source_sha256,
            source_url=ref.url,
            zenodo_record=ref.record_id,
            zenodo_version=ref.version,
        )
    typer.echo(
        f"Built {cfg.db_path}: {summary['score_set_count']} score sets (as of {summary['dump_as_of']})."
    )
    return summary


@app.command()
def build(
    dump: Path | None = typer.Option(None, help="Local dump zip; omit to download latest."),
) -> None:
    """Build the mirror from a local dump, or download the latest from Zenodo."""
    cfg = _config()
    if dump is not None:
        with build_lock(cfg.data_dir / ".build.lock"):
            summary = build_database(dump, cfg.db_path)
        typer.echo(f"Built {cfg.db_path}: {summary['score_set_count']} score sets.")
        return
    _download_and_build(
        cfg,
        resolve_latest_dump(
            cfg.zenodo_concept_id,
            max_dump_bytes=cfg.max_dump_bytes,
            max_metadata_bytes=cfg.max_metadata_bytes,
        ),
    )


@app.command()
def refresh() -> None:
    """Rebuild only if Zenodo has a newer dump version than the local mirror."""
    cfg = _config()
    ref = resolve_latest_dump(
        cfg.zenodo_concept_id,
        max_dump_bytes=cfg.max_dump_bytes,
        max_metadata_bytes=cfg.max_metadata_bytes,
    )
    repo = MirrorRepository.open(cfg.db_path)
    if repo is not None:
        current = repo.meta().get("zenodo_record")
        repo.close()
        if current == ref.record_id:
            typer.echo(f"Up to date (Zenodo record {ref.record_id}, v{ref.version}).")
            return
    _download_and_build(cfg, ref)


@app.command()
def status() -> None:
    """Print the local mirror's provenance (or report that none is built)."""
    cfg = _config()
    repo = MirrorRepository.open(cfg.db_path)
    if repo is None:
        typer.echo(f"No mirror at {cfg.db_path} (serving live-only).")
        return
    meta = repo.meta()
    repo.close()
    typer.echo(
        f"Mirror {cfg.db_path}\n"
        f"  as_of={meta.get('dump_as_of')} zenodo_record={meta.get('zenodo_record')} "
        f"v{meta.get('zenodo_version')}\n"
        f"  score_sets={meta.get('score_set_count')} "
        f"mapped_variants={meta.get('mapped_variant_count')} built={meta.get('build_utc')}"
    )


@app.command()
def bootstrap() -> None:
    """Ensure a mirror exists (reuse -> pull -> build); degrade to live-only on failure."""
    cfg = _config()
    repo = MirrorRepository.open(cfg.db_path)
    if repo is not None:
        repo.close()
        typer.echo(f"Mirror present at {cfg.db_path}; reusing.")
        return
    try:
        if cfg.bundle_url or cfg.bundle_path is not None:
            typer.echo("Pulling prebuilt mirror artifact ...")
            _pull_bundle(cfg)
            typer.echo(f"Installed prebuilt mirror at {cfg.db_path}.")
            return
    except MaveDBError as exc:
        typer.echo(f"Prebuilt pull failed ({exc}); falling back.")
    try:
        if cfg.build_local:
            _download_and_build(
                cfg,
                resolve_latest_dump(
                    cfg.zenodo_concept_id,
                    max_dump_bytes=cfg.max_dump_bytes,
                    max_metadata_bytes=cfg.max_metadata_bytes,
                ),
            )
            return
    except MaveDBError as exc:
        typer.echo(f"Local build failed ({exc}).")
    typer.echo("No mirror available; the server will run live-only (live API backup).")


@app.command()
def pull() -> None:
    """Download + install the latest prebuilt mirror artifact from GitHub Releases."""
    cfg = _config()
    _pull_bundle(cfg)
    typer.echo(f"Installed prebuilt mirror at {cfg.db_path}.")


@app.command()
def pack() -> None:
    """Compress the local mirror into a publishable artifact (+ sha256 sidecar)."""
    cfg = _config()
    out, sha = bundle.pack(cfg.db_path, cfg.data_dir / cfg.bundle_asset_name)
    typer.echo(f"Packed {out} (+ {sha.name}).")


def _pull_bundle(cfg: MirrorConfig) -> None:
    exact = (
        cfg.bundle_expected_sha256 is not None
        and cfg.bundle_expected_expanded_sha256 is not None
        and cfg.bundle_expected_schema_version is not None
        and cfg.bundle_release_tag is not None
    )
    if exact:
        assert cfg.bundle_expected_sha256 is not None
        target = cfg.reference_root / cfg.bundle_expected_sha256
    else:
        target = cfg.data_dir
    destination = target / cfg.db_filename if exact else cfg.db_path
    with build_lock(cfg.reference_root / ".materialize.lock"):
        if cfg.bundle_path is not None:
            if not exact:
                raise DataUnavailableError(
                    "a pre-seeded bundle requires complete immutable data identity"
                )
            assert cfg.bundle_expected_sha256 is not None
            assert cfg.bundle_expected_expanded_sha256 is not None
            assert cfg.bundle_expected_schema_version is not None
            identity = bundle.install_preseeded(
                cfg.bundle_path,
                destination,
                expected_sha256=cfg.bundle_expected_sha256,
                expected_expanded_sha256=cfg.bundle_expected_expanded_sha256,
                expected_schema_version=cfg.bundle_expected_schema_version,
                max_expanded_bytes=cfg.max_database_bytes,
            )
        else:
            identity = bundle.pull(
                cfg.github_repo,
                cfg.bundle_asset_name,
                cfg.bundle_url,
                destination,
                expected_sha256=cfg.bundle_expected_sha256,
                max_compressed_bytes=cfg.max_bundle_bytes,
                max_expanded_bytes=cfg.max_database_bytes,
                max_metadata_bytes=cfg.max_metadata_bytes,
                max_seconds=cfg.max_download_seconds,
                expected_expanded_sha256=cfg.bundle_expected_expanded_sha256,
                expected_schema_version=cfg.bundle_expected_schema_version,
            )
        if exact:
            assert cfg.bundle_expected_sha256 is not None
            assert cfg.bundle_release_tag is not None
            bundle.select_reference(
                cfg.reference_root,
                target,
                {
                    "release_tag": cfg.bundle_release_tag,
                    "compressed_sha256": cfg.bundle_expected_sha256,
                    **identity,
                },
            )


@app.command()
def publish(tag: str = typer.Argument(..., help="Release tag, e.g. data-2026-02-06.")) -> None:
    """Pack + upload the mirror to a GitHub Release (maintainer/CI; needs `gh`)."""
    cfg = _config()
    bundle.publish(cfg.db_path, cfg.github_repo, tag, cfg.bundle_asset_name)
    typer.echo(f"Published {cfg.bundle_asset_name} to {cfg.github_repo}@{tag}.")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
