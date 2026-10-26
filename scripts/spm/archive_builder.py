from __future__ import annotations

from pathlib import Path


def find_dynamic_library(archive_root: Path, derived_data_root: Path, library_basename: str) -> Path:
    archive_products_root = archive_root / "Products"
    archive_matches = sorted(archive_products_root.rglob(f"lib{library_basename}*.dylib"))
    if archive_matches:
        return archive_matches[0]

    fallback_root = derived_data_root / "ArchiveIntermediates"
    fallback_matches = sorted(fallback_root.rglob(f"lib{library_basename}*.dylib"))
    if fallback_matches:
        return fallback_matches[0]

    raise FileNotFoundError(f"failed to find lib{library_basename}*.dylib in archive products or uninstalled products")
