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
MARGIN = 40

# GitHub-style green ramp, dark->light unused levels get muted ground tile
GROUND_COLOR = "#ebedf0"
GROUND_STROKE = "#d8dbe0"
TRUNK_COLOR = "#7b4a24"
LEVEL_COLORS = ["#9be9a8", "#40c463", "#30a14e", "#216e39"]  # level 1..4


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


def iso_pos(week_idx: int, day_idx: int):
    """Map a (week, day) grid cell to isometric screen coordinates."""
    x = MARGIN + (week_idx - day_idx) * (TILE_W / 2)
    y = MARGIN + (week_idx + day_idx) * (TILE_H / 2)
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
    """A small front-facing pine tree, sized and colored by contribution level."""
    color = LEVEL_COLORS[level - 1]
    scale = 0.6 + level * 0.22  # level 1 smallest, level 4 tallest
    trunk_h = 3 * scale
    trunk_w = 2.2 * scale
    crown_h = 12 * scale
    crown_w = 11 * scale

    base_x = x
    base_y = y + TILE_H / 2  # anchor at tile center, tree grows upward

    trunk = (
        f'<rect x="{base_x - trunk_w/2:.1f}" y="{base_y - trunk_h:.1f}" '
        f'width="{trunk_w:.1f}" height="{trunk_h:.1f}" fill="{TRUNK_COLOR}"/>'
    )

    tiers = []
    tier_count = 3
    for i in range(tier_count):
        frac_top = i / tier_count
        frac_bottom = (i + 1) / tier_count
        tier_top_y = base_y - trunk_h - crown_h * (1 - frac_top)
        tier_bottom_y = base_y - trunk_h - crown_h * (1 - frac_bottom) + crown_h * 0.12
        tier_w = crown_w * (0.45 + 0.55 * frac_bottom)
        pts = [
            (base_x, tier_top_y),
            (base_x + tier_w / 2, tier_bottom_y),
            (base_x - tier_w / 2, tier_bottom_y),
        ]
        pts_str = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        tiers.append(f'<polygon points="{pts_str}" fill="{color}"/>')

    return trunk + "".join(tiers)


def render_svg(weeks, total: int) -> str:
    all_days = [d for w in weeks for d in w["contributionDays"]]
    max_count = max((d["contributionCount"] for d in all_days), default=0)

    num_weeks = len(weeks)
    width = MARGIN * 2 + (num_weeks + 7) * (TILE_W / 2)
    height = MARGIN * 2 + (num_weeks + 7) * (TILE_H / 2)

    ground_parts = []
    tree_parts = []

    for week_idx, week in enumerate(weeks):
        for day_idx, day in enumerate(week["contributionDays"]):
            x, y = iso_pos(week_idx, day_idx)
            ground_parts.append(draw_ground_tile(x, y))
            level = level_for_count(day["contributionCount"], max_count)
            if level > 0:
                tree_parts.append(draw_tree(x, y, level))

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}">
<rect width="100%" height="100%" fill="none"/>
<g>{"".join(ground_parts)}</g>
<g>{"".join(tree_parts)}</g>
<text x="{MARGIN}" y="{height - 10:.0f}" font-family="sans-serif" font-size="11" fill="#57606a">{total} contributions in the last year</text>
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
