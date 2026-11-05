---
name: release-checkpoint
description: Use when cutting a release of disputes-service (version bump, CHANGELOG entry) to export real checkpoints from that release and prove they resume, so threads paused in production survive the next upgrade.
---

# Release checkpoint

Threads paused at approval can wait 72 hours; a deploy in between must resume them. Every release commits one
paused resolution and one assist checkpoint, and CI replays each one forever after.

1. Bump `app/__init__.py` and `pyproject.toml` to the release version; `uv lock`.
2. Against a scratch database at head (`alembic upgrade head`, `scripts/checkpoint_setup.py`, `python -m seeds.load`),
   start the app and create, through the API:
   - a VISA 10.4 dispute on `NW-1001` → start a resolution → it pauses at approval (CONTEST);
   - one assist question on the same dispute.
3. Export both:
   ```bash
   uv run python scripts/export_checkpoint.py resolution:<dispute>:1 \
       tests/fixtures/checkpoints/release_<v>_resolution_awaiting.json resolution <resolution schema>
   uv run python scripts/export_checkpoint.py assist:<dispute> \
       tests/fixtures/checkpoints/release_<v>_assist.json assist <assist schema>
   ```
   Schemas are `SCHEMA_VERSION` in `app/orchestration/state.py`.
4. `uv run pytest tests/graph/test_checkpoint_compat.py`: every release fixture, old and new, must pass. If an
   older one fails, the change broke paused threads: add an upgrade step (see `upgrade_state` in `assist.py`)
   instead of editing the fixture.
5. Add the CHANGELOG entry (migrations in order, flags, rollback note) and commit fixtures with the bump.

Never edit or delete a committed release fixture; supported versions are listed in `docs/maintenance.md`.
