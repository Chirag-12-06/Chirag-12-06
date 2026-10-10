#!/usr/bin/env python3
"""
Generate an isometric 'contribution forest' SVG for a GitHub profile README.

Each day of the past year is an isometric tile. Days with contributions grow
a cypress tree whose height and colour scale with a fixed 6-level threshold
table. Days with zero contributions stay bare ground (no tree).

Usage:
    python generate_forest.py --username YOUR_GITHUB_USERNAME --token $GITHUB_TOKEN --out forest.svg
    python generate_forest.py --demo --out forest.svg      # synthetic data, no token needed
"""

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
TILE_W = 22
TILE_H = 12
PAD_SIDE = 30
PAD_TOP = 50
PAD_BOTTOM = 55

# ---------------------------------------------------------------------------
# Colours / levels
# ---------------------------------------------------------------------------
GROUND_COLOR = "#182022"
GROUND_STROKE = "#2b3538"
TRUNK_COLOR = "#7b4a24"

# Level 1..6 (level 0 = no tree). Light -> dark green; the darkest is still
# clearly green so it never blends into the ground tile.
LEVEL_COLORS = ["#98FB98", "#50C878", "#00A86B", "#2E8B57", "#1B7F3B", "#0F5A2A"]
LEVEL_THRESHOLDS = [1, 2, 3, 5, 9, 13]  # minimum contribution count for level 1..6
MAX_LEVEL = len(LEVEL_COLORS)

assert len(LEVEL_COLORS) == len(LEVEL_THRESHOLDS), "colours and thresholds must match"


def _darken(hex_color: str, factor: float) -> str:
    """Scale a colour toward black (0 = black, 1 = unchanged)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def _lighten(hex_color: str, factor: float) -> str:
    """Scale a colour toward white (0 = unchanged, 1 = white)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (
        f"#{int(r + (255 - r) * factor):02x}"
        f"{int(g + (255 - g) * factor):02x}"
        f"{int(b + (255 - b) * factor):02x}"
    )


DARK_LEVEL_COLORS = [_darken(c, 0.55) for c in LEVEL_COLORS]
LIGHT_LEVEL_COLORS = [_lighten(c, 0.35) for c in LEVEL_COLORS]


def _tree_scale(level: int) -> float:
    return 1.05 + level * 0.4


