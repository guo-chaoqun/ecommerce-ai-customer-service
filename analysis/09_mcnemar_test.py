#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
McNemar 配对显著性检验：V2 复评基线（v4.4 裁判） vs V3 全量优化版

为什么用 McNemar 而不是普通比例 z 检验？
----------------------------------------
两次评测跑的是同一批 505 条用例，两组结果是「配对」的：
- 两版都答对 / 都答错的用例，对「有没有差异」没有信息量；
- 真正有信息量的是「翻案」的用例：
    b = V2 答错、V3 答对（改对了）
    c = V2 答对、V3 答错（改坏了，回归）
  在「改动无效」的零假设下，b 和 c 应各占一半（翻案方向随机）。
  b 明显大于 c，才说明优化真实有效。
- 普通 z 检验假设两批样本独立，用在配对数据上会得出错误结论
  （本数据用 z 检验会误得 p=0.56）。

检验方法：对不一致对数 n=b+c 做精确二项检验（Binomial Test），
零假设下 min(b,c) ~ Binomial(n, 0.5)，报告双侧 p 值。

数据（仓库内相对路径，从仓库根目录运行）：
  V2 复评：eval_runs/baseline_v2_v44/baseline_v2_v44.json
  V3 全量：eval_runs/baseline_v3_full/eval_results_baseline_v3_full_20260817_150803.json

用法：
  python analysis/09_mcnemar_test.py
  python analysis/09_mcnemar_test.py <v2_v44.json> <v3.json>
"""
import sys
import json
from pathlib import Path

PASS_THRESHOLD = 4  # 与两版官方通过口径一致：branch_routing >= 4 记为通过


def load_records(path: Path):
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    return {r["case_id"]: r for r in records}


def is_passed(record) -> bool:
    return record["score"]["branch_routing"] >= PASS_THRESHOLD


def exact_mcnemar_p(b: int, c: int) -> float:
    """精确双侧 p 值；优先 scipy，无 scipy 时用纯 Python 二项系数手算。"""
    n = b + c
    k = min(b, c)
    try:
        from scipy.stats import binomtest
        return float(binomtest(k, n, 0.5, alternative="two-sided").pvalue)
    except ImportError:
        from math import comb
        # P(X<=k) 单侧，双侧 = 2 * 单侧（对称分布）
        one_sided = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
        return min(1.0, 2 * one_sided)


def main():
    root = Path(__file__).resolve().parent.parent
    v2_path = Path(sys.argv[1]) if len(sys.argv) > 2 else root / "eval_runs/baseline_v2_v44/baseline_v2_v44.json"
    v3_path = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "eval_runs/baseline_v3_full/eval_results_baseline_v3_full_20260817_150803.json"

    v2 = load_records(v2_path)
    v3 = load_records(v3_path)
    common = sorted(set(v2) & set(v3))
    if len(common) != 505:
        print(f"[警告] 配对 case_id 数为 {len(common)}（预期 505），请检查数据文件")

    # 2x2 配对表（行=V2，列=V3）
    a = b = c_ = d = 0  # a:都过  b:V2败V3过  c:V2过V3败  d:都败
    for cid in common:
        p2, p3 = is_passed(v2[cid]), is_passed(v3[cid])
        if p2 and p3:
            a += 1
        elif (not p2) and p3:
            b += 1
        elif p2 and (not p3):
            c_ += 1
        else:
            d += 1

    v2_pass, v3_pass = a + c_, a + b
    p_value = exact_mcnemar_p(b, c_)

    print("=" * 56)
    print("McNemar 配对检验：V2 复评基线(v4.4) vs V3 优化版")
    print("=" * 56)
    print(f"配对用例数 n = {len(common)}")
    print(f"通过口径：score.branch_routing >= {PASS_THRESHOLD}")
    print("-" * 56)
    print("配对表（行=V2，列=V3）")
    print(f"  两版都通过 a = {a}")
    print(f"  V2败→V3过 b = {b}   (优化改对)")
    print(f"  V2过→V3败 c = {c_}   (回归改坏)")
    print(f"  两版都失败 d = {d}")
    print("-" * 56)
    print(f"V2 复评通过率：{v2_pass}/{len(common)} = {v2_pass/len(common):.3f}")
    print(f"V3 全量通过率：{v3_pass}/{len(common)} = {v3_pass/len(common):.3f}")
    print(f"不一致对数 n' = b + c = {b + c_}")
    print(f"McNemar 精确双侧 p = {p_value:.4f}")
    print("-" * 56)
    verdict = "统计显著（p<0.05），提升不是随机波动" if p_value < 0.05 else "不显著，无法排除随机波动"
    print(f"结论：{verdict}")
    print("=" * 56)


if __name__ == "__main__":
    main()
