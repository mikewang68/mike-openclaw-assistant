#!/usr/bin/env python3
"""V3 Verification Script"""
import os

OUTPUT_DIR = "/obsidian/02_Output/04_论文"
EXPECTED_DIR = "/obsidian/02_Output/04_论文"

files = [
    "2026-03-24-Hardness_of_High-Dimensional_L-评阅意见.md",
    "2026-03-24-A_Novel_Method_for_Enforcing_E-评阅意见.md",
    "2026-03-24-Autoregressive_vs._Masked_Diff-评阅意见.md",
    "2026-03-24-Greater_accessibility_can_ampl-评阅意见.md",
]

all_pass = True
for fname in files:
    fpath = os.path.join(OUTPUT_DIR, fname)
    print(f"\n{'='*60}")
    print(f"V3 Check: {fname}")
    print('='*60)

    # V3-1: File exists
    v3_1 = os.path.exists(fpath)
    print(f"  V3-1 (exists): {'PASS' if v3_1 else 'FAIL'}")

    # V3-2: Directory correct
    actual_dir = os.path.dirname(fpath)
    v3_2 = actual_dir == EXPECTED_DIR
    print(f"  V3-2 (dir={EXPECTED_DIR}): {'PASS' if v3_2 else 'FAIL'}")

    # V3-3: Filename format
    v3_3 = fname.startswith("2026-03-24-") and fname.endswith("-评阅意见.md")
    print(f"  V3-3 (filename format): {'PASS' if v3_3 else 'FAIL'}")

    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()

    # V3-4: No path fragments
    v3_4 = "_obsidian_" not in content
    print(f"  V3-4 (no _obsidian_): {'PASS' if v3_4 else 'FAIL'}")

    # V3-5: Five parts complete
    has_p1 = "## 第一部分" in content
    has_p2 = "## 第二部分" in content
    has_p3 = "## 第三部分" in content
    has_p4 = "## 第四部分" in content
    has_p5 = "## 第五部分" in content
    v3_5 = has_p1 and has_p2 and has_p3 and has_p4 and has_p5
    print(f"  V3-5 (5 parts): P1={has_p1} P2={has_p2} P3={has_p3} P4={has_p4} P5={has_p5} -> {'PASS' if v3_5 else 'FAIL'}")

    # V3-6: No template placeholders
    v3_6 = "{{" not in content and "}}" not in content
    print(f"  V3-6 (no placeholders): {'PASS' if v3_6 else 'FAIL'}")

    # V3-7: Non-empty file > 500 bytes
    size = os.path.getsize(fpath)
    v3_7 = size > 500
    print(f"  V3-7 (size>{500}): {size} bytes -> {'PASS' if v3_7 else 'FAIL'}")

    # V3-8: source field with PDF filename wiki link
    import re
    source_matches = re.findall(r'\*\*source\*\*:\s*\[\[([^\]]+)\]\]', content)
    v3_8 = len(source_matches) >= 1
    print(f"  V3-8 (source wiki link): {source_matches} -> {'PASS' if v3_8 else 'FAIL'}")

    # V3-9: No prohibited headers after Part 5
    p5_pos = content.find("## 第五部分")
    if p5_pos >= 0:
        after_p5 = content[p5_pos + len("## 第五部分"):]
        # Check for "##" headers (section level) after part 5
        lines_after = after_p5.split('\n')
        prohibited = [l for l in lines_after if l.startswith('## ') and not l.startswith('## 第五部分')]
        v3_9 = len(prohibited) == 0
    else:
        v3_9 = False
    print(f"  V3-9 (no extra headers after part5): {'PASS' if v3_9 else 'FAIL'}")

    passed = v3_1 and v3_2 and v3_3 and v3_4 and v3_5 and v3_6 and v3_7 and v3_8 and v3_9
    print(f"\n  OVERALL: {'✅ PASS' if passed else '❌ FAIL'}")
    if not passed:
        all_pass = False

print(f"\n{'='*60}")
print(f"ALL V3 CHECKS: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")
