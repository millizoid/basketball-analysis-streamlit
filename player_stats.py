import re
import urllib.request
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from typing import Optional, Dict, Any
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.error import HTTPError, URLError


# ============================================================
# Low-level HTML / scraping helpers
# ============================================================

def url_get_contents(url: str) -> str:
    """Fetch HTML from a URL and return as decoded text."""
    req = urllib.request.Request(url=url)
    with urllib.request.urlopen(req) as f:
        return f.read().decode("utf-8")


def _is_game_log_table(table) -> bool:
    """
    Decide whether a <table class="my_Title"> is a game-log table.

    On usbasket, game-log tables have a header row like:
    Date | Team | Against Team | Result | MIN | PTS | 2FGP | 3FGP | FT | ...
    and rows with classes my_pStats1/my_pStats2.
    """
    header_rows = table.find_all("tr", class_="my_Headers")
    if not header_rows:
        return False

    for hr in header_rows:
        headers = [
            cell.get_text(strip=True).lower()
            for cell in hr.find_all(["th", "td"])
        ]
        if (
            "date" in headers
            and "against team" in headers
            and "result" in headers
        ):
            return True

    return False


def _find_latest_season_game_log_table(soup: BeautifulSoup):
    """
    Find the latest-season game-log table.

    Strategy:
    - Look at all <table class="my_Title">.
    - Keep only those that look like game logs (have Date / Against Team / Result).
    - For each candidate, look at nearest previous <h4 class="plstats-head">
      and extract the year.
    - Return the table with the highest year.
    """
    tables = soup.find_all("table", class_="my_Title")
    candidates = []

    for table in tables:
        if not _is_game_log_table(table):
            continue

        # Get nearest previous season header
        h4 = table.find_previous("h4", class_="plstats-head")
        year = None
        if h4 is not None:
            text = h4.get_text(" ", strip=True)
            m = re.search(r"(\d{4})", text)
            if m:
                year = int(m.group(1))

        candidates.append((year, table))

    if not candidates:
        return None

    # Year can be None; treat None as older than any real year
    def _key(yt):
        y, _t = yt
        return (y is not None, y or 0)

    candidates.sort(key=_key, reverse=True)
    return candidates[0][1]


def scrape_player_game_log(url: str) -> pd.DataFrame:
    """
    High-level scraper: given a player URL, return the latest-season game log
    as a pandas DataFrame.
    """
    xhtml = url_get_contents(url)
    soup = BeautifulSoup(xhtml, "html.parser")

    table = _find_latest_season_game_log_table(soup)
    if table is None:
        raise ValueError(
            "Could not find a game-log stats table on this page "
            "(no table with 'Date' / 'Against Team' / 'Result' headers)."
        )

    # Parse rows
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) <= 1:
            continue
        rows.append(cells)

    if not rows:
        raise ValueError("No data rows found in selected game-log table.")

    col_count = max(len(r) for r in rows)
    rows_same_len = [r for r in rows if len(r) == col_count]

    if not rows_same_len:
        raise ValueError("Could not find any consistently-sized rows to form a table.")

    header = rows_same_len[0]
    data_rows = rows_same_len[1:]

    df = pd.DataFrame(data_rows, columns=header)
    return df

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)

def _extract_usbasket_from_player_url(candidate: str) -> Optional[str]:
    """
    Given a Eurobasket or USBasket player URL, normalize it to the canonical
    USBasket form:

        https://basketball.usbasket.com/player/Slug/ID

    Examples:
        https://basketball.eurobasket.com/player/LeBron-James/NBA/Cleveland-Cavaliers/52424
        https://basketball.usbasket.com/player/Martavian-Payne/330331
    """
    candidate = candidate.split("?", 1)[0]  # drop query params
    parsed = urlparse(candidate)

    path_parts = parsed.path.strip("/").split("/")
    # We expect something like: ["player", "Slug", "...", "ID"]
    if len(path_parts) < 3 or path_parts[0].lower() != "player":
        return None

    slug = path_parts[1]
    player_id = path_parts[-1]

    if not player_id.isdigit():
        return None

    return f"https://basketball.usbasket.com/player/{slug}/{player_id}"



USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)

SEARCH_URL = "https://www.eurobasket.com/basketball-search.aspx"


def find_usbasket_player_url_by_name(name: str) -> Optional[str]:
    """
    Use Eurobasket's basketball-search.aspx endpoint to look up a player.
    The endpoint responds with a 302 redirect to the full USbasket player URL,
    e.g. https://basketball.usbasket.com/player/LeBron-James/52424
    """
    name = name.strip()
    if not name:
        return None

    # 🔴 IMPORTANT: change these keys to match the "Form Data" you see in DevTools
    # Example ONLY – replace 'txtSearch' and 'SearchType' with the real names:
    form_data = {
        "txtSearch": name,      # <-- whatever field holds "LeBron James"
        "SearchType": "Player" # <-- if there is a type/section field; otherwise remove
    }

    encoded = urllib.parse.urlencode(form_data).encode("utf-8")

    req = urllib.request.Request(
        SEARCH_URL,
        data=encoded,  # POST body
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://basketball.usbasket.com",
            "Referer": "https://basketball.usbasket.com/",
        },
        method="POST",
    )

    try:
        # urlopen will follow the 302 by default
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl()
    except (HTTPError, URLError) as e:
        print("Error hitting basketball-search.aspx:", e)
        return None
    except Exception as e:
        print("Unexpected error:", e)
        return None

    # We expect something like: https://basketball.usbasket.com/player/Name/ID
    if "basketball.usbasket.com/player/" in final_url:
        final_url = final_url.split("?", 1)[0].split("#", 1)[0]
        return final_url

    return None

# ============================================================
# Advanced stats helpers
# ============================================================

def split_made_attempts(series: pd.Series):
    """Convert strings like '7-13' into made, attempts integer series."""
    parts = series.fillna("0-0").str.split("-", n=1, expand=True)
    parts[0] = pd.to_numeric(parts[0], errors="coerce").fillna(0).astype(int)
    parts[1] = pd.to_numeric(parts[1], errors="coerce").fillna(0).astype(int)
    made = parts[0]
    att = parts[1]
    return made, att


def add_parsed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add parsed shooting + result columns to the raw game log."""
    df = df.rename(columns={c: c.strip() for c in df.columns})

    # Shooting splits
    df["two_made"], df["two_att"] = split_made_attempts(df["2FGP"])
    df["three_made"], df["three_att"] = split_made_attempts(df["3FGP"])
    df["ft_made"], df["ft_att"] = split_made_attempts(df["FT"])

    df["fgm"] = df["two_made"] + df["three_made"]
    df["fga"] = df["two_att"] + df["three_att"]

    # Result → team / opp score
    team_score = df["Result"].str.split("-", n=1, expand=True)[0]
    opp_score = df["Result"].str.split("-", n=1, expand=True)[1]
    team_score = pd.to_numeric(team_score, errors="coerce").fillna(0).astype(int)
    opp_score = pd.to_numeric(opp_score, errors="coerce").fillna(0).astype(int)

    df["team_score"] = team_score
    df["opp_score"] = opp_score
    df["margin"] = df["team_score"] - df["opp_score"]
    df["win"] = df["margin"] > 0

    # Rebounds
    if "RT" in df.columns:
        df["reb"] = pd.to_numeric(df["RT"], errors="coerce").fillna(0).astype(int)
    else:
        df["reb"] = (
            pd.to_numeric(df["RO"], errors="coerce").fillna(0).astype(int)
            + pd.to_numeric(df["RD"], errors="coerce").fillna(0).astype(int)
        )

    # Core box-score numeric columns
    numeric_cols = ["MIN", "PTS", "RO", "RD", "AS", "PF", "BS", "ST", "TO", "RNK"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


def add_game_advanced_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add TS%, eFG%, GameScore, per-36 style metrics to each game."""
    df["efg"] = np.where(
        df["fga"] > 0,
        (df["fgm"] + 0.5 * df["three_made"]) / df["fga"],
        np.nan,
    )

    df["ts"] = np.where(
        (df["fga"] + 0.44 * df["ft_att"]) > 0,
        df["PTS"] / (2 * (df["fga"] + 0.44 * df["ft_att"])),
        np.nan,
    )

    df["game_score"] = (
        df["PTS"]
        + 0.4 * df["fgm"]
        - 0.7 * df["fga"]
        - 0.4 * (df["ft_att"] - df["ft_made"])
        + 0.7 * df["RO"]
        + 0.3 * df["RD"]
        + df["ST"]
        + 0.7 * df["AS"]
        + 0.7 * df["BS"]
        - 0.4 * df["PF"]
        - df["TO"]
    )

    df["pts_per_36"] = np.where(df["MIN"] > 0, df["PTS"] / df["MIN"] * 36, np.nan)
    df["fga_per_36"] = np.where(df["MIN"] > 0, df["fga"] / df["MIN"] * 36, np.nan)

    return df


