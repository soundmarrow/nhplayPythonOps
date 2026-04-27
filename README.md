# NHPlayPythonOps

Operational Python scripts for NHPlay work that should run outside the ColdFusion web application.

This repository is intended for manual or scheduled utility work: read-only exports, verification jobs, one-off reporting helpers, and similar tasks. Keep web application code in `nhplay`; keep operational scripts here.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Real env files are intentionally ignored by git. This repo follows the same env style as `gwsBackfillAndGc`:

```bash
cp .env.example .env.dev-read
cp .env.example .env.prod-read
```

The initial local setup also copied `.env.dev-write` / `.env.prod-write` from `gwsBackfillAndGc`. Treat them as local-only secrets. Prefer read-only SQL users for export scripts when those logins exist.

## GWS Raw File Export

Export one zip per track, year, and file type:

```bash
python scripts/gws_export_files.py --env-file .env.dev-write --track-code SAR --year 2025 --file-type Cycle
```

For a custom date range:

```bash
python scripts/gws_export_files.py --env-file .env.dev-write --track-code SAR --start-date 2025-07-01 --end-date 2025-07-07 --file-type FinalCycle
```

Useful options:

```bash
--file-type Cycle|FinalCycle|TriProbs|Price|Meeting|RaceCard|Change
--source all|main|quarterly
--count-only
--background
--output-dir exports
```

The exporter defaults to `READ UNCOMMITTED` and streams database rows directly into zip archives. It does not leave a directory of raw JSON files behind.
