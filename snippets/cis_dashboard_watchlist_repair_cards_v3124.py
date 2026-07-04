"""
V3.12.4 dashboard補強スニペット

目的：
- docs/latest/watchlist_repair_status_latest.json
- docs/latest/watchlist_repair_persist_status_latest.json
を読み、失敗時だけiPhone上部に出すカードを作る。

既存の cis_dashboard.py の workflow_status_cards 相当の生成箇所に統合してください。
既存カードの形式が違う場合は、返り値のdictキーだけ合わせてください。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


LATEST_DIR = Path("docs/latest")


def _load_json_safely(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"statusファイルを読み取れませんでした: {path.name}: {exc}",
            "user_next_action": "GitHub Actionsの「CIS Watchlist修復｜手動実行」を開き、Run workflowを押してください。通常のCIS日次更新は自動のままで、これは異常時だけ使う非常用ボタンです。",
        }


def _is_bad_status(payload: Optional[Dict[str, Any]]) -> bool:
    if not payload:
        return False
    status = str(payload.get("status", "")).lower()
    persisted = payload.get("persisted", True)
    return status in {"failed", "error", "warning"} or persisted is False


def _action_url(payload: Dict[str, Any]) -> str:
    """GitHub Pages上で相対URLが壊れないよう、status内の絶対URLを優先する。"""
    return str(payload.get("run_url") or payload.get("actions_workflow_url") or "")


def build_watchlist_repair_status_cards(latest_dir: Path = LATEST_DIR) -> List[Dict[str, str]]:
    """
    失敗時だけ表示するカードを返す。

    既存dashboardのカード形式に合わせて、必要ならキー名を変えてください。
    想定キー：level/title/body/action_label/action_url
    """
    cards: List[Dict[str, str]] = []

    repair = _load_json_safely(latest_dir / "watchlist_repair_status_latest.json")
    persist = _load_json_safely(latest_dir / "watchlist_repair_persist_status_latest.json")

    if _is_bad_status(repair):
        cards.append(
            {
                "level": "error",
                "title": "Watchlist修復に失敗しています",
                "body": (
                    repair.get("message")
                    or "watchlist_master.csv の修復処理でエラーが出ています。"
                )
                + "\n\n次にやること："
                + (
                    repair.get("user_next_action")
                    or "GitHub Actions の「CIS Watchlist修復｜手動実行」を開き、Run workflowを押してください。"
                ),
                "action_label": "Watchlist修復を開く",
                "action_url": _action_url(repair),
            }
        )

    if _is_bad_status(persist):
        cards.append(
            {
                "level": "error",
                "title": "Watchlist修復結果を保存できていません",
                "body": (
                    persist.get("message")
                    or "修復処理は動いた可能性がありますが、GitHubへの保存に失敗しています。"
                )
                + "\n\n次にやること："
                + (
                    persist.get("user_next_action")
                    or "通常更新workflowとの競合の可能性があります。少し時間を空けて GitHub Actions の「CIS Watchlist修復｜手動実行」を再実行してください。"
                ),
                "action_label": "失敗ログを確認する",
                "action_url": _action_url(persist),
            }
        )

    return cards
