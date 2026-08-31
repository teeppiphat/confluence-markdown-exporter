import json
import logging
import os
import re
import secrets
import stat
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO
from typing import TextIO

from confluence_markdown_exporter.utils.app_data_store import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
export_options = settings.export


def _open_atomic_temp(file_path: Path, *, binary: bool) -> tuple[BinaryIO | TextIO, Path]:
    """Create a sibling temp file with normal output-file permissions.

    ``NamedTemporaryFile`` intentionally creates files as ``0600``. Replacing the
    destination with that file changes exported Markdown and attachments from the
    normal ``0666 & umask`` mode to owner-only, which prevents preview processes
    running under another account from reading images. ``os.open`` applies the
    process umask just like ``Path.open``. When replacing an existing file, retain
    its current permission bits, matching a direct write to that file.
    """
    previous_mode = None
    with suppress(FileNotFoundError):
        previous_mode = stat.S_IMODE(file_path.stat().st_mode)

    for _attempt in range(10):
        tmp_path = file_path.with_name(f".{file_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue

        try:
            if previous_mode is not None:
                tmp_path.chmod(previous_mode)
            if binary:
                return os.fdopen(fd, "wb"), tmp_path
            return os.fdopen(fd, "w", encoding="utf-8"), tmp_path
        except BaseException:
            os.close(fd)
            tmp_path.unlink(missing_ok=True)
            raise

    msg = f"Could not allocate a temporary file next to {file_path}"
    raise FileExistsError(msg)


def parse_encode_setting(encode_setting: str) -> dict[str, str]:
    """Parse encoding setting containing character mapping.

    Args:
        encode_setting: JSON object content without braces
            '"char1":"replacement1","char2":"replacement2"'

    Returns:
        Dictionary mapping characters to their replacements

    Examples:
        "" -> {}
        '" ":"%2D","-":"%2D"' -> {" ": "%2D", "-": "%2D"}
        '" ":"dash","-":"%2D"' -> {" ": "dash", "-": "%2D"}
        '"=":" equals "' -> {"=": " equals "}

    Note:
        Uses JSON format for mapping to handle all characters unambiguously.
        Curly braces are added automatically before parsing.
    """
    if not encode_setting:
        return {}

    # Add curly braces to make it valid JSON
    json_str = f"{{{encode_setting}}}"

    # Use JSON parsing for robust and unambiguous parsing
    try:
        mapping = json.loads(json_str)
        if isinstance(mapping, dict):
            return mapping
    except (json.JSONDecodeError, TypeError):
        # Fallback: if parsing fails, return empty mapping
        pass

    return {}


def save_file(file_path: Path, content: str | bytes) -> None:
    """Atomically save content to a file, creating parent directories as needed.

    Content is written to a temporary file in the destination directory and
    moved into place only after the complete payload has been flushed.  A
    failed or interrupted write therefore cannot leave a partially-written
    destination file behind.
    """
    if not isinstance(content, str | bytes):
        msg = "Content must be either a string or bytes."
        raise TypeError(msg)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        file, tmp_path = _open_atomic_temp(file_path, binary=isinstance(content, bytes))
        with file:
            file.write(content)
            file.flush()
        tmp_path.replace(file_path)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise

    logger.debug("Saved file %s (%d bytes)", file_path, len(content))


class FileSizeMismatchError(OSError):
    """Raised when a streamed file does not match its advertised byte size."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Expected {expected} bytes, received {actual} bytes")


def _validate_stream_size(expected: int, actual: int) -> None:
    if expected > 0 and actual != expected:
        raise FileSizeMismatchError(expected, actual)


def save_stream(file_path: Path, chunks: Iterable[bytes], *, expected_size: int = 0) -> int:
    """Atomically stream byte chunks to disk without buffering the whole file in RAM."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    written = 0
    try:
        file, tmp_path = _open_atomic_temp(file_path, binary=True)
        with file:
            for chunk in chunks:
                if not chunk:
                    continue
                file.write(chunk)
                written += len(chunk)
            file.flush()
        _validate_stream_size(expected_size, written)
        tmp_path.replace(file_path)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    logger.debug("Saved streamed file %s (%d bytes)", file_path, written)
    return written


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename for cross-platform compatibility.

    Replaces characters based on encoding mapping,
    trims trailing spaces and dots, and prevents reserved names.

    Args:
        filename: The original filename.

    Returns:
        A sanitized filename string.
    """
    sanitized = filename

    # Strip control characters (ASCII 0x00-0x1F, 0x7F) invalid on Windows/Linux
    sanitized = re.sub(r"[\x00-\x1f\x7f]", "", sanitized)

    if export_options.filename_encoding:
        encode_map = parse_encode_setting(export_options.filename_encoding)

        # Create pattern from all characters that have mappings
        if encode_map:
            chars_to_encode = "".join(encode_map.keys())
            encode_re = escape_character_class(chars_to_encode)
            encode_pattern = re.compile(f"[{encode_re}]")

            def map_char(m: re.Match[str]) -> str:
                char = m.group(0)
                return encode_map[char]

            sanitized = re.sub(encode_pattern, map_char, sanitized)

    # Trim spaces and dots from the end
    sanitized = sanitized.rstrip(" .")

    # Reserved Windows names (case-insensitive)
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    name = Path(sanitized).stem.upper()
    if name in reserved:
        sanitized = f"{sanitized}_"

    if export_options.filename_lowercase:
        sanitized = sanitized.lower()

    # Limit length to specificed number of characters
    return sanitized[: export_options.filename_length]


def sanitize_key(s: str, connector: str = "_") -> str:
    """Convert an input string to a valid Python/YAML-compatible key.

    - Lowercase the string.
    - Replace non-alphanumeric characters with underscores.
    - Collapse multiple underscores into one.
    - Trim leading/trailing underscores.
    - Prefix with 'key_' if the first character is not a letter or underscore.
    """
    s = s.lower()
    s = re.sub(f"[^a-z0-9{connector}]", connector, s)
    s = re.sub(f"{connector}+", connector, s)
    s = s.strip(connector)
    if not re.match(r"^[a-z]", s):
        s = f"key{connector}{s}"
    return s


def github_heading_slug(text: str) -> str:
    """Generate a GitHub-compatible heading anchor slug.

    Matches the github-slugger algorithm used by GitHub to render heading anchors,
    so that generated TOC links resolve correctly in GitHub-rendered Markdown.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)  # drop punctuation; keep letters, digits, spaces, hyphens
    text = re.sub(r"[\s_]+", "-", text)  # whitespace/underscores → hyphens
    return re.sub(r"-{2,}", "-", text)  # collapse runs of hyphens (e.g. "- word" → "-word")


def escape_character_class(s: str) -> str:
    """Escape characters for use in a regex character class.

    Args:
        s: The string containing characters to escape.

    Returns:
        The input string with special regex character class characters escaped.
    """
    # Escape backslash first, then other special characters for character classes
    return s.replace("\\", r"\\").replace("-", r"\-").replace("]", r"\]").replace("^", r"\^")
