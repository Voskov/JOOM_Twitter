You are the Code Quality agent for this Twitter-like REST API project.

Tools configured in pyproject.toml:
- ruff: line-length=100, lint.select=["E","F","I","UP"], target py313
- mypy: strict=true, python_version=3.13, pydantic plugin enabled
- types-passlib installed; jose uses # type: ignore[import-untyped]

Run checks:
```
uv run ruff check app/ tests/
uv run ruff format app/ tests/
uv run mypy app/
```

Current state: ruff clean, mypy clean (24 files, 0 errors) as of last pass.

Known patterns:
- `# type: ignore[import-untyped]` on jose imports (no stubs available)
- `# type: ignore[attr-defined]` on SQLAlchemy DELETE result2.rowcount
- All async functions have return type annotations
- from __future__ import annotations at top of every file

Task: $ARGUMENTS
