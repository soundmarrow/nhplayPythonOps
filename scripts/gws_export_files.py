#!/usr/bin/env python3
"""Export raw GWS JSON files as track/year/file-type zip archives."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nhplay_ops.config import SqlConfig, load_env_file


FILE_TYPE_ALIASES = {
    "meeting": "Meeting",
    "racecard": "RaceCard",
    "race-card": "RaceCard",
    "cycle": "Cycle",
    "finalcycle": "FinalCycle",
    "final-cycle": "FinalCycle",
    "price": "Price",
    "change": "Change",
    "triprobs": "TriProbs",
    "tri-probs": "TriProbs",
    "trialprobs": "TriProbs",
    "trial-probs": "TriProbs",
}

FILE_TYPE_IDS = {
    "Meeting": 1,
    "RaceCard": 2,
    "Cycle": 3,
    "FinalCycle": 4,
    "Price": 5,
    "Change": 6,
    "TriProbs": 7,
}

SOURCE_TABLES = {
    "all": "dbo.v_gws_files_all",
    "main": "dbo.gws_files",
    "quarterly": "dbo.gws_files_q",
}


def normalize_file_type(value: str) -> str:
    text = value.strip()
    key = re.sub(r"[\s_]+", "-", text.lower())
    return FILE_TYPE_ALIASES.get(key, text)


def selected_file_type(args: argparse.Namespace) -> Tuple[int, str]:
    if args.file_type_id is not None:
        return args.file_type_id, f"type-{args.file_type_id}"
    file_type = normalize_file_type(args.file_type)
    if file_type not in FILE_TYPE_IDS:
        allowed = ", ".join(FILE_TYPE_IDS)
        raise SystemExit(f"Unknown --file-type {args.file_type!r}. Use one of: {allowed}, or pass --file-type-id.")
    return FILE_TYPE_IDS[file_type], file_type


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_track_codes(value: str) -> List[str]:
    codes = [code.strip().upper() for code in value.split(",") if code.strip()]
    if not codes:
        raise argparse.ArgumentTypeError("Provide at least one track code")
    return codes


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD") from exc


def safe_file_name(value: str) -> str:
    name = os.path.basename(value.strip())
    name = re.sub(r"(?i)\.xml$", ".json", name)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" .")
    return name or "gws-file.json"


def json_file_name(row: Any) -> str:
    try:
        parsed = json.loads(row.data_json)
        if isinstance(parsed, dict) and parsed.get("FileName"):
            return safe_file_name(str(parsed["FileName"]))
    except (TypeError, json.JSONDecodeError):
        pass
    race_date = str(row.race_date)[:10]
    return safe_file_name(
        f"{race_date}-{row.track_code}-R{row.race}-{row.file_type}-"
        f"S{row.sequence_num}-C{row.cycle_id}.json"
    )


def zip_member_name(row: Any, seen: set[str]) -> str:
    race_date = str(row.race_date)[:10]
    base = f"{race_date}/{json_file_name(row)}"
    if base not in seen:
        seen.add(base)
        return base
    path = Path(base)
    counter = 2
    while True:
        candidate = f"{path.parent}/{path.stem}-{counter}{path.suffix}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        counter += 1


def year_windows(start_date: date, end_date: date) -> List[Tuple[int, date, date]]:
    windows: List[Tuple[int, date, date]] = []
    for year in range(start_date.year, end_date.year + 1):
        window_start = max(start_date, date(year, 1, 1))
        window_end = min(end_date, date(year, 12, 31))
        windows.append((year, window_start, window_end))
    return windows


def connect():
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pyodbc is required. Install with: pip install -r requirements.txt") from exc

    return pyodbc.connect(SqlConfig.from_env().connection_string())


def build_query(args: argparse.Namespace) -> Tuple[str, List[Any]]:
    source = SOURCE_TABLES[args.source]
    where = [
        "f.race_date >= ?",
        "f.race_date <= ?",
        "t.track_code = ?",
    ]
    params: List[Any] = []

    where.append("f.file_type_id = ?")
    if args.track_breed:
        where.append("t.track_breed = ?")
    if args.race is not None:
        where.append("f.race = ?")

    sql = f"""
SELECT
    f.cycle_id,
    f.file_type_id,
    ? AS file_type,
    f.race_date,
    f.track_id,
    t.track_code,
    t.track_breed,
    f.race,
    f.sequence_num,
    f.serial_num,
    f.timestamp_added,
    f.data_json
FROM {source} f
JOIN dbo.gws_tracks t
    ON t.track_id = f.track_id
