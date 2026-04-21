#!/usr/bin/env python3
"""V2检查：验证coach JSON格式和内容完整性"""
import json, sys

def check_v2(cj_path):
    errors = []
    with open(cj_path) as f:
        content = f.read()
    try:
        d = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[V2] ❌ JSON格式错误: {e}")
        return False

    p4 = d.get('part4', {})
    p5 = d.get('part5', {})

    # Part4: p0, p1, p2 must have real items
    p0 = p4.get('p0_hard_fixes_impossible_to_accept_without', [])
    p1 = p4.get('p1_logical_enhancements_needed_for_top_tiers', p4.get('p1_logical_enhancements_needed_for_top_tier', []))
    p2 = p4.get('p2_polish_and_formatting_details', [])
    
    if not p0:
        errors.append("part4.p0_hard_fixes_impossible_to_accept_without 为空")
    if not p1:
        errors.append("part4.p1_logical_enhancements_needed_for_top_tiers 为空")
    if not p2:
        errors.append("part4.p2_polish_and_formatting_details 为空")

    # Compliance audit
    ca = p4.get('compliance_audit', {})
    ca_keys = ['author_contribution', 'gdpr_privacy', 'ethical_compliance', 'data_availability', 'code_availability']
    for k in ca_keys:
        v = ca.get(k, '')
        if not v or v == 'N/A' or len(str(v).strip()) < 10:
            errors.append(f"part4.compliance_audit.{k} 为空或N/A")

    # Part5 restructure_items
    items = p5.get('restructure_items', [])
    if not items:
        errors.append("part5.restructure_items 为空")
    else:
        for i, item in enumerate(items):
            if not item.get('restructured_text'):
                errors.append(f"part5.restructure_items[{i}].restructured_text 为空")
            if not item.get('reason') or len(str(item.get('reason', '')).strip()) < 10:
                errors.append(f"part5.restructure_items[{i}].reason 为空或太短")

    if errors:
        print(f"[V2] ❌ 检查失败:")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print(f"[V2] ✅ 检查通过 (p0={len(p0)}项, p1={len(p1)}项, p2={len(p2)}项, restructure={len(items)}项)")
        return True

if __name__ == '__main__':
    ok = check_v2(sys.argv[1])
    sys.exit(0 if ok else 1)