def summarize_overall(df: pd.DataFrame) -> Dict[str, Any]:
    games = len(df)
    total_minutes = df["MIN"].sum()

    per_game_stats = df[["PTS", "reb", "AS", "ST", "BS", "TO", "PF"]].mean()
    per_36_stats = per_game_stats / df["MIN"].mean() * 36 if games > 0 else np.nan

    totals = df[["PTS", "reb", "AS", "ST", "BS", "TO", "PF"]].sum()

    totals_shooting = {
        "2FGM": df["two_made"].sum(),
        "2FGA": df["two_att"].sum(),
        "3FGM": df["three_made"].sum(),
        "3FGA": df["three_att"].sum(),
        "FTM": df["ft_made"].sum(),
        "FTA": df["ft_att"].sum(),
    }
    totals_shooting["FGM"] = totals_shooting["2FGM"] + totals_shooting["3FGM"]
    totals_shooting["FGA"] = totals_shooting["2FGA"] + totals_shooting["3FGA"]

    if totals_shooting["FGA"] > 0:
        efg = (totals_shooting["FGM"] + 0.5 * totals_shooting["3FGM"]) / totals_shooting["FGA"]
    else:
        efg = np.nan

    denom_ts = (totals_shooting["FGA"] + 0.44 * totals_shooting["FTA"])
    if denom_ts > 0:
        ts = totals["PTS"] / (2 * denom_ts)
    else:
        ts = np.nan

    advanced = {
        "games": games,
        "total_minutes": total_minutes,
        "per_game": per_game_stats.to_dict(),
        "per_36": per_36_stats.to_dict() if games > 0 else {},
        "totals": totals.to_dict(),
        "shooting_totals": totals_shooting,
        "efg": efg,
        "ts": ts,
        "avg_game_score": df["game_score"].mean(),
    }

    return advanced


def summarize_splits(df: pd.DataFrame) -> Dict[str, Any]:
    win_split = df.groupby("win")[["PTS", "reb", "AS", "ST", "BS", "TO", "ts", "efg", "game_score"]].mean()
    opp_split = df.groupby("Against Team")[["PTS", "reb", "AS", "ST", "BS", "TO", "ts", "efg", "game_score"]].mean()
    corr_minutes_pts = df["MIN"].corr(df["PTS"])
    corr_minutes_game_score = df["MIN"].corr(df["game_score"])

    return {
        "wins_vs_losses": win_split,
        "by_opponent": opp_split,
        "corr_MIN_PTS": corr_minutes_pts,
        "corr_MIN_GameScore": corr_minutes_game_score,
    }


# ============================================================
# Export helpers (HTML + CSV)
# ============================================================

