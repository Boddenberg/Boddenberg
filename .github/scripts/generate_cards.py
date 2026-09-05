#!/usr/bin/env python3
"""Gera os cards SVG do README (estatísticas, linguagens e atividade).

Os SVGs sao versionados no proprio repositorio e servidos pelo GitHub, entao o
README não depende de nenhum serviço externo de terceiros para renderizar.

Usa apenas a stdlib e o GITHUB_TOKEN nativo do Actions — nenhum secret extra.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.github.com/graphql"

# Paleta tokyonight, a mesma que o README já usava nos cards antigos.
BG = "#1a1b27"
TITLE = "#70a5fd"
TEXT = "#38bdae"
ICON = "#bf91f3"
MUTED = "#4b5263"

FONT = "'Segoe UI', Ubuntu, -apple-system, BlinkMacSystemFont, Sans-Serif"

OCTICONS = {
    "star": "M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z",
    "commit": "M10.5 7.75a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zm1.43.75a4.002 4.002 0 01-7.86 0H.75a.75.75 0 110-1.5h3.32a4.001 4.001 0 017.86 0h3.32a.75.75 0 110 1.5h-3.32z",
    "pr": "M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z",
    "merge": "M5 3.254V3.25v.005a.75.75 0 110-.005v.004zm.45 1.9a2.25 2.25 0 10-1.95.218v5.256a2.25 2.25 0 101.5 0V7.123A5.735 5.735 0 009.25 9h1.378a2.251 2.251 0 100-1.5H9.25a4.25 4.25 0 01-3.8-2.346zM12.75 9a.75.75 0 100-1.5.75.75 0 000 1.5zm-8.5 4.5a.75.75 0 100-1.5.75.75 0 000 1.5z",
}


# --------------------------------------------------------------------------- #
# Coleta de dados
# --------------------------------------------------------------------------- #

def graphql(query: str, variables: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "boddenberg-readme-cards",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GitHub API respondeu {exc.code}: {exc.read().decode()[:500]}")
    if "errors" in body:
        raise SystemExit(f"GraphQL retornou erros: {json.dumps(body['errors'])[:500]}")
    return body["data"]


PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    issues { totalCount }
    pullRequests { totalCount }
    mergedPullRequests: pullRequests(states: MERGED) { totalCount }
    contributionsCollection {
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

COMMITS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) { totalCommitContributions }
  }
}
"""


def all_time_commits(login: str, token: str, created_at: str) -> int:
    """Soma os commits ano a ano (a API limita cada consulta a 1 ano)."""
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    total = 0
    cursor = start
    while cursor < now:
        end = min(cursor + timedelta(days=365), now)
        data = graphql(
            COMMITS_QUERY,
            {"login": login, "from": cursor.isoformat(), "to": end.isoformat()},
            token,
        )
        total += data["user"]["contributionsCollection"]["totalCommitContributions"]
        cursor = end
    return total


def collect(login: str, token: str) -> dict:
    user = graphql(PROFILE_QUERY, {"login": login}, token)["user"]
    if user is None:
        raise SystemExit(f"Usuário '{login}' não encontrado.")

    repos = user["repositories"]["nodes"]
    stars = sum(r["stargazerCount"] for r in repos)

    # Combina bytes e número de repositórios, como o size_weight/count_weight
    # que o README usava antes (0.5 / 0.5).
    langs: dict[str, dict] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = langs.setdefault(
                name, {"name": name, "color": edge["node"]["color"] or "#858585",
                       "size": 0, "count": 0}
            )
            entry["size"] += edge["size"]
            entry["count"] += 1
    for entry in langs.values():
        entry["score"] = (entry["size"] ** 0.5) * (entry["count"] ** 0.5)

    calendar = user["contributionsCollection"]["contributionCalendar"]
    days = [
        day
        for week in calendar["weeks"]
        for day in week["contributionDays"]
    ]

    prs = user["pullRequests"]["totalCount"]
    merged = user["mergedPullRequests"]["totalCount"]

    return {
        "name": user["name"] or user["login"],
        "login": user["login"],
        "stars": stars,
        "commits": all_time_commits(login, token, user["createdAt"]),
        "prs": prs,
        "merged": merged,
        "merged_pct": (merged / prs * 100) if prs else 0.0,
        "issues": user["issues"]["totalCount"],
        "reviews": user["contributionsCollection"]["totalPullRequestReviewContributions"],
        "followers": user["followers"]["totalCount"],
        "contributions": calendar["totalContributions"],
        "languages": sorted(langs.values(), key=lambda e: e["score"], reverse=True),
        "days": days,
    }


