# CI

## Why the workflow is not in this directory

GitHub Actions only reads workflows from `.github/workflows/`. That path is
not configurable, so the pipeline itself lives at
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). What lives
here is the part that is genuinely reusable — scripts CI calls, which you
can also run locally.

## What runs on every push

| Job | Gates the build | What it checks |
|---|---|---|
| **No secrets committed** | yes | `.env` is untracked; no key-shaped strings in tracked files |
| **Backend tests** | yes | migrations up, down and up again; no schema drift; the full pytest suite |
| **Frontend build** | yes | `tsc --noEmit`, lint, production `next build` |
| **Dependency audit** | **no** | `npm audit --audit-level=high`, advisory only |

The audit job is deliberately `continue-on-error`. A new critical advisory
in a transitive dependency should not block an unrelated hotfix — that is
how teams learn to ignore a red build. Read it and decide.

## Why the tests need real infrastructure

The backend job starts a real `pgvector/pgvector:pg16` and downloads the
real embedding model. Neither is mocked, for the reason given in
`backend/tests/conftest.py`: a mocked embedding proves only that the SQL
compiles. The tests exist to check that a real vector query finds the right
passage, and that cannot be faked.

Plain `postgres:16` will not work — every retrieval test fails at
`CREATE EXTENSION vector`.

## Why CI has no API keys

`conftest.py` pins `CHAT_PROVIDER=extractive` and `FAST_PROVIDER=none`
unless `RUN_HOSTED_CHAT_EVAL=1`. Adding a `GROQ_API_KEY` secret would make
every push a billed API call whose pass/fail depends on a model's output
that day. The hosted-provider evals are run deliberately, by a person, with
`make eval`.

If you do add a key later, add it as a repository *secret* and gate it on
`github.event_name != 'pull_request'` — a workflow that exposes secrets to
PRs from forks hands them to anyone who opens one.

## Scripts

### `check_schema_drift.py`

```bash
cd backend && python ../infra/ci/check_schema_drift.py
```

Fails if the SQLAlchemy models and the migrated schema disagree. Catches the
quiet failure where a model gains a column, works locally because that
developer's database was built from metadata, and then breaks on the first
real `alembic upgrade head`.

Uses alembic's `compare_metadata` rather than generating a throwaway
revision, so a failed run leaves no stray migration file behind.

## Running the same checks locally

```bash
make test
```

```bash
cd frontend && npx tsc --noEmit && npm run build
```
