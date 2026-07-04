#!/usr/bin/env python3
"""
CIS Watchlist master repair script (V3.12.4-compatible)

Purpose:
- Repair only the existing data/watchlist_master.csv.
- Do not recreate the watchlist from a hard-coded old list.
- Preserve row order as much as possible.
- Remove blank rows and exact duplicate rows.
- Normalize headers/cells enough to keep CSV readable by downstream jobs.
- Write output/watchlist_repair_status.json/md for GitHub Actions and CIS dashboard.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT_VERSION = "V3.12.4-compatible"
WATCHLIST_PATH = Path("data/watchlist_master.csv")
OUTPUT_DIR = Path("output")
STATUS_JSON = OUTPUT_DIR / "watchlist_repair_status.json"
STATUS_MD = OUTPUT_DIR / "watchlist_repair_status.md"
JST = dt.timezone(dt.timedelta(hours=9))


def now_jst() -> str:
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_status(payload: Dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload.setdefault("schema_version", "V3.12.4")
    payload.setdefault("script_version", SCRIPT_VERSION)
    payload.setdefault("module", "watchlist_repair")
    payload.setdefault("checked_at_jst", now_jst())
    payload.setdefault("run_url", os.environ.get("RUN_URL", ""))
    payload.setdefault("actions_workflow_url", os.environ.get("ACTIONS_WORKFLOW_URL", ""))
    payload.setdefault("reason", os.environ.get("REPAIR_REASON", ""))

    STATUS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATUS_MD.write_text(
        "# Watchlist修復 status\n\n"
        f"- status: {payload.get('status', 'unknown')}\n"
        f"- checked_at_jst: {payload.get('checked_at_jst', '')}\n"
        f"- script_version: {payload.get('script_version', SCRIPT_VERSION)}\n"
        f"- watchlist_changed: {payload.get('watchlist_changed', '')}\n"
        f"- rows_before: {payload.get('rows_before', '')}\n"
        f"- rows_after: {payload.get('rows_after', '')}\n"
        f"- blank_rows_removed: {payload.get('blank_rows_removed', '')}\n"
        f"- duplicate_rows_removed: {payload.get('duplicate_rows_removed', '')}\n"
        f"- message: {payload.get('message', '')}\n"
        f"- next_action: {payload.get('user_next_action', '')}\n"
        f"- run_url: {payload.get('run_url', '')}\n"
        f"- actions_workflow_url: {payload.get('actions_workflow_url', '')}\n",
        encoding="utf-8",
    )


def fail(message: str, exit_code: int = 2, **extra: Any) -> None:
    payload: Dict[str, Any] = {
        "status": "failed",
        "repair_exit_code": exit_code,
        "watchlist_changed": False,
        "message": message,
        "user_next_action": "GitHub Actionsの失敗ログを確認してください。watchlist_master.csv が存在するか、CSVの1行目にヘッダーがあるかを確認し、修正後に再度Run workflowを押してください。",
    }
    payload.update(extra)
    write_status(payload)
    print(f"[CIS] ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def decode_bytes(raw: bytes) -> Tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # 最後の保険。壊れた文字は置換して、CSV構造だけでも救う。
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def make_unique_headers(headers: List[str]) -> Tuple[List[str], int, int]:
    """Strip headers, fill blanks, and make duplicates unique."""
    cleaned: List[str] = []
    blank_count = 0
    duplicate_count = 0
    seen: Dict[str, int] = {}

    for idx, h in enumerate(headers, start=1):
        name = (h or "").replace("\ufeff", "").strip()
        if not name:
            blank_count += 1
            name = f"extra_col_{idx}"
        base = name
        if base in seen:
            duplicate_count += 1
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
        cleaned.append(name)

    return cleaned, blank_count, duplicate_count


def parse_and_repair(text: str) -> Tuple[List[str], List[List[str]], Dict[str, Any]]:
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        fail("data/watchlist_master.csv が空です。", rows_before=0, rows_after=0)

    header_raw = rows[0]
    data_raw = rows[1:]
    if not any((cell or "").strip() for cell in header_raw):
        fail("data/watchlist_master.csv の1行目ヘッダーが空です。", rows_before=len(data_raw), rows_after=0)

    max_len = max([len(header_raw)] + [len(r) for r in data_raw] + [1])
    if len(header_raw) < max_len:
        header_raw = header_raw + [f"extra_col_{i}" for i in range(len(header_raw) + 1, max_len + 1)]

    header, blank_headers, duplicate_headers = make_unique_headers(header_raw)

    cleaned_rows: List[List[str]] = []
    blank_rows_removed = 0
    duplicate_rows_removed = 0
    seen_rows = set()

    for raw_row in data_raw:
        row = [(cell or "").strip() for cell in raw_row]
        if len(row) < len(header):
            row += [""] * (len(header) - len(row))
        elif len(row) > len(header):
            # 通常はmax_lenでheader拡張済みだが、念のため。
            extra_needed = len(row) - len(header)
            start = len(header) + 1
            header.extend([f"extra_col_{i}" for i in range(start, start + extra_needed)])

        if not any(row):
            blank_rows_removed += 1
            continue

        key = tuple(row)
        if key in seen_rows:
            duplicate_rows_removed += 1
            continue
        seen_rows.add(key)
        cleaned_rows.append(row)

    metrics: Dict[str, Any] = {
        "columns": header,
        "column_count": len(header),
        "rows_before": len(data_raw),
        "rows_after": len(cleaned_rows),
        "blank_rows_removed": blank_rows_removed,
        "duplicate_rows_removed": duplicate_rows_removed,
        "blank_headers_filled": blank_headers,
        "duplicate_headers_renamed": duplicate_headers,
    }
    return header, cleaned_rows, metrics


def render_csv(header: List[str], rows: List[List[str]]) -> bytes:
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def main() -> int:
    if not WATCHLIST_PATH.exists():
        fail("data/watchlist_master.csv が見つかりません。", rows_before=0, rows_after=0)

    raw = WATCHLIST_PATH.read_bytes()
    before_sha = sha256_bytes(raw)
    text, encoding = decode_bytes(raw)
    header, rows, metrics = parse_and_repair(text)
    repaired = render_csv(header, rows)
    after_sha = sha256_bytes(repaired)
    changed = before_sha != after_sha

    if changed:
        tmp = WATCHLIST_PATH.with_suffix(WATCHLIST_PATH.suffix + ".tmp")
        tmp.write_bytes(repaired)
        tmp.replace(WATCHLIST_PATH)

    message = "watchlist_master.csv を確認しました。"
    if changed:
        message = "watchlist_master.csv を修復・正規化しました。"
    elif metrics.get("blank_rows_removed") or metrics.get("duplicate_rows_removed"):
        message = "watchlist_master.csv を確認しました。修復対象は検出されましたが、ファイル内容の差分はありませんでした。"

    payload: Dict[str, Any] = {
        "status": "success",
        "repair_exit_code": 0,
        "watchlist_changed": changed,
        "message": message,
        "user_next_action": "通常対応は不要です。CIS Pages側にWatchlist修復系の異常カードが出ていないことを確認してください。",
        "input_encoding": encoding,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
    }
    payload.update(metrics)
    write_status(payload)

    print(f"[CIS] Watchlist repair success. changed={changed} rows_before={metrics['rows_before']} rows_after={metrics['rows_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
