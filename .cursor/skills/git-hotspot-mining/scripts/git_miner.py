#!/usr/bin/env python3
"""git_miner.py — T1 git 历史挖掘的参考实现（04-工具箱设计 / 09-多仓库联合分析）。

单仓库模式（传 1 个路径），每仓库产出三张 CSV：
  hotspot.csv    文件 × 加权变更次数 × LOC × 热点分
  cochange.csv   同 commit 共现的文件对（support / confidence）
  ownership.csv  文件 × 作者分布 × bus factor（知识孤岛）

多仓库工作区模式（传 ≥2 个路径），额外产出跨仓库 co-change：
  cross-cochange-files.csv  按工单 ID 聚合的跨仓库文件对
  cross-cochange-repos.csv  仓库级耦合（via=ticket / window）

设计对应方法论中 T1 的三个坑：
  1. 时间衰减（指数衰减，--half-life 天）
  2. 巨型 commit：co-change 中剔除，热点/作者中降权 ×0.2
  3. rename 追踪：解析 numstat 的 "old => new" 记号，历史归并到当前名

跨仓库 co-change 的两条路径（09 篇第三节）：
  工单 ID（--ticket-regex，首选）> 同作者时间窗（--window-hours，兜底，仅仓库级）

零第三方依赖，Python 3.8+。
"""

import argparse
import csv
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from itertools import combinations

COMMIT_MARK = "@@@COMMIT@@@"
MEGA_DAMPING = 0.2          # 巨型 commit 在热点/作者统计中的降权系数
COCHANGE_TOP_N = 500        # cochange.csv 最多输出的文件对数
CROSS_FILES_TOP_N = 300     # 跨仓库文件对最多输出数
RENAME_RE = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


# ---------------------------------------------------------------- git 解析

def run_git_log(repo: str, months: int) -> str:
    cmd = [
        "git", "-C", repo,
        "-c", "core.quotepath=false",   # 中文/特殊字符路径不转义
        "log", "--numstat", "--no-merges",
        f"--since={months} months ago",
        f"--pretty=format:{COMMIT_MARK}%H|%at|%an|%s",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"git log failed in {repo}: {out.stderr.strip()}")
    return out.stdout


def resolve_rename(raw_path: str):
    """把 numstat 的 rename 记号解析为 (old, new)。无 rename 时两者相同。"""
    if "=>" not in raw_path:
        return raw_path, raw_path
    if RENAME_RE.search(raw_path):
        old = RENAME_RE.sub(lambda m: m.group(1), raw_path)
        new = RENAME_RE.sub(lambda m: m.group(2), raw_path)
        return old.replace("//", "/"), new.replace("//", "/")
    old, new = raw_path.split(" => ", 1)
    return old.strip(), new.strip()


def parse_commits(log_text: str):
    """yield (hash, epoch, author, subject, [paths])。

    git log 默认新→旧。遇到 rename 记录 old -> canonical(new)，
    这样更旧的 commit 里出现的 old 路径会被归并到当前名。
    """
    rename_map = {}

    def canon(p: str) -> str:
        seen = set()
        while p in rename_map and p not in seen:
            seen.add(p)
            p = rename_map[p]
        return p

    for block in log_text.split(COMMIT_MARK):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        header = lines[0]
        try:
            chash, epoch_s, author, subject = header.split("|", 3)
            epoch = int(epoch_s)
        except ValueError:
            continue
        paths = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            old, new = resolve_rename(parts[2])
            if old != new:
                rename_map[old] = canon(new)
            paths.append(canon(new))
        if paths:
            yield chash, epoch, author, subject, paths


