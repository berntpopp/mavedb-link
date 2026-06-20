"""``mavedb-link-cache`` CLI: inspect and clear the mapped-variant cache."""

from __future__ import annotations

import typer

from mavedb_link.data.mapped_cache import MappedVariantCache, mapped_cache_data_version
from mavedb_link.data.repository import MirrorRepository

app = typer.Typer(add_completion=False, help="Inspect and clear the mapped-variant cache.")


def _cache_data_version() -> str:
    """Current cache data version, tied to the mirror snapshot when present."""
    from mavedb_link.config import settings

    meta = None
    if settings.mirror.enabled:
        repo = MirrorRepository.open(settings.mirror.db_path)
        if repo is not None:
            try:
                meta = repo.meta()
            finally:
                repo.close()
    return mapped_cache_data_version(meta)


def _open_cache() -> MappedVariantCache:
    """Open the configured mapped-variant cache."""
    from mavedb_link.config import settings

    return MappedVariantCache(
        settings.cache.db_path,
        data_version=_cache_data_version(),
        lru_sets=settings.cache.lru_sets,
    )


@app.command()
def status() -> None:
    """Print mapped-variant cache status for the current data version."""
    from mavedb_link.config import settings

    if not settings.cache.enabled:
        typer.echo("Cache enabled=False")
        return
    cache = _open_cache()
    try:
        stats = cache.stats()
    finally:
        cache.close()
    typer.echo(
        f"Cache {settings.cache.db_path}\n"
        f"  enabled=True data_version={stats['data_version']}\n"
        f"  on_disk={stats['on_disk']} lru_size={stats['lru_size']}"
    )


@app.command()
def clear(yes: bool = typer.Option(False, "--yes", help="Confirm deletion.")) -> None:
    """Clear mapped-variant cache rows for the current data version."""
    from mavedb_link.config import settings

    if not yes:
        typer.echo("Refusing to clear without --yes.")
        raise typer.Exit(1)
    if not settings.cache.enabled:
        typer.echo("Cache enabled=False cleared=0")
        return
    cache = _open_cache()
    try:
        count = cache.clear()
    finally:
        cache.close()
    typer.echo(f"Cache {settings.cache.db_path} cleared={count}")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
