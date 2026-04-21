#!/usr/bin/env python3
"""V1检查：验证reviewer JSON格式和内容完整性（对齐reviewer-prompt.md）"""
import json, sys, re

def check_v1(rj_path):
    errors = []
    with open(rj_path) as f:
        content = f.read()
    try:
        d = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[V1] ❌ JSON格式错误: {e}")
        return False

    # Check part1
    p1 = d.get('part1', {})
    required_p1 = ['tags', 'title', 'keywords', 'date', 'source']
    for k in required_p1:
        if not p1.get(k):
            errors.append(f"part1.{k} 为空")

    # Check part2
    p2 = d.get('part2', {})
    if not p2.get('scores') or not isinstance(p2['scores'], list):
        errors.append("part2.scores 为空或非数组")

    scores = p2.get('scores', [])
    if scores:
        last_score = scores[-1]
        # total_score: 优先用 total_score，否则从 percentage 字段提取
        total = last_score.get('total_score')
        if total is None:
            pct = last_score.get('percentage', '')
            if isinstance(pct, str) and pct.endswith('%'):
                total = int(re.sub(r'[^\d]', '', pct))
            else:
                total = None
        if total is None:
            errors.append("part2.scores[-1] 缺少 total_score 或 percentage 字段")
        else:
            if not (0 <= total <= 100):
                errors.append(f"part2.scores[-1].total_score = {total}，超出0-100范围")
    
    # overall_quality must match scores[-1].level
    oq = p2.get('overall_quality', '')
    if scores and 'level' in scores[-1]:
        if oq != scores[-1]['level']:
            errors.append(f"part2.overall_quality='{oq}' 与 scores[-1].level='{scores[-1]['level']}' 不一致")

    # suitable_journal_level must contain total_score
    sjl = p2.get('suitable_journal_level', '')
    if scores:
        ts = total if total is not None else 0
        if str(ts) not in sjl:
            errors.append(f"part2.suitable_journal_level='{sjl}' 中未包含 total_score={ts}")

    # innovation_contributions and weaknesses must be non-empty arrays
    for field in ['innovation_contributions', 'weaknesses']:
        v = p2.get(field)
        if not v or not isinstance(v, list) or len(v) == 0:
            errors.append(f"part2.{field} 为空或非数组")

    # Part3 must have real content (not N/A, min 10 chars)
    p3 = d.get('part3', {})
    part3_keys = ['domain_criteria_match', 'logic_chain_audit', 'interpretability_transparency',
                  'experimental_efficiency_stress_test', 'generalization_robustness']
    for k in part3_keys:
        v = p3.get(k, '')
        if not v or v == 'N/A' or len(str(v).strip()) < 10:
            errors.append(f"part3.{k} 为空或N/A: {str(v)[:50]}")

    if errors:
        print(f"[V1] ❌ 检查失败:")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print(f"[V1] ✅ 检查通过 (总分 {total})")
        return True

if __name__ == '__main__':
    ok = check_v1(sys.argv[1])
    sys.exit(0 if ok else 1)