# Tallest possible tree rises this far above a tile's vertical centre.
_MAX_SCALE = _tree_scale(MAX_LEVEL)
MAX_TREE_RISE = _MAX_SCALE * 3 + _MAX_SCALE * 19  # trunk_h + foliage height


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def fetch_contributions(username: str, token: str):
    body = json.dumps({"query": QUERY, "variables": {"login": username}}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "tree-forest-generator",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API returned HTTP {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach GitHub API: {e.reason}")

    if "errors" in data:
        raise RuntimeError(f"GitHub API error: {data['errors']}")
    if not data.get("data") or not data["data"].get("user"):
        raise RuntimeError(f"User '{username}' not found")

    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return cal["weeks"], cal["totalContributions"]


def demo_contributions(seed: int = 7):
    """Synthetic 53-week calendar for testing without a token."""
    rng = random.Random(seed)
    weeks, total = [], 0
    for _ in range(53):
        days = []
        for _ in range(7):
            count = rng.choice([0, 0, 0, 1, 2, 3, 4, 6, 9, 12, 15, 20])
            total += count
            days.append({"date": "", "contributionCount": count})
        weeks.append({"contributionDays": days})
    return weeks, total


def level_for_count(count: int) -> int:
    """Bucket a raw contribution count into 0 (no tree) or 1-6."""
    level = 0
    for i, threshold in enumerate(LEVEL_THRESHOLDS, start=1):
        if count >= threshold:
            level = i
    return level


def compute_streaks(all_days):
    """Longest and current streaks.

    Matches github-readme-streak-stats: a zero-contribution *today* does not
    break the current streak (the day isn't over yet).
    """
    longest = running = 0
    for d in all_days:
        if d["contributionCount"] > 0:
            running += 1
            longest = max(longest, running)
        else:
            running = 0

    idx = len(all_days) - 1
    if idx >= 0 and all_days[idx]["contributionCount"] == 0:
        idx -= 1

    current = 0
    while idx >= 0 and all_days[idx]["contributionCount"] > 0:
        current += 1
        idx -= 1

    return longest, current


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def iso_pos(week_idx: int, day_idx: int, offset_x: float, offset_y: float):
    """Map a (week, day) cell to isometric screen coordinates."""
    x = offset_x + (week_idx - day_idx) * (TILE_W / 2)
    y = offset_y + (week_idx + day_idx) * (TILE_H / 2)
    return x, y


def draw_ground_tile(x: float, y: float) -> str:
    pts = [
        (x, y),
        (x + TILE_W / 2, y + TILE_H / 2),
        (x, y + TILE_H),
        (x - TILE_W / 2, y + TILE_H / 2),
    ]
    pts_str = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    return (
        f'<polygon points="{pts_str}" fill="{GROUND_COLOR}" '
        f'stroke="{GROUND_STROKE}" stroke-width="0.5"/>'
    )


def draw_tree(x: float, y: float, level: int) -> str:
    """Cypress tree for level 1..MAX_LEVEL, anchored at the tile centre."""
    color = LEVEL_COLORS[level - 1]
    dark_color = DARK_LEVEL_COLORS[level - 1]
    light_color = LIGHT_LEVEL_COLORS[level - 1]

    scale = _tree_scale(level)
    trunk_h = 3 * scale
    trunk_w = 2.4 * scale
    height = 19 * scale
    width = 10 * scale

    base_x = x
    base_y = y + TILE_H / 2
    foliage_base_y = base_y - trunk_h
    top_y = foliage_base_y - height
    mid_y = foliage_base_y - height * 0.55

    shadow_rx = width * 0.55
    shadow_ry = shadow_rx * 0.32
    shadow = (
        f'<ellipse cx="{base_x + shadow_rx * 0.18:.1f}" cy="{base_y + 1:.1f}" '
        f'rx="{shadow_rx:.1f}" ry="{shadow_ry:.1f}" fill="#000000" opacity="0.28"/>'
    )

    trunk = (
        f'<rect x="{base_x - trunk_w / 2:.1f}" y="{base_y - trunk_h:.1f}" '
        f'width="{trunk_w:.1f}" height="{trunk_h:.1f}" fill="{TRUNK_COLOR}"/>'
    )

    foliage = (
        f'<path d="M {base_x:.1f} {top_y:.1f} '
        f'C {base_x - width * 0.46:.1f} {top_y + height * 0.32:.1f}, '
        f'{base_x - width * 0.5:.1f} {mid_y:.1f}, '
        f'{base_x - width * 0.28:.1f} {foliage_base_y:.1f} '
        f'L {base_x + width * 0.28:.1f} {foliage_base_y:.1f} '
        f'C {base_x + width * 0.5:.1f} {mid_y:.1f}, '
        f'{base_x + width * 0.46:.1f} {top_y + height * 0.32:.1f}, '
        f'{base_x:.1f} {top_y:.1f} Z" fill="{color}" '
        f'stroke="{dark_color}" stroke-width="{max(0.5, scale * 0.28):.1f}" '
        f'stroke-linejoin="round"/>'
    )

    highlight = (
        f'<path d="M {base_x + width * 0.08:.1f} {top_y + height * 0.12:.1f} '
        f'C {base_x + width * 0.28:.1f} {top_y + height * 0.32:.1f}, '
        f'{base_x + width * 0.3:.1f} {mid_y:.1f}, '
        f'{base_x + width * 0.16:.1f} {foliage_base_y - height * 0.08:.1f}" '
        f'stroke="{light_color}" stroke-width="{max(0.6, scale * 0.35):.1f}" '
        f'fill="none" opacity="0.55" stroke-linecap="round"/>'
    )

    return shadow + trunk + foliage + highlight


def render_svg(weeks, total: int) -> str:
    if not weeks:
        raise ValueError("No contribution weeks to render")

    all_days = [d for w in weeks for d in w["contributionDays"]]
    longest_streak, current_streak = compute_streaks(all_days)

    max_week = len(weeks) - 1

    # Extents of the diagonal tile strip (computed analytically).
    raw_min_x = (0 - 6) * (TILE_W / 2) - TILE_W / 2
    raw_max_x = max_week * (TILE_W / 2) + TILE_W / 2
    raw_min_y = -MAX_TREE_RISE
    raw_max_y = (max_week + 6) * (TILE_H / 2) + TILE_H

    width = (raw_max_x - raw_min_x) + PAD_SIDE * 2
    height = (raw_max_y - raw_min_y) + PAD_TOP + PAD_BOTTOM

    offset_x = PAD_SIDE - raw_min_x
    offset_y = PAD_TOP - raw_min_y

    # Back-to-front so nearer (larger week+day) trees overlap farther ones.
    cells = [
        (wi, di, day["contributionCount"])
        for wi, week in enumerate(weeks)
        for di, day in enumerate(week["contributionDays"])
    ]
    cells.sort(key=lambda c: (c[0] + c[1], c[0]))

    ground_parts, tree_parts = [], []
    for wi, di, count in cells:
        x, y = iso_pos(wi, di, offset_x, offset_y)
        ground_parts.append(draw_ground_tile(x, y))
        level = level_for_count(count)
        if level > 0:
            tree_parts.append(draw_tree(x, y, level))

    ground_block = "\n".join(ground_parts)
    tree_block = "\n".join(tree_parts)

    top_right_text = (
        f'<text x="{width - PAD_SIDE:.0f}" y="24" font-family="sans-serif" font-size="13" '
        f'font-weight="600" text-anchor="end" fill="#39d353">{total} contributions</text>\n'
        f'<text x="{width - PAD_SIDE:.0f}" y="40" font-family="sans-serif" font-size="11" '
        f'text-anchor="end" fill="#8b949e">in the last year</text>'
    )

    bottom_left_text = (
        f'<text x="{PAD_SIDE}" y="{height - 34:.0f}" font-family="sans-serif" font-size="13" '
        f'font-weight="600" fill="#39d353">Longest streak: {longest_streak} days</text>\n'
        f'<text x="{PAD_SIDE}" y="{height - 16:.0f}" font-family="sans-serif" font-size="11" '
        f'fill="#8b949e">Current streak: {current_streak} days</text>'
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}">
<rect width="100%" height="100%" fill="none"/>
<g>
{ground_block}
</g>
<g>
{tree_block}
</g>
{top_right_text}
{bottom_left_text}
</svg>'''


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate an isometric contribution forest SVG.")
    parser.add_argument("--username", help="GitHub username")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"),
                        help="GitHub token (or set GITHUB_TOKEN)")
    parser.add_argument("--out", default="forest.svg", help="Output SVG path")
    parser.add_argument("--demo", action="store_true", help="Use synthetic data (no token needed)")
    args = parser.parse_args()

    if args.demo:
        weeks, total = demo_contributions()
    else:
        if not args.username:
            parser.error("--username is required unless --demo is used")
        if not args.token:
            print("Error: no token provided (use --token or set GITHUB_TOKEN)", file=sys.stderr)
            sys.exit(1)
        try:
            weeks, total = fetch_contributions(args.username, args.token)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    svg = render_svg(weeks, total)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"Wrote {args.out} ({total} contributions, {len(weeks)} weeks)")


if __name__ == "__main__":
    main()