def count_loc(repo: str, path: str) -> int:
    try:
        with open(os.path.join(repo, path), "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


# ---------------------------------------------------------------- 单仓库挖掘

def mine_repo(repo: str, args, now: float):
    """返回 (stats dict, commits list)。commits 供跨仓库分析复用。"""
    changes_raw = defaultdict(int)
    changes_decayed = defaultdict(float)
    author_weight = defaultdict(lambda: defaultdict(float))
    pair_decayed = defaultdict(float)
    pair_raw = defaultdict(int)
    commits = []
    n_commits = n_mega = 0

    def excluded(p: str) -> bool:
        return any(s in p for s in args.exclude)

    for chash, epoch, author, subject, paths in parse_commits(run_git_log(repo, args.months)):
        paths = sorted({p for p in paths if not excluded(p)})
        if not paths:
            continue
        n_commits += 1
        age_days = max(0.0, (now - epoch) / 86400)
        w = 0.5 ** (age_days / args.half_life)
        is_mega = len(paths) > args.mega_threshold
        if is_mega:
            n_mega += 1
            w *= MEGA_DAMPING
        commits.append((chash, epoch, author, subject, paths, is_mega, w))
        for p in paths:
            changes_raw[p] += 1
            changes_decayed[p] += w
            author_weight[p][author] += w
        if not is_mega and len(paths) >= 2:
            for a, b in combinations(paths, 2):
                pair_decayed[(a, b)] += w
                pair_raw[(a, b)] += 1

    return {
        "changes_raw": changes_raw,
        "changes_decayed": changes_decayed,
        "author_weight": author_weight,
        "pair_decayed": pair_decayed,
        "pair_raw": pair_raw,
        "n_commits": n_commits,
        "n_mega": n_mega,
    }, commits


def write_repo_csvs(repo: str, out_dir: str, s: dict, args) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # hotspot.csv（仅现存文件；分数 = 加权变更 × log2(LOC+1)）
    rows = []
    for p, dec in s["changes_decayed"].items():
        loc = count_loc(repo, p)
        if loc == 0:
            continue  # 已删除或二进制
        rows.append((p, s["changes_raw"][p], round(dec, 3), loc,
                     round(dec * math.log2(loc + 1), 3)))
    rows.sort(key=lambda r: -r[4])
    with open(os.path.join(out_dir, "hotspot.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "changes_raw", "changes_decayed", "loc", "hotspot_score"])
        w.writerows(rows)

    # cochange.csv
    co = []
    for (a, b), dec in s["pair_decayed"].items():
        if s["changes_raw"][a] < args.min_changes or s["changes_raw"][b] < args.min_changes:
            continue
        conf = dec / min(s["changes_decayed"][a], s["changes_decayed"][b])
        co.append((a, b, s["pair_raw"][(a, b)], round(dec, 3), round(conf, 3)))
    co.sort(key=lambda r: -r[3])
    with open(os.path.join(out_dir, "cochange.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path_a", "path_b", "cochange_raw", "cochange_decayed", "confidence"])
        w.writerows(co[:COCHANGE_TOP_N])

    # ownership.csv（bus factor = 覆盖 50% 权重所需的最少作者数）
    own = []
    for p, authors in s["author_weight"].items():
        total = sum(authors.values())
        if total <= 0:
            continue
        ranked = sorted(authors.items(), key=lambda kv: -kv[1])
        main_author, main_w = ranked[0]
        cum, bus = 0.0, 0
        for _, aw in ranked:
            cum += aw
            bus += 1
            if cum >= total * 0.5:
                break
        main_share = main_w / total
        island = "yes" if (main_share >= 0.8 and len(ranked) <= 2) else "no"
        own.append((p, s["changes_raw"][p], len(ranked), main_author,
                    round(main_share, 3), bus, island))
    own.sort(key=lambda r: (r[6] != "yes", -r[1]))
    with open(os.path.join(out_dir, "ownership.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "changes_raw", "n_authors", "main_author",
                    "main_share", "bus_factor", "knowledge_island"])
        w.writerows(own)


# ---------------------------------------------------------------- 跨仓库

def cross_repo_analysis(all_commits: dict, args, out_dir: str) -> None:
    """all_commits: repo_name -> [(hash, epoch, author, subject, paths, is_mega, w)]"""
    ticket_re = re.compile(args.ticket_regex) if args.ticket_regex else None

    # ---- 路径一：工单 ID 聚合 ----
    # ticket -> repo -> (files set, best_weight)
    ticket_repo_files = defaultdict(lambda: defaultdict(set))
    ticket_weight = defaultdict(float)
    if ticket_re:
        for repo, commits in all_commits.items():
            for _, _, _, subject, paths, is_mega, w in commits:
                if is_mega:
                    continue
                for t in set(ticket_re.findall(subject)):
                    ticket_repo_files[t][repo].update(paths)
                    ticket_weight[t] = max(ticket_weight[t], w)

    file_pairs = defaultdict(lambda: [0, 0.0, []])   # (ra,pa,rb,pb) -> [tickets, decayed, examples]
    repo_pairs = defaultdict(lambda: [0.0, 0, []])   # (ra,rb,via) -> [weight, count, examples]

    for t, repo_files in ticket_repo_files.items():
        repos = sorted(repo_files)
        if len(repos) < 2:
            continue
        tw = ticket_weight[t]
        for ra, rb in combinations(repos, 2):
            key = (ra, rb, "ticket")
            repo_pairs[key][0] += tw
            repo_pairs[key][1] += 1
            if len(repo_pairs[key][2]) < 5:
                repo_pairs[key][2].append(t)
            for pa in repo_files[ra]:
                for pb in repo_files[rb]:
                    fk = (ra, pa, rb, pb)
                    file_pairs[fk][0] += 1
                    file_pairs[fk][1] += tw
                    if len(file_pairs[fk][2]) < 3:
                        file_pairs[fk][2].append(t)

    # ---- 路径二：同作者时间窗（仅仓库级，兜底信号）----
    if args.window_hours > 0:
        window = args.window_hours * 3600
        by_author = defaultdict(list)   # author -> [(epoch, repo, w)]
        for repo, commits in all_commits.items():
            for _, epoch, author, _, _, is_mega, w in commits:
                if not is_mega:
                    by_author[author].append((epoch, repo, w))
        for author, entries in by_author.items():
            entries.sort()
            for i, (e1, r1, w1) in enumerate(entries):
                for e2, r2, w2 in entries[i + 1:]:
                    if e2 - e1 > window:
                        break
                    if r1 == r2:
                        continue
                    ra, rb = sorted((r1, r2))
                    key = (ra, rb, "window")
                    repo_pairs[key][0] += min(w1, w2)
                    repo_pairs[key][1] += 1
                    if len(repo_pairs[key][2]) < 5 and author not in repo_pairs[key][2]:
                        repo_pairs[key][2].append(author)

    # ---- 输出 ----
    frows = [(ra, pa, rb, pb, c, round(d, 3), ";".join(ex))
             for (ra, pa, rb, pb), (c, d, ex) in file_pairs.items()]
    frows.sort(key=lambda r: -r[5])
    with open(os.path.join(out_dir, "cross-cochange-files.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo_a", "path_a", "repo_b", "path_b",
                    "shared_tickets", "weight_decayed", "example_tickets"])
        w.writerows(frows[:CROSS_FILES_TOP_N])

    rrows = [(ra, rb, via, c, round(wt, 3), ";".join(ex))
             for (ra, rb, via), (wt, c, ex) in repo_pairs.items()]
    rrows.sort(key=lambda r: -r[4])
    with open(os.path.join(out_dir, "cross-cochange-repos.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo_a", "repo_b", "via", "pair_count",
                    "weight_decayed", "examples"])
        w.writerows(rrows)

    print(f"cross-cochange-files.csv: {min(len(frows), CROSS_FILES_TOP_N)} rows")
    print(f"cross-cochange-repos.csv: {len(rrows)} rows")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(
        description="T1 git-miner: hotspot / cochange / ownership (+ cross-repo cochange)")
    ap.add_argument("repos", nargs="+", help="git 仓库路径，可传多个（工作区模式）")
    ap.add_argument("--months", type=int, default=12, help="挖掘窗口（月），默认 12")
    ap.add_argument("--half-life", type=float, default=90, help="时间衰减半衰期（天），默认 90")
    ap.add_argument("--mega-threshold", type=int, default=50, help="巨型 commit 文件数阈值，默认 50")
    ap.add_argument("--min-changes", type=int, default=3, help="cochange 参与文件的最小变更次数，默认 3")
    ap.add_argument("--exclude", action="append", default=[], help="排除路径子串，可多次")
    ap.add_argument("--ticket-regex", default=r"[A-Z]{2,10}-\d+",
                    help=r"从 commit message 提取工单 ID 的正则，默认 [A-Z]{2,10}-\d+；传空串禁用")
    ap.add_argument("--window-hours", type=float, default=0,
                    help="同作者时间窗（小时），跨仓库兜底信号，默认 0=关闭")
    ap.add_argument("--out", default="./t1-output", help="输出目录，默认 ./t1-output")
    args = ap.parse_args()

    now = time.time()
    os.makedirs(args.out, exist_ok=True)
    multi = len(args.repos) > 1

    all_commits = {}
    for repo in args.repos:
        name = os.path.basename(os.path.abspath(repo.rstrip("/")))
        stats, commits = mine_repo(repo, args, now)
        all_commits[name] = commits
        out_dir = os.path.join(args.out, name) if multi else args.out
        write_repo_csvs(repo, out_dir, stats, args)
        print(f"[{name}] commits: {stats['n_commits']} (mega: {stats['n_mega']}) -> {out_dir}")

    if multi:
        cross_repo_analysis(all_commits, args, args.out)

    print(f"output -> {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