def rank(d: dict) -> tuple[str, float]:
    """Reproduz o cálculo de rank do github-readme-stats."""
    exp_cdf = lambda x: 1 - 2 ** -x
    log_cdf = lambda x: x / (1 + x)

    weights = {"commits": 2, "prs": 3, "issues": 1, "reviews": 1, "stars": 4, "followers": 1}
    scores = {
        "commits": exp_cdf(d["commits"] / 250),
        "prs": exp_cdf(d["prs"] / 50),
        "issues": exp_cdf(d["issues"] / 25),
        "reviews": exp_cdf(d["reviews"] / 2),
        "stars": log_cdf(d["stars"] / 50),
        "followers": log_cdf(d["followers"] / 10),
    }
    total = sum(weights.values())
    percentile = 1 - sum(weights[k] * scores[k] for k in weights) / total

    levels = ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]
    thresholds = [1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100]
    value = percentile * 100
    level = next(lv for lv, th in zip(levels, thresholds) if value <= th)
    return level, value


# --------------------------------------------------------------------------- #
# Renderização
# --------------------------------------------------------------------------- #

def esc(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def br_number(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def render_stats(d: dict) -> str:
    width, height = 495, 195
    level, percentile = rank(d)
    rows = [
        ("star", "Total de estrelas", br_number(d["stars"])),
        ("commit", "Total de commits", br_number(d["commits"])),
        ("pr", "Total de PRs", br_number(d["prs"])),
        ("merge", "PRs mesclados", f"{d['merged_pct']:.2f}%".replace(".", ",")),
    ]

    body = []
    for index, (icon, label, value) in enumerate(rows):
        y = index * 28
        body.append(
            f'<g transform="translate(0, {y})">'
            f'<svg x="0" y="-1" viewBox="0 0 16 16" width="16" height="16" fill="{ICON}">'
            f'<path d="{OCTICONS[icon]}"/></svg>'
            f'<text class="label" x="26" y="12.5">{esc(label)}:</text>'
            f'<text class="value" x="245" y="12.5">{esc(value)}</text>'
            f"</g>"
        )

    radius = 38
    circumference = 2 * math.pi * radius
    # percentile baixo = rank melhor = anel mais cheio
    offset = circumference * (percentile / 100)

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
     fill="none" xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="Estatísticas do GitHub de {esc(d['name'])}">
  <title>Estatísticas do GitHub de {esc(d['name'])}</title>
  <style>
    .title {{ font: 600 18px {FONT}; fill: {TITLE}; }}
    .label {{ font: 400 14px {FONT}; fill: {TEXT}; }}
    .value {{ font: 600 14px {FONT}; fill: {TEXT}; }}
    .rank  {{ font: 800 24px {FONT}; fill: {TITLE}; }}
  </style>
  <rect x="0" y="0" width="{width}" height="{height}" rx="6" fill="{BG}"/>
  <text class="title" x="25" y="35">Estatísticas do GitHub de {esc(d['name'])}</text>
  <g transform="translate(25, 58)">
    {"".join(body)}
  </g>
  <g transform="translate({width - 78}, {height / 2 + 4})">
    <circle r="{radius}" fill="none" stroke="{MUTED}" stroke-width="6"/>
    <circle r="{radius}" fill="none" stroke="{TITLE}" stroke-width="6"
            stroke-linecap="round" stroke-dasharray="{circumference:.2f}"
            stroke-dashoffset="{offset:.2f}" transform="rotate(-90)"/>
    <text class="rank" text-anchor="middle" dominant-baseline="central">{esc(level)}</text>
  </g>
</svg>
"""


def render_languages(d: dict, limit: int = 8) -> str:
    width, height = 340, 195
    langs = d["languages"][:limit]
    total = sum(l["score"] for l in langs) or 1

    bar_x, bar_y, bar_w, bar_h = 25, 58, width - 50, 9
    segments, legend = [], []
    offset = 0.0
    for index, lang in enumerate(langs):
        share = lang["score"] / total
        seg_w = share * bar_w
        segments.append(
            f'<rect x="{offset:.2f}" y="0" width="{seg_w:.2f}" height="{bar_h}" '
            f'fill="{esc(lang["color"])}"/>'
        )
        offset += seg_w

        col, row = index % 2, index // 2
        lx, ly = col * 155, row * 26
        pct = f"{share * 100:.1f}".replace(".", ",")
        legend.append(
            f'<g transform="translate({lx}, {ly})">'
            f'<circle cx="5" cy="6" r="5" fill="{esc(lang["color"])}"/>'
            f'<text class="lang" x="16" y="10">{esc(lang["name"])} {pct}%</text>'
            f"</g>"
        )

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
     fill="none" xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="Linguagens mais usadas por {esc(d['name'])}">
  <title>Linguagens mais usadas por {esc(d['name'])}</title>
  <style>
    .title {{ font: 600 18px {FONT}; fill: {TITLE}; }}
    .lang  {{ font: 400 12px {FONT}; fill: {TEXT}; }}
  </style>
  <rect x="0" y="0" width="{width}" height="{height}" rx="6" fill="{BG}"/>
  <text class="title" x="25" y="35">Linguagens mais usadas</text>
  <mask id="bar-mask">
    <rect x="0" y="0" width="{bar_w}" height="{bar_h}" rx="{bar_h / 2}" fill="white"/>
  </mask>
  <g transform="translate({bar_x}, {bar_y})" mask="url(#bar-mask)">
    {"".join(segments)}
  </g>
  <g transform="translate({bar_x}, {bar_y + 24})">
    {"".join(legend)}
  </g>
</svg>
"""


def smooth_path(points: list[tuple[float, float]], baseline: float) -> str:
    """Catmull-Rom convertido para bezier, preso na baseline para não estourar."""
    if len(points) < 2:
        return ""
    d = f"M {points[0][0]:.2f},{points[0][1]:.2f}"
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, min(p1[1] + (p2[1] - p0[1]) / 6, baseline))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, min(p2[1] - (p3[1] - p1[1]) / 6, baseline))
        d += (
            f" C {c1[0]:.2f},{c1[1]:.2f} {c2[0]:.2f},{c2[1]:.2f}"
            f" {p2[0]:.2f},{p2[1]:.2f}"
        )
    return d