def build_summary_html(player_url: str, overall: Dict[str, Any], splits: Dict[str, Any]) -> str:
    """Create a simple, nicely formatted HTML summary report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    per_game_df = pd.DataFrame(overall["per_game"], index=["Per game"]).T.round(2)
    per36_df = pd.DataFrame(overall["per_36"], index=["Per 36"]).T.round(2)
    totals_df = pd.DataFrame(overall["totals"], index=["Total"]).T.astype(int)
    shooting_df = pd.DataFrame(overall["shooting_totals"], index=["Total"]).T.astype(int)

    win_loss_df = splits["wins_vs_losses"].copy().round(2)
    win_loss_df.index = win_loss_df.index.map(lambda x: "Win" if x else "Loss")
    opp_df = splits["by_opponent"].copy().round(2)

    # Convert tables to HTML
    def to_html_table(df: pd.DataFrame) -> str:
        return df.to_html(classes="data-table", border=0)

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Player Summary Report</title>
<style>
    body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 24px;
        color: #222;
    }}
    h1, h2, h3 {{
        color: #222;
        margin-bottom: 0.2rem;
    }}
    .subtitle {{
        color: #666;
        margin-bottom: 1.5rem;
    }}
    .metric-row {{
        display: flex;
        gap: 24px;
        margin-bottom: 1rem;
    }}
    .metric {{
        background: #f7f7f7;
        border-radius: 8px;
        padding: 12px 16px;
        min-width: 140px;
    }}
    .metric-label {{
        font-size: 0.75rem;
        text-transform: uppercase;
        color: #777;
        letter-spacing: 0.08em;
    }}
    .metric-value {{
        font-size: 1.4rem;
        font-weight: 600;
    }}
    table.data-table {{
        border-collapse: collapse;
        width: 100%;
        margin-bottom: 1.5rem;
        font-size: 0.9rem;
    }}
    table.data-table th, table.data-table td {{
        border: 1px solid #ddd;
        padding: 6px 8px;
        text-align: right;
    }}
    table.data-table th {{
        background-color: #f0f0f0;
        text-align: center;
    }}
    table.data-table tr:nth-child(even) {{
        background-color: #fafafa;
    }}
    .section {{
        margin-top: 1.75rem;
    }}
</style>
</head>
<body>
    <h1>Player Advanced Stats Summary</h1>
    <div class="subtitle">
        Generated: {now}<br>
        Source: <a href="{player_url}">{player_url}</a>
    </div>

    <div class="metric-row">
        <div class="metric">
            <div class="metric-label">Games</div>
            <div class="metric-value">{overall["games"]}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Total Minutes</div>
            <div class="metric-value">{overall["total_minutes"]:.1f}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Season eFG%</div>
            <div class="metric-value">{overall["efg"]:.3f}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Season TS%</div>
            <div class="metric-value">{overall["ts"]:.3f}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Avg Game Score</div>
            <div class="metric-value">{overall["avg_game_score"]:.2f}</div>
        </div>
    </div>

    <div class="section">
        <h2>Per-Game Averages</h2>
        {to_html_table(per_game_df)}
    </div>

    <div class="section">
        <h2>Per-36-Minute Averages</h2>
        {to_html_table(per36_df)}
    </div>

    <div class="section">
        <h2>Totals</h2>
        {to_html_table(totals_df)}
    </div>

    <div class="section">
        <h2>Shooting Totals</h2>
        {to_html_table(shooting_df)}
    </div>

    <div class="section">
        <h2>Wins vs. Losses</h2>
        {to_html_table(win_loss_df)}
    </div>

    <div class="section">
        <h2>By Opponent</h2>
        {to_html_table(opp_df)}
    </div>

    <div class="section">
        <h2>Correlations</h2>
        <p>Corr(MIN, PTS): {splits["corr_MIN_PTS"]:.3f}<br>
           Corr(MIN, Game Score): {splits["corr_MIN_GameScore"]:.3f}</p>
    </div>
</body>
</html>
"""
    return html
