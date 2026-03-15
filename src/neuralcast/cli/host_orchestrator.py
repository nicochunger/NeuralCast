"""CLI entrypoint for generating and injecting AI host segments."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator.main import (
    ArgumentValidationError,
    build_arg_parser,
    run,
)


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        run(args)
    except ArgumentValidationError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
