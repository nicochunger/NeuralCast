"""CLI entrypoint for weekly schedule generation."""

from __future__ import annotations

from collections.abc import Sequence

from neuralcast.pipelines.schedule_generator import build_arg_parser, run


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv) if argv is not None else parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
