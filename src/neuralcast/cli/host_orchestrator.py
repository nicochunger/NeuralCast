"""CLI entrypoint for generating and injecting AI host segments."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator.main import build_arg_parser, run


def main() -> None:
    args = build_arg_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
