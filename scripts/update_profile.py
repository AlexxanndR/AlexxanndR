#!/usr/bin/env python3
"""
Regenerate the dynamic sections of the profile README from live GitHub data.

Scans all public, non-fork repositories of the owner, aggregates the language
byte counts from the /languages endpoint of each repo, and rewrites the marked
blocks inside README.md:

  <!-- STACK:START -->   ...auto-generated tech stack badges...   <!-- STACK:END -->
  <!-- PROJECTS:START --> ...auto-generated latest projects table... <!-- PROJECTS:END -->

Anything outside the markers is never touched. Stdlib only, no dependencies.

Env vars:
  GITHUB_OWNER  profile owner (default: AlexxanndR)
  GITHUB_TOKEN  token for API auth (optional but recommended; avoids rate limits)
  README_PATH   path to README.md (default: README.md next to repo root)
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

API = "https://api.github.com"
OWNER = os.environ.get("GITHUB_OWNER", "AlexxanndR")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("INPUT_TOKEN")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README_PATH = os.environ.get("README_PATH", os.path.join(HERE, "README.md"))
MAX_PROJECTS = 5

# language -> (devicon/simple-icons slug, brand color). Unknown languages get a plain badge.
LANG_META = {
    "C#": ("csharp", "512BD4"),
    "C": ("c", "A8B9CC"),
    "C++": ("cplusplus", "00599C"),
    "Java": ("java", "ED8B00"),
    "Kotlin": ("kotlin", "7F52FF"),
    "Scala": ("scala", "DC322F"),
    "Python": ("python", "3776AB"),
    "JavaScript": ("javascript", "F7DF1E"),
    "TypeScript": ("typescript", "3178C6"),
    "HTML": ("html5", "E34F26"),
    "CSS": ("css3", "1572B6"),
    "SCSS": ("sass", "CC6699"),
    "PHP": ("php", "777BB4"),
    "Ruby": ("ruby", "CC342D"),
    "Go": ("go", "00ADD8"),
    "Rust": ("rust", "DEA584"),
    "Swift": ("swift", "F05138"),
    "Dart": ("dart", "0175C2"),
    "Shell": ("gnubash", "4EAA25"),
    "PowerShell": ("powershell", "5391FE"),
    "Dockerfile": ("docker", "2496ED"),
    "SQL": ("postgresql", "4169E1"),
    "PLpgSQL": ("postgresql", "336791"),
    "T-SQL": ("microsoftsqlserver", "CC2927"),
    "Vue": ("vuedotjs", "4FC08D"),
    "Svelte": ("svelte", "FF3E00"),
    "QML": ("qt", "41CD52"),
    "Haskell": ("haskell", "5D4F85"),
    "Elixir": ("elixir", "4B275F"),
    "F#": ("fsharp", "378BBA"),
    "R": ("r", "276DC3"),
    "Lua": ("lua", "2C2D72"),
    "Perl": ("perl", "39457E"),
    "Objective-C": ("objectivec", "438EFF"),
    "Assembly": ("assemblyscript", "6E4C13"),
    "Makefile": ("cmake", "6F4E37"),
    "CMake": ("cmake", "064F8C"),
    "Nix": ("nixos", "7EBAE4"),
    "MDX": ("mdx", "F9AC00"),
    "Markdown": None,
    "Text": None,
    "Batchfile": None,
}


def fetch(url: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{OWNER}-profile-readme-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"  ! HTTP {e.code} for {url}", file=sys.stderr)
        return None


def list_repos():
    """All public repos of the owner, newest pushes first. Forks and the
    profile repo itself are excluded."""
    repos, page = [], 1
    while True:
        batch = fetch(f"{API}/users/{OWNER}/repos?per_page=100&sort=pushed&page={page}")
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [
        r for r in repos
        if not r["fork"] and r["name"].lower() != OWNER.lower()
    ]


def aggregate_languages(repos):
    """language -> total bytes across all repos."""
    totals = defaultdict(int)
    for repo in repos:
        langs = fetch(f"{API}/repos/{OWNER}/{repo['name']}/languages") or {}
        for lang, nbytes in langs.items():
            totals[lang] += nbytes
    return totals


def badge(lang: str, pct: float) -> str:
    """An <img> tag, not markdown image syntax: the block lives inside a raw
    HTML <p align="center">, where GitHub does not parse markdown."""
    slug_color = LANG_META.get(lang, "unknown")
    label = urllib.parse.quote(lang.replace(" ", "_"), safe="")
    pct_str = urllib.parse.quote(f"{pct:.1f}%")
    if slug_color in (None, "unknown"):
        url = f"https://img.shields.io/badge/{label}-{pct_str}-555555?style=for-the-badge"
    else:
        slug, color = slug_color
        url = (f"https://img.shields.io/badge/{label}-{pct_str}-{color}"
               f"?style=for-the-badge&logo={slug}&logoColor=white")
    return f'<img src="{url}" alt="{lang} - {pct:.1f}% of code">'


def build_stack_section(lang_totals, repo_count):
    if not lang_totals:
        return "_No languages detected yet — check back after the first push!_"
    total_bytes = sum(lang_totals.values())
    ordered = sorted(lang_totals.items(), key=lambda kv: kv[1], reverse=True)
    top_lang, top_bytes = ordered[0]
    top_pct = 100.0 * top_bytes / total_bytes

    lines = [
        f"<p align=\"center\">🧠 Dominant language: <b>{top_lang}</b> "
        f"({top_pct:.0f}% of {total_bytes / 1e6:.1f} MB across {repo_count} "
        f"{'repo' if repo_count == 1 else 'repos'})</p>",
        "",
        "<p align=\"center\">",
    ]
    for lang, nbytes in ordered:
        pct = 100.0 * nbytes / total_bytes
        if pct < 0.5 and len(lines) > 12:
            continue  # skip trace amounts to keep the row tidy
        lines.append("  " + badge(lang, pct))
    lines.append("</p>")
    return "\n".join(lines)


def build_projects_section(repos):
    if not repos:
        return "_No projects yet._"
    lines = [
        "| Repository | Description | Language | ⭐ |",
        "| --- | --- | :---: | ---: |",
    ]
    for repo in repos[:MAX_PROJECTS]:
        name = repo["name"]
        url = repo["html_url"]
        desc = (repo.get("description") or "—").replace("|", "\\|")
        lang = repo.get("language") or "—"
        stars = repo["stargazers_count"]
        lines.append(f"| [{name}]({url}) | {desc} | `{lang}` | {stars} |")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("")
    lines.append(f"<sub>Last refreshed automatically on {stamp}.</sub>")
    return "\n".join(lines)


def replace_block(readme: str, name: str, body: str) -> str:
    pattern = re.compile(
        rf"(<!--\s*{name}:START\s*-->).*?(<!--\s*{name}:END\s*-->)",
        re.DOTALL,
    )
    replacement = rf"\1\n{body}\n\2"
    new_readme, n = pattern.subn(replacement, readme)
    if n == 0:
        raise SystemExit(f"Marker block {name}:START/{name}:END not found in README.md")
    return new_readme


def main():
    print(f"Fetching repositories for {OWNER} ...")
    repos = list_repos()
    print(f"  {len(repos)} eligible repos (forks and the profile repo excluded)")

    lang_totals = aggregate_languages(repos)
    print(f"  {len(lang_totals)} languages detected: "
          + ", ".join(sorted(lang_totals, key=lang_totals.get, reverse=True)))

    with open(README_PATH, encoding="utf-8") as f:
        readme = f.read()

    readme = replace_block(readme, "STACK", build_stack_section(lang_totals, len(repos)))
    readme = replace_block(readme, "PROJECTS", build_projects_section(repos))

    with open(README_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(readme)
    print(f"README.md updated: {README_PATH}")


if __name__ == "__main__":
    main()
