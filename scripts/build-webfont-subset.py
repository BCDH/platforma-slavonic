#!/usr/bin/env python3
"""Build and optionally install the Platforma Slavonic Regular webfont subset."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from fontTools import subset
from fontTools.ttLib import TTFont


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "fonts" / "ttf" / "Monomakh-Regular.ttf"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "fonts" / "webfonts" / "PlatformaSlavonic-Regular.woff2"
)
FRONTEND_FILENAME = "platformaslavonic-regular-webfont.woff2"
FRONTEND_DESTINATIONS = (
    Path("resources/assets/fonts/platforma") / FRONTEND_FILENAME,
    Path("public/fonts/platforma") / FRONTEND_FILENAME,
)

# Each range is inclusive. The final cmap is the intersection of these ranges
# and the source font's cmap, so future source additions in these blocks are
# picked up automatically without manufacturing missing characters.
CYRILLIC_RANGES = (
    (0x0400, 0x052F),  # Cyrillic and Cyrillic Supplement
    (0x1C80, 0x1C8F),  # Cyrillic Extended-C
    (0x2DE0, 0x2DFF),  # Cyrillic Extended-A combining marks
    (0xA640, 0xA69F),  # Cyrillic Extended-B
    (0x1E030, 0x1E08F),  # Cyrillic Extended-D
)
COMBINING_MARK_RANGES = (
    (0x0300, 0x036F),
    (0x1AB0, 0x1AFF),
    (0x1DC0, 0x1DFF),
    (0x20D0, 0x20FF),
    (0xFE20, 0xFE2F),
)
SUPPORT_RANGES = (
    (0x0021, 0x002F),  # ASCII punctuation
    (0x0030, 0x0039),  # ASCII digits
    (0x003A, 0x0040),  # ASCII punctuation
    (0x005B, 0x0060),  # ASCII punctuation
    (0x007B, 0x007E),  # ASCII punctuation
    (0x2000, 0x206F),  # General Punctuation
    (0x2E00, 0x2E7F),  # Supplemental Punctuation
)
SUPPORT_CODEPOINTS = {
    0x0020,  # Space
    0x00A0,  # No-break space
    0x00AB,  # Left-pointing double angle quotation mark
    0x00AD,  # Soft hyphen
    0x00BB,  # Right-pointing double angle quotation mark
    0x25CC,  # Dotted circle
}

FAMILY_NAME = "Platforma Slavonic"
STYLE_NAME = "Regular"
FULL_NAME = f"{FAMILY_NAME} {STYLE_NAME}"
POSTSCRIPT_NAME = "PlatformaSlavonic-Regular"
RENAMED_NAME_IDS = {
    1: FAMILY_NAME,
    2: STYLE_NAME,
    4: FULL_NAME,
    6: POSTSCRIPT_NAME,
    16: FAMILY_NAME,
    17: STYLE_NAME,
    18: FULL_NAME,
    21: FAMILY_NAME,
    22: STYLE_NAME,
}


def codepoints_for_ranges(ranges: tuple[tuple[int, int], ...]) -> set[int]:
    return {
        codepoint
        for first, last in ranges
        for codepoint in range(first, last + 1)
    }


REQUESTED_CODEPOINTS = (
    codepoints_for_ranges(CYRILLIC_RANGES)
    | codepoints_for_ranges(COMBINING_MARK_RANGES)
    | codepoints_for_ranges(SUPPORT_RANGES)
    | SUPPORT_CODEPOINTS
)


def rename_font(font: TTFont) -> None:
    """Rename user-facing family/style records while preserving legal metadata."""
    for record in font["name"].names:
        replacement = RENAMED_NAME_IDS.get(record.nameID)
        if replacement is not None:
            record.string = replacement.encode(record.getEncoding())


def decoded_names(font: TTFont, name_id: int) -> set[str]:
    return {
        record.toUnicode()
        for record in font["name"].names
        if record.nameID == name_id
    }


def validate_font(path: Path, expected_cmap: set[int]) -> None:
    with TTFont(path, recalcTimestamp=False) as font:
        actual_cmap = set(font.getBestCmap() or {})
        if font.flavor != "woff2":
            raise ValueError(f"Expected WOFF2 flavor, found {font.flavor!r}")
        if actual_cmap != expected_cmap:
            missing = sorted(expected_cmap - actual_cmap)
            unexpected = sorted(actual_cmap - expected_cmap)
            raise ValueError(
                "Subset cmap mismatch: "
                f"missing={[f'U+{value:04X}' for value in missing]}, "
                f"unexpected={[f'U+{value:04X}' for value in unexpected]}"
            )
        for codepoint in range(ord("A"), ord("Z") + 1):
            if codepoint in actual_cmap:
                raise ValueError("Basic Latin uppercase letters must not be retained")
        for codepoint in range(ord("a"), ord("z") + 1):
            if codepoint in actual_cmap:
                raise ValueError("Basic Latin lowercase letters must not be retained")
        expected_names = {
            1: FAMILY_NAME,
            2: STYLE_NAME,
            4: FULL_NAME,
            6: POSTSCRIPT_NAME,
        }
        for name_id, expected_name in expected_names.items():
            names = decoded_names(font, name_id)
            if not names or names != {expected_name}:
                raise ValueError(
                    f"Unexpected name ID {name_id}: {sorted(names)!r}; "
                    f"expected {expected_name!r}"
                )
        if font["OS/2"].usWeightClass != 400:
            raise ValueError("Subset is not Regular weight 400")
        required_layout_tables = {"GDEF", "GPOS", "GSUB"}
        missing_tables = required_layout_tables - set(font.keys())
        if missing_tables:
            raise ValueError(
                f"Missing required layout tables: {', '.join(sorted(missing_tables))}"
            )


def build_subset(source: Path, output: Path) -> set[int]:
    if not source.is_file():
        raise FileNotFoundError(
            f"Source font not found: {source}. Run `make build` before subsetting."
        )

    with TTFont(source, recalcTimestamp=False) as font:
        source_cmap = set(font.getBestCmap() or {})
        expected_cmap = source_cmap & REQUESTED_CODEPOINTS

        options = subset.Options()
        options.layout_features = ["*"]
        options.layout_scripts = ["*"]
        options.name_IDs = ["*"]
        options.name_languages = ["*"]
        options.name_legacy = True
        options.notdef_glyph = True
        options.recommended_glyphs = True

        subsetter = subset.Subsetter(options=options)
        subsetter.populate(unicodes=expected_cmap)
        subsetter.subset(font)
        rename_font(font)
        font.flavor = "woff2"

        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        try:
            font.save(temporary_path)
            validate_font(temporary_path, expected_cmap)
            os.replace(temporary_path, output)
        finally:
            temporary_path.unlink(missing_ok=True)

    return expected_cmap


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as font_file:
        for chunk in iter(lambda: font_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_in_frontend(output: Path, frontend_dir: Path) -> list[Path]:
    frontend_dir = frontend_dir.expanduser().resolve()
    if not (frontend_dir / "AGENTS.md").is_file():
        raise ValueError(
            f"Frontend path does not look like raskovnik-frontend: {frontend_dir}"
        )

    destinations = [frontend_dir / relative for relative in FRONTEND_DESTINATIONS]
    missing_directories = [
        destination.parent
        for destination in destinations
        if not destination.parent.is_dir()
    ]
    if missing_directories:
        raise FileNotFoundError(
            "Expected frontend font directories do not exist: "
            + ", ".join(str(path) for path in missing_directories)
        )

    staged_files: list[tuple[Path, Path]] = []
    try:
        for destination in destinations:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            shutil.copyfile(output, temporary_path)
            staged_files.append((temporary_path, destination))

        for temporary_path, destination in staged_files:
            os.replace(temporary_path, destination)
    finally:
        for temporary_path, _ in staged_files:
            temporary_path.unlink(missing_ok=True)

    output_digest = sha256(output)
    for destination in destinations:
        if sha256(destination) != output_digest:
            raise ValueError(f"Installed copy differs from generated font: {destination}")
    return destinations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        help="raskovnik-frontend checkout in which to install both tracked copies",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    expected_cmap = build_subset(DEFAULT_SOURCE, DEFAULT_OUTPUT)
    digest = sha256(DEFAULT_OUTPUT)
    print(
        f"Built {DEFAULT_OUTPUT} ({DEFAULT_OUTPUT.stat().st_size} bytes, "
        f"{len(expected_cmap)} encoded characters, sha256 {digest})"
    )

    if args.frontend_dir:
        for destination in install_in_frontend(DEFAULT_OUTPUT, args.frontend_dir):
            print(f"Installed {destination}")


if __name__ == "__main__":
    main()
