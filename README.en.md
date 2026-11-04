# Auto ETL

A locally installed ETL and schema-design tool where code does the measuring and an LLM
does the judging. It analyzes CSV and Excel files and produces PostgreSQL schema
candidates and a load plan. The whole pipeline is controlled by code so that the output
quality stays the same regardless of which model you use.

> Status: early development. Not yet published on PyPI.
> [한국어 README](README.md)

## What it will do

Given CSV/Excel input:

1. A deterministic engine computes column profiles, type inference, relationship
   analysis, quality diagnostics, and sensitivity classification.
2. An LLM harness assists only at fixed decision points; LLM output is forced through
   contract schemas.
3. The tool produces candidate DDLs (3NF, star schema, and more), scorecards, and a
   load plan.

Core principles:

| Principle | Meaning |
| --- | --- |
| Determinism first | Statistics, types, relations, candidate schemas, and DDL are computed by code. The LLM only chooses and explains at fixed points |
| Contracts first | Step inputs and outputs are pinned with Pydantic schemas, isolating the pipeline from model changes |
| Local first | Engine, local server, and SQLite metadata in one process. No Redis, Docker, or GPU required |
| Secure by default | In Secure mode, zero real cell values leave for external LLMs |

## Development

Python 3.10+ with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # install dependencies into .venv
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format --check .
uv run mypy             # type checking (strict)
```

CI runs the same checks plus a dependency-license check on Windows, macOS, and Linux ×
Python 3.10–3.13. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to contribute and
[AGENTS.md](AGENTS.md) for the rules AI coding agents follow in this repository.

## License

[Apache-2.0](LICENSE)