def render_activity(d: dict, span: int = 31) -> str:
    width, height = 1000, 350
    left, right, top, bottom = 70, 30, 70, 55
    plot_w = width - left - right
    plot_h = height - top - bottom
    baseline = top + plot_h

    days = d["days"][-span:]
    counts = [day["contributionCount"] for day in days]
    peak = max(counts) if counts else 0
    # Escala arredondada para cima, para o topo do gráfico nunca encostar no título.
    ceiling = max(4, math.ceil((peak or 1) / 4) * 4)

    step = plot_w / max(len(days) - 1, 1)
    points = [
        (left + i * step, baseline - (c / ceiling) * plot_h)
        for i, c in enumerate(counts)
    ]

    line = smooth_path(points, baseline)
    area = f"{line} L {points[-1][0]:.2f},{baseline} L {points[0][0]:.2f},{baseline} Z"

    grid, y_labels = [], []
    for i in range(5):
        value = ceiling * i / 4
        y = baseline - (i / 4) * plot_h
        grid.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" '
            f'stroke="{MUTED}" stroke-width="1" stroke-opacity="0.45"/>'
        )
        y_labels.append(
            f'<text class="axis" x="{left - 12}" y="{y:.2f}" text-anchor="end" '
            f'dominant-baseline="central">{int(value)}</text>'
        )

    x_labels = []
    tick = max(1, len(days) // 8)
    for i in range(0, len(days), tick):
        date = datetime.fromisoformat(days[i]["date"])
        x_labels.append(
            f'<text class="axis" x="{points[i][0]:.2f}" y="{baseline + 22}" '
            f'text-anchor="middle">{date.strftime("%d/%m")}</text>'
        )

    dots = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{TEXT}"/>' for x, y in points
    )

    period = f"{datetime.fromisoformat(days[0]['date']):%d/%m/%Y} - {datetime.fromisoformat(days[-1]['date']):%d/%m/%Y}"

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
     fill="none" xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="Gráfico de atividade de {esc(d['name'])}">
  <title>Gráfico de atividade de {esc(d['name'])}</title>
  <style>
    .title  {{ font: 600 20px {FONT}; fill: {TITLE}; }}
    .period {{ font: 400 13px {FONT}; fill: {TEXT}; }}
    .axis   {{ font: 400 12px {FONT}; fill: {TEXT}; fill-opacity: 0.75; }}
  </style>
  <defs>
    <linearGradient id="area-fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{ICON}" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="{ICON}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{width}" height="{height}" rx="6" fill="{BG}"/>
  <text class="title" x="{left}" y="38">Gráfico de atividade de {esc(d['name'])}</text>
  <text class="period" x="{width - right}" y="38" text-anchor="end">{period}</text>
  {"".join(grid)}
  {"".join(y_labels)}
  <path d="{area}" fill="url(#area-fill)"/>
  <path d="{line}" fill="none" stroke="{TITLE}" stroke-width="2.5"
        stroke-linecap="round" stroke-linejoin="round"/>
  {dots}
  {"".join(x_labels)}
  <text class="axis" x="{left + plot_w / 2}" y="{height - 12}"
        text-anchor="middle">Contribuições por dia</text>
</svg>
"""


# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", default=os.environ.get("GH_LOGIN", ""),
                        help="usuário do GitHub (padrão: $GH_LOGIN)")
    parser.add_argument("--out", default="assets", help="diretório de saída")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN não definido.", file=sys.stderr)
        return 1
    if not args.login:
        print("Informe --login ou defina GH_LOGIN.", file=sys.stderr)
        return 1

    data = collect(args.login, token)
    level, percentile = rank(data)
    print(
        f"{data['login']}: {data['stars']} estrelas, {data['commits']} commits, "
        f"{data['prs']} PRs ({data['merged_pct']:.1f}% mesclados), "
        f"{data['contributions']} contribuições no ano, rank {level} "
        f"(percentil {percentile:.1f}), {len(data['languages'])} linguagens."
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for filename, svg in (
        ("github-stats.svg", render_stats(data)),
        ("top-langs.svg", render_languages(data)),
        ("activity-graph.svg", render_activity(data)),
    ):
        (out / filename).write_text(svg, encoding="utf-8")
        print(f"  gerado {out / filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
