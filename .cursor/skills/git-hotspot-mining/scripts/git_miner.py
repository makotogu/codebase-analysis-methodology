#!/usr/bin/env python3
"""git_miner.py — T1 git 历史挖掘的参考实现（04-工具箱设计）。

产出三张 CSV：
  hotspot.csv    文件 × 加权变更次数 × LOC × 热点分
  cochange.csv   同 commit 共现的文件对（support / confidence）
  ownership.csv  文件 × 作者分布 × bus factor（知识孤岛）

设计对应方法论中 T1 的三个坑：
  1. 时间衰减（指数衰减，--half-life 天）
  2. 巨型 commit：co-change 中剔除，热点/作者中降权 ×0.2
  3. rename 追踪：解析 numstat 的 "old => new" 记号，历史归并到当前名

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
RENAME_RE = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


def run_git_log(repo: str, months: int) -> str:
    cmd = [
        "git", "-C", repo,
        "-c", "core.quotepath=false",   # 中文/特殊字符路径不转义
        "log", "--numstat", "--no-merges",
        f"--since={months} months ago",
        f"--pretty=format:{COMMIT_MARK}%H|%at|%an",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"git log failed: {out.stderr.strip()}")
    return out.stdout


def resolve_rename(raw_path: str):
    """把 numstat 的 rename 记号解析为 (old, new)。无 rename 时两者相同。"""
    if "=>" not in raw_path:
        return raw_path, raw_path
    if RENAME_RE.search(raw_path):
        old = RENAME_RE.sub(lambda m: m.group(1), raw_path)
        new = RENAME_RE.sub(lambda m: m.group(2), raw_path)
        # 形如 a/{ => sub}/b 会产生双斜杠，清理
        return old.replace("//", "/"), new.replace("//", "/")
    old, new = raw_path.split(" => ", 1)
    return old.strip(), new.strip()


def parse_commits(log_text: str):
    """yield (hash, epoch, author, [paths])，并顺带构建 rename 映射。

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
            chash, epoch_s, author = header.split("|", 2)
            epoch = int(epoch_s)
        except ValueError:
            continue
        paths = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            raw = parts[2]
            old, new = resolve_rename(raw)
            if old != new:
                rename_map[old] = canon(new)
            paths.append(canon(new))
        if paths:
            yield chash, epoch, author, paths


def count_loc(repo: str, path: str) -> int:
    full = os.path.join(repo, path)
    try:
        with open(full, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="T1 git-miner: hotspot / cochange / ownership")
    ap.add_argument("repo", help="git 仓库路径")
    ap.add_argument("--months", type=int, default=12, help="挖掘窗口（月），默认 12")
    ap.add_argument("--half-life", type=float, default=90, help="时间衰减半衰期（天），默认 90")
    ap.add_argument("--mega-threshold", type=int, default=50, help="巨型 commit 文件数阈值，默认 50")
    ap.add_argument("--min-changes", type=int, default=3, help="cochange 参与文件的最小变更次数，默认 3")
    ap.add_argument("--exclude", action="append", default=[], help="排除路径子串，可多次")
    ap.add_argument("--out", default="./t1-output", help="输出目录，默认 ./t1-output")
    args = ap.parse_args()

    now = time.time()
    os.makedirs(args.out, exist_ok=True)

    def excluded(p: str) -> bool:
        return any(s in p for s in args.exclude)

    changes_raw = defaultdict(int)            # path -> 原始变更次数
    changes_decayed = defaultdict(float)      # path -> 衰减加权变更
    author_weight = defaultdict(lambda: defaultdict(float))  # path -> author -> 权重
    pair_decayed = defaultdict(float)         # (a,b) -> 衰减共现
    pair_raw = defaultdict(int)
    n_commits = n_mega = 0

    for chash, epoch, author, paths in parse_commits(run_git_log(args.repo, args.months)):
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
        for p in paths:
            changes_raw[p] += 1
            changes_decayed[p] += w
            author_weight[p][author] += w
        if not is_mega and len(paths) >= 2:
            for a, b in combinations(paths, 2):
                pair_decayed[(a, b)] += w
                pair_raw[(a, b)] += 1

    # ---- hotspot.csv（仅现存文件；分数 = 加权变更 × log2(LOC+1)）----
    hotspot_rows = []
    for p, dec in changes_decayed.items():
        loc = count_loc(args.repo, p)
        if loc == 0:
            continue  # 已删除或二进制
        score = dec * math.log2(loc + 1)
        hotspot_rows.append((p, changes_raw[p], round(dec, 3), loc, round(score, 3)))
    hotspot_rows.sort(key=lambda r: -r[4])
    with open(os.path.join(args.out, "hotspot.csv"), "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["path", "changes_raw", "changes_decayed", "loc", "hotspot_score"])
        wtr.writerows(hotspot_rows)

    # ---- cochange.csv ----
    cochange_rows = []
    for (a, b), dec in pair_decayed.items():
        if changes_raw[a] < args.min_changes or changes_raw[b] < args.min_changes:
            continue
        conf = dec / min(changes_decayed[a], changes_decayed[b])
        cochange_rows.append((a, b, pair_raw[(a, b)], round(dec, 3), round(conf, 3)))
    cochange_rows.sort(key=lambda r: -r[3])
    with open(os.path.join(args.out, "cochange.csv"), "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["path_a", "path_b", "cochange_raw", "cochange_decayed", "confidence"])
        wtr.writerows(cochange_rows[:COCHANGE_TOP_N])

    # ---- ownership.csv（bus factor = 覆盖 50% 权重所需的最少作者数）----
    ownership_rows = []
    for p, authors in author_weight.items():
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
        ownership_rows.append((p, changes_raw[p], len(ranked), main_author,
                               round(main_share, 3), bus, island))
    ownership_rows.sort(key=lambda r: (r[6] != "yes", -r[1]))
    with open(os.path.join(args.out, "ownership.csv"), "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["path", "changes_raw", "n_authors", "main_author",
                      "main_share", "bus_factor", "knowledge_island"])
        wtr.writerows(ownership_rows)

    print(f"commits: {n_commits} (mega: {n_mega})")
    print(f"hotspot.csv:   {len(hotspot_rows)} rows")
    print(f"cochange.csv:  {min(len(cochange_rows), COCHANGE_TOP_N)} rows")
    print(f"ownership.csv: {len(ownership_rows)} rows")
    print(f"output -> {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
