
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import html

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DOCS = ROOT / "docs"
LATEST = DOCS / "latest"

JST = ZoneInfo("Asia/Tokyo")


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def newest(pattern):
    files = sorted(OUT.glob(pattern))
    return files[-1] if files else None


def read_text(path):
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_between(text, start_patterns, stop_patterns=None, max_lines=120):
    if not text:
        return "未生成"

    lines = text.splitlines()
    start = 0

    for i, line in enumerate(lines):
        if any(p in line for p in start_patterns):
            start = i
            break

    end = min(len(lines), start + max_lines)
    if stop_patterns:
        for i in range(start + 1, len(lines)):
            if any(p in lines[i] for p in stop_patterns):
                end = i
                break

    section = "\n".join(lines[start:end]).strip()
    return section if section else "未生成"


def md_to_html(md):
    out = []
    in_ul = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            continue

        if line.startswith("### "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h4>{html.escape(line[4:])}</h4>")
        elif line.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h3>{html.escape(line[3:])}</h3>")
        elif line.startswith("# "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h2>{html.escape(line[2:])}</h2>")
        elif line.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<p>{html.escape(line)}</p>")

    if in_ul:
        out.append("</ul>")

    return "\n".join(out)


def copy_latest(src, dest_name):
    if not src or not src.exists():
        return None
    LATEST.mkdir(parents=True, exist_ok=True)
    dest = LATEST / dest_name
    dest.write_text(src.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    return dest


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    LATEST.mkdir(parents=True, exist_ok=True)

    buy = newest("cis_buy_alert_*.md")
    weekly = newest("cis_t04_weekly_report_*.md")
    monthly = newest("buy_zone_monthly_review_*.md")
    ratings = newest("ratings_weekly_*.md")

    buy_text = read_text(buy)
    weekly_text = read_text(weekly)
    monthly_text = read_text(monthly)
    ratings_text = read_text(ratings)

    ratings_summary = extract_between(
        ratings_text,
        ["## TradingViewレーティング品質", "TradingViewレーティング品質"],
        ["## 重要ルール", "## 確認が必要"],
        max_lines=80,
    )

    buy_summary = extract_between(
        buy_text,
        ["## 今日のサマリー", "今日のサマリー"],
        ["## まず見る候補"],
        max_lines=50,
    )

    buy_candidates = extract_between(
        buy_text,
        ["## まず見る候補・全件", "まず見る候補・全件", "まず見る候補"],
        ["## 詳細"],
        max_lines=120,
    )

    weekly_summary = extract_between(
        weekly_text,
        ["## サマリー", "サマリー", "## 米国株", "米国株"],
        ["## 全件", "## 日本株", "## 取得不可"],
        max_lines=70,
    )

    monthly_summary = extract_between(
        monthly_text,
        ["## iPhoneでまず見るところ", "## サマリー", "サマリー"],
        ["## 出力ファイル", "## 運用ルール"],
        max_lines=100,
    )

    copy_latest(buy, "buy_alert_latest.md")
    copy_latest(weekly, "weekly_report_latest.md")
    copy_latest(monthly, "buy_zone_monthly_review_latest.md")
    copy_latest(ratings, "ratings_latest.md")

    css = """
:root {
  --bg: #f6f7f9;
  --card: #ffffff;
  --text: #111827;
  --muted: #6b7280;
  --border: #e5e7eb;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 16px;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
}
header { margin-bottom: 14px; }
h1 { font-size: 24px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 13px; }
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 14px;
  margin: 12px 0;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
h2 { font-size: 19px; margin: 0 0 8px; }
h3 { font-size: 17px; margin: 12px 0 6px; }
h4 { font-size: 15px; margin: 10px 0 4px; }
p { margin: 6px 0; }
ul { padding-left: 18px; margin: 6px 0 10px; }
li { margin: 5px 0; }
a { color: #0f62fe; text-decoration: none; }
.links { display: grid; grid-template-columns: 1fr; gap: 8px; }
.linkbtn {
  display: block;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: #fbfdff;
  font-weight: 600;
}
.badge {
  display: inline-block;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  margin-bottom: 8px;
}
.footer { color: var(--muted); font-size: 12px; padding: 12px 2px 28px; }
"""

    html_body = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CIS Dashboard</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <h1>📊 CIS Dashboard</h1>
    <div class="sub">最終更新：{html.escape(now_jst())}</div>
    <div class="sub">iPhoneで毎日ここだけ開くための画面</div>
  </header>

  <section class="card">
    <span class="badge">毎朝見る</span>
    <h2>買い場アラート</h2>
    {md_to_html(buy_summary)}
    {md_to_html(buy_candidates)}
  </section>

  <section class="card">
    <span class="badge">TradingView</span>
    <h2>TradingViewレーティング品質</h2>
    {md_to_html(ratings_summary)}
  </section>

  <section class="card">
    <span class="badge">土曜見る</span>
    <h2>週間騰落まとめ</h2>
    {md_to_html(weekly_summary)}
  </section>

  <section class="card">
    <span class="badge">月1見る</span>
    <h2>買い場基準マスター月次レビュー</h2>
    {md_to_html(monthly_summary)}
  </section>

  <section class="card">
    <h2>詳細リンク</h2>
    <div class="links">
      <a class="linkbtn" href="latest/buy_alert_latest.md">最新の買い場アラート全文</a>
      <a class="linkbtn" href="latest/ratings_latest.md">最新のTradingViewレーティング全文</a>
      <a class="linkbtn" href="latest/weekly_report_latest.md">最新の週間騰落全文</a>
      <a class="linkbtn" href="latest/buy_zone_monthly_review_latest.md">最新の月次レビュー全文</a>
      <a class="linkbtn" href="https://github.com/69wmjpm6j2-alt/chappy-investment-system/actions">GitHub Actions</a>
    </div>
  </section>

  <div class="footer">
    CIS DashboardはGitHub Actionsで自動更新。D03はTradingViewへ毎日アクセスせず、保存済みTV情報を読む。
  </div>
</body>
</html>
"""
    (DOCS / "index.html").write_text(html_body, encoding="utf-8")
    print("created docs/index.html")
    print("created docs/latest/*.md")


if __name__ == "__main__":
    main()
