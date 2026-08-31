import json
import logging
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path

from confluence_markdown_exporter.utils.app_data_store import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
export_options = settings.export


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
        binary = isinstance(content, bytes)
        with tempfile.NamedTemporaryFile(
            mode="wb" if binary else "w",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
            encoding=None if binary else "utf-8",
        ) as file:
            tmp_path = Path(file.name)
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
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            tmp_path = Path(file.name)
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