WHERE {" AND ".join(where)}
ORDER BY f.race_date, f.track_id, f.race, f.sequence_num, f.cycle_id
"""
    return sql, params


def query_params(args: argparse.Namespace, track_code: str, start_date: date, end_date: date) -> List[Any]:
    file_type_id, file_type_label = selected_file_type(args)
    params: List[Any] = [file_type_label, start_date.isoformat(), end_date.isoformat(), track_code, file_type_id]
    if args.track_breed:
        params.append(args.track_breed)
    if args.race is not None:
        params.append(args.race)
    return params


def export_window(conn: Any, args: argparse.Namespace, track_code: str, year: int, start_date: date, end_date: date) -> Dict[str, Any]:
    _, file_type_label = selected_file_type(args)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{track_code}-{year}-{re.sub(r'[^A-Za-z0-9]+', '', file_type_label)}.zip"
    archive_path = output_dir / archive_name
    if archive_path.exists() and not args.overwrite:
        raise SystemExit(f"Archive already exists: {archive_path}. Use --overwrite to replace it.")

    cur = conn.cursor()
    if args.read_uncommitted:
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")

    sql, _ = build_query(args)
    cur.execute(sql, query_params(args, track_code, start_date, end_date))

    manifest: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    file_count = 0
    started_at = utc_timestamp()

    if args.count_only:
        while True:
            rows = cur.fetchmany(args.batch_size)
            if not rows:
                break
            file_count += len(rows)
        return {"track_code": track_code, "year": year, "archive_path": "", "file_count": file_count}

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=args.compress_level) as zf:
        while True:
            rows = cur.fetchmany(args.batch_size)
            if not rows:
                break
            for row in rows:
                member_name = zip_member_name(row, seen_names)
                zf.writestr(member_name, row.data_json)
                file_count += 1
                manifest.append(
                    {
                        "cycle_id": row.cycle_id,
                        "file_type_id": row.file_type_id,
                        "file_type": row.file_type,
                        "race_date": str(row.race_date)[:10],
                        "track_id": row.track_id,
                        "track_code": row.track_code,
                        "track_breed": row.track_breed,
                        "race": row.race,
                        "sequence_num": row.sequence_num,
                        "serial_num": row.serial_num,
                        "timestamp_added": row.timestamp_added,
                        "path": member_name,
                    }
                )
            print(f"{archive_name}: exported={file_count}", flush=True)

        metadata = {
            "created_at": utc_timestamp(),
            "started_at": started_at,
            "track_code": track_code,
            "track_breed": args.track_breed,
            "file_type": file_type_label,
            "file_type_id": args.file_type_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "source": args.source,
            "read_uncommitted": args.read_uncommitted,
            "file_count": file_count,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str) + "\n")
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, default=str) + "\n")

    return {"track_code": track_code, "year": year, "archive_path": str(archive_path), "file_count": file_count}


def run_export(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    conn = connect()
    conn.autocommit = True

    results = []
    for track_code in args.track_codes:
        for year, window_start, window_end in year_windows(args.start_date, args.end_date):
            results.append(export_window(conn, args, track_code, year, window_start, window_end))

    for result in results:
        if result["archive_path"]:
            print(f"archive_path={result['archive_path']}")
        print(f"track_code={result['track_code']} year={result['year']} file_count={result['file_count']}")
    return 0


def start_background(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    log_path = log_dir / f"gws_export_{run_id}.log"

    child_args = [
        sys.executable,
        str(Path(__file__).resolve()),
        *[arg for arg in sys.argv[1:] if arg != "--background"],
    ]
    with log_path.open("ab") as log_fh:
        subprocess.Popen(
            child_args,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    print(f"started run_id={run_id}")
    print(f"output_dir={output_dir}")
    print(f"log_path={log_path}")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export raw GWS JSON files as track/year/file-type zip archives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/gws_export_files.py --env-file .env.dev-write --track-code SAR,BEL,AQU --year 2025 --file-type Cycle\n"
            "  python scripts/gws_export_files.py --env-file .env.prod-write --track-code SAR --start-date 2025-07-01 --end-date 2025-07-07 --file-type FinalCycle --background\n"
            "  python scripts/gws_export_files.py --env-file .env.dev-write --track-code SAR --year 2025 --file-type TriProbs --count-only"
        ),
    )
    parser.add_argument("--env-file", required=True, help="Env file with GWS_SQL_* settings")
    parser.add_argument("--track-code", required=True, help="GWS track code or comma-separated list, e.g. SAR or SAR,BEL,AQU")
    parser.add_argument("--track-breed", default="", help="Optional breed filter, e.g. TB, H, DG")
    parser.add_argument("--year", type=int, help="Export a full calendar year")
    parser.add_argument("--start-date", type=parse_date, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", type=parse_date, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--file-type", default="Cycle", help="Cycle, FinalCycle, TriProbs, Price, Meeting, RaceCard, Change")
    parser.add_argument("--file-type-id", type=int, help="File type id; overrides --file-type")
    parser.add_argument("--race", type=int, help="Optional race number filter")
    parser.add_argument("--source", choices=sorted(SOURCE_TABLES), default="all")
    parser.add_argument("--output-dir", default=str(ROOT / "exports"))
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--compress-level", type=int, default=6)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing archive")
    parser.add_argument("--count-only", action="store_true", help="Count matching rows without creating archives")
    parser.add_argument("--background", action="store_true", help="Start export in the background and return")
    parser.add_argument(
        "--read-uncommitted",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use READ UNCOMMITTED to avoid blocking ingest/reporting reads",
    )
    args = parser.parse_args(argv)

    if args.year is not None:
        if args.start_date or args.end_date:
            raise SystemExit("Use either --year or --start-date/--end-date, not both")
        args.start_date = date(args.year, 1, 1)
        args.end_date = date(args.year, 12, 31)
    if not args.start_date or not args.end_date:
        raise SystemExit("Provide --year or both --start-date and --end-date")
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    if args.compress_level < 0 or args.compress_level > 9:
        raise SystemExit("--compress-level must be between 0 and 9")
    args.track_codes = parse_track_codes(args.track_code)
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.background:
        return start_background(args)
    return run_export(args)


if __name__ == "__main__":
    raise SystemExit(main())
