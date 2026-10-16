"""Resource-bounded child process. Never receives service credentials or emits raw exceptions."""

import json
import logging
import sys
from pathlib import Path

from app.processing.parsers import ParseFailure, ParseLimits, parse_document


def main() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    logging.disable(logging.CRITICAL)
    source, output, mime, configuration = sys.argv[1:]
    try:
        limits = ParseLimits.model_validate_json(configuration)
        parsed = parse_document(Path(source).read_bytes(), mime, limits)
        result: dict[str, object] = {"document": parsed.model_dump()}
    except ParseFailure as exc:
        result = {"error": exc.code}
    except Exception:  # noqa: BLE001 - untrusted parser failure; never expose contents or exception text
        result = {"error": "PARSE_FAILED"}
    Path(output).write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")


if __name__ == "__main__":
    main()
