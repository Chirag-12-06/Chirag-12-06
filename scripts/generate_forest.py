#!/usr/bin/env python3
"""
Generate an isometric 'contribution forest' SVG for a GitHub profile README.

Same idea as the classic isometric contribution skyline, but each day is
rendered as a small isometric pine tree instead of a building. Tree height
and color intensity scale with that day's contribution count.

Usage:
    python generate_forest.py --username YOUR_GITHUB_USERNAME --token $GITHUB_TOKEN --out forest.svg
"""

import argparse
import os
import sys
import urllib.request
import json

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

# Tile / tree geometry (isometric diamond grid)
TILE_W = 22
TILE_H = 12
PAD_SIDE = 30     # left/right breathing room
PAD_TOP = 50      # top room, must fit the tallest tree + top-right text
PAD_BOTTOM = 55   # bottom room, must fit the bottom-left streak text

# GitHub-style green ramp, dark->light unused levels get muted ground tile
GROUND_COLOR = "#ebedf0"
GROUND_STROKE = "#d8dbe0"
TRUNK_COLOR = "#7b4a24"
LEVEL_COLORS = ["#9be9a8", "#40c463", "#30a14e", "#216e39"]  # level 1..4

# Tallest possible tree (level 4) extends this far above a tile's vertical center
_LEVEL4_SCALE = 0.6 + 4 * 0.24
MAX_TREE_RISE = _LEVEL4_SCALE * 2 + _LEVEL4_SCALE * 15  # trunk_h + height at level 4


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
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)

    if "errors" in data:
        raise RuntimeError(f"GitHub API error: {data['errors']}")

    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    total = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]
    return weeks, total


def level_for_count(count: int, max_count: int) -> int:
    """Bucket a raw contribution count into 0-4, like GitHub's own heatmap."""
    if count <= 0:
        return 0
    if max_count <= 0:
        return 0
    ratio = count / max_count
    if ratio <= 0.25:
        return 1
    if ratio <= 0.5:
        return 2
    if ratio <= 0.75:
        return 3
    return 4


def iso_pos(week_idx: int, day_idx: int, offset_x: float, offset_y: float):
    """Map a (week, day) grid cell to isometric screen coordinates."""
    x = offset_x + (week_idx - day_idx) * (TILE_W / 2)
    y = offset_y + (week_idx + day_idx) * (TILE_H / 2)
    return x, y


def draw_ground_tile(x: float, y: float) -> str:
    """A flat diamond tile representing one day."""
    pts = [
        (x, y),
        (x + TILE_W / 2, y + TILE_H / 2),
        (x, y + TILE_H),
        (x - TILE_W / 2, y + TILE_H / 2),
    ]
    pts_str = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    return f'<polygon points="{pts_str}" fill="{GROUND_COLOR}" stroke="{GROUND_STROKE}" stroke-width="0.5"/>'


def draw_tree(x: float, y: float, level: int) -> str:
    """A tall, narrow front-facing cypress tree, sized and colored by contribution level."""
    color = LEVEL_COLORS[level - 1]
    scale = 0.6 + level * 0.24  # level 1 shortest, level 4 tallest
    trunk_h = 2 * scale
    trunk_w = 1.4 * scale
    height = 15 * scale
    width = 6.5 * scale

    base_x = x
    base_y = y + TILE_H / 2  # anchor at tile center, tree grows upward
    foliage_base_y = base_y - trunk_h
    top_y = foliage_base_y - height
    mid_y = foliage_base_y - height * 0.55

    trunk = (
        f'<rect x="{base_x - trunk_w/2:.1f}" y="{base_y - trunk_h:.1f}" '
        f'width="{trunk_w:.1f}" height="{trunk_h:.1f}" fill="{TRUNK_COLOR}"/>'
    )

    # Slender spindle silhouette: narrow point at top, gentle bulge mid-way, tapered base
    path = (
        f'<path d="M {base_x:.1f} {top_y:.1f} '
        f'C {base_x - width*0.46:.1f} {top_y + height*0.32:.1f}, '
        f'{base_x - width*0.5:.1f} {mid_y:.1f}, '
        f'{base_x - width*0.28:.1f} {foliage_base_y:.1f} '
        f'L {base_x + width*0.28:.1f} {foliage_base_y:.1f} '
        f'C {base_x + width*0.5:.1f} {mid_y:.1f}, '
        f'{base_x + width*0.46:.1f} {top_y + height*0.32:.1f}, '
        f'{base_x:.1f} {top_y:.1f} Z" fill="{color}"/>'
    )

    return trunk + path


def compute_streaks(all_days):
    """Longest and current consecutive-day contribution streaks."""
    longest = current = running = 0
    for d in all_days:
        if d["contributionCount"] > 0:
            running += 1
            longest = max(longest, running)
        else:
            running = 0
    # current streak = trailing run at the end of the (chronological) list
    for d in reversed(all_days):
        if d["contributionCount"] > 0:
            current += 1
        else:
            break
    return longest, current


def render_svg(weeks, total: int) -> str:
    all_days = [d for w in weeks for d in w["contributionDays"]]
    max_count = max((d["contributionCount"] for d in all_days), default=0)
    longest_streak, current_streak = compute_streaks(all_days)

    num_weeks = len(weeks)
    max_week = num_weeks - 1

    # Raw (unshifted) extents of the diagonal tile strip, computed analytically:
    # x is minimized at (week=0, day=6) and maximized at (week=max_week, day=0);
    # y is minimized at (week=0, day=0) and maximized at (week=max_week, day=6).
    raw_min_x = (0 - 6) * (TILE_W / 2) - TILE_W / 2
    raw_max_x = (max_week - 0) * (TILE_W / 2) + TILE_W / 2
    raw_min_y = (0 + 0) * (TILE_H / 2) - MAX_TREE_RISE  # leave room for a tall tree here
    raw_max_y = (max_week + 6) * (TILE_H / 2) + TILE_H

    content_w = raw_max_x - raw_min_x
    content_h = raw_max_y - raw_min_y

    width = content_w + PAD_SIDE * 2
    height = content_h + PAD_TOP + PAD_BOTTOM

    # Offset that shifts raw_min_x/raw_min_y to sit exactly PAD_SIDE/PAD_TOP from the edges
    offset_x = PAD_SIDE - raw_min_x
    offset_y = PAD_TOP - raw_min_y

    ground_parts = []
    tree_parts = []

    for week_idx, week in enumerate(weeks):
        for day_idx, day in enumerate(week["contributionDays"]):
            x, y = iso_pos(week_idx, day_idx, offset_x, offset_y)
            ground_parts.append(draw_ground_tile(x, y))
            level = level_for_count(day["contributionCount"], max_count)
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

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}">
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
    return svg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--out", default="forest.svg")
    args = parser.parse_args()

    if not args.token:
        print("Error: no token provided (use --token or set GITHUB_TOKEN)", file=sys.stderr)
        sys.exit(1)

    weeks, total = fetch_contributions(args.username, args.token)
    svg = render_svg(weeks, total)

    with open(args.out, "w") as f:
        f.write(svg)

    print(f"Wrote {args.out} ({total} contributions, {len(weeks)} weeks)")


if __name__ == "__main__":
    main()
