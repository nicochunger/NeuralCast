"""CLI entrypoint for weekly schedule generation."""

from __future__ import annotations

from neuralcast.pipelines.schedule_generator import build_arg_parser, run


def main() -> None:
    args = build_arg_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
