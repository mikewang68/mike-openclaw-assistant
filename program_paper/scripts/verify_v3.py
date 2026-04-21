#!/usr/bin/env python3
"""
Verifier V3 检查脚本（增强版）
用法: python3 verify_v3.py <md_path> <paper_id>
"""
import os, re, sys

def check_v3(md_path, paper_id):
    # V3-1: 存在性
    if not os.path.exists(md_path):
        return "fail", f"[V3-1] 文件不存在: {md_path}"
    
    # V3-2: 目录正确性
    parent = os.path.normpath(os.path.dirname(os.path.abspath(md_path)))
    expected_parent = os.path.normpath("/obsidian/02_Output/04_论文")
    if parent != expected_parent:
        return "fail", f"[V3-2] 目录错误: 期望 {expected_parent}，实际 {parent}"
    
    # V3-3: 文件名格式（空格必须用 _ 替代）
    filename = os.path.basename(md_path)
    # 正确格式：2026-03-21-Paper_Title_With_Underscores-评阅意见.md
    if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}[-_][\w\-]{1,30}-评阅意见\.md$', filename):
        return "fail", f"[V3-3] 文件名不符合规范: {filename}"
    # 检查是否包含空格（空格必须用 _ 替代）
    if ' ' in filename:
        return "fail", f"[V3-3] 文件名包含空格，必须用 _ 替代: {filename}"
    
    # V3-4: 无路径残片
    BAD_PATTERNS = ['_obsidian_', '02_Output_', '04_论文_', '_obsidian']
    for pat in BAD_PATTERNS:
        if pat in filename:
            return "fail", f"[V3-4] 文件名含路径残片 '{pat}': {filename}"
    
    # V3-5~7: 内容检查
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # V3-5: 五部分完整（严格按 paper-prompt.md 格式）
    required_parts = ['## 第一部分', '## 第二部分', '## 第三部分', 
                       '## 第四部分', '## 第五部分']
    for part in required_parts:
        if part not in content:
            return "fail", f"[V3-5] 缺少 {part}"
    
    # V3-6: 模板占位符（排除 LaTeX 数学表达式中的 {{ }}}
    # 匹配 Mustache 风格: {{VAR}}, {{ variable }}, {{关键词}}
    placeholder_pattern = re.compile(r'\{\{[^}]+\}\}')
    # 排除 LaTeX math: $...$, $$...$$, d_{\text{...}}
    # 先移除 LaTeX math 再检查
    content_no_math = re.sub(r'\$[^$]*\$', '', content)  # inline math
    content_no_math = re.sub(r'\$\$[^$]*\$\$', '', content_no_math)  # display math
    # 也移除 \text{...} 等 latex 命令
    content_no_math = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', content_no_math)
    found_placeholders = placeholder_pattern.findall(content_no_math)
    if found_placeholders:
        return "fail", f"[V3-6] 存在未替换的模板占位符: {found_placeholders[:3]}"
    
    # V3-7: 非空文件（质量检查：至少 5KB 才算有实质内容）
    if len(content) < 500:
        return "fail", f"[V3-7] 文件过小 ({len(content)} bytes)"
    if len(content) < 5000:
        return "warn", f"[V3-7] 文件过小 ({len(content)} bytes)，内容可能不完整"
    
    # V3-8: source 字段必须是 PDF 文件名（Obsidian 双链）
    source_match = re.search(r'\*\*source\*\*:\s*\[\[([^\]]+)\]\]', content)
    if not source_match:
        return "fail", f"[V3-8] 缺少 source 字段或格式错误"
    source_value = source_match.group(1)
    if not source_value.endswith('.pdf'):
        return "fail", f"[V3-8] source 必须是 PDF 文件名，实际: {source_value}"
    # source 文件名中空格必须用 _ 替代
    if ' ' in source_value:
        return "fail", f"[V3-8] source 文件名包含空格，必须用 _ 替代: {source_value}"
    
    # V3-9: 禁止第五部分后出现额外 ## 标题
    fifth_part_match = re.search(r'## 第五部分[：:]?\s*[^\n]*\n', content)
    if fifth_part_match:
        after_fifth = content[fifth_part_match.end():]
        # 检查 after_fifth 中是否有独立的 ## 标题行
        forbidden_heading_pattern = re.compile(r'^##\s+\S+', re.MULTILINE)
        found_forbidden = forbidden_heading_pattern.search(after_fifth)
        if found_forbidden:
            return "fail", f"[V3-9] 第五部分后禁止出现 ## 标题，发现: {found_forbidden.group().strip()}"
    
    # V3-10: 内容质量检查 - paper_abstract 不能为 N/A
    abstract_match = re.search(r'\*\*论文摘要\*\*[：:]?\s*N/?A', content)
    if abstract_match:
        return "fail", f"[V3-10] paper_abstract 为 N/A，Reviewer 输出不完整"
    
    # V3-11: 内容质量检查 - 总结不能全为 N/A
    summary_match = re.search(r'\*\*总结\*\*[：:]?\s*N/?A', content)
    if summary_match:
        return "fail", f"[V3-11] summary 为 N/A，Reviewer 输出不完整"
    
    # V3-12: 内容质量检查 - 五维评分依据不能为空
    score_reason_lines = re.findall(r'\|\s*[\u4e00-\u9fff·•\*]+\s*\|', content)
    if len(score_reason_lines) < 4:  # 五维至少4行有中文理由
        # 检查是否有明显敷衍的评分依据（如全为空或只有符号）
        empty_reason_patterns = ['|     |', '|  |', '|      |']
        for pat in empty_reason_patterns:
            if pat in content:
                return "fail", f"[V3-12] 评分依据格式异常，可能未完整填写"
    
    # V3-13: (已移除，允许使用 #### P0/P1/P2 多级编号)
    
    # V3-14: Code Markdown 格式检查 - Part4 必须使用 ### 1. ### 2. 结构
    part4_pos = content.find('## 第四部分')
    if part4_pos != -1:
        part5_pos = content.find('## 第五部分', part4_pos)
        part4_content = content[part4_pos:part5_pos] if part5_pos != -1 else content[part4_pos:]
        # 检查是否有正确的 ### 1. 格式（或 #### P1/P2/P3 格式）
        if not re.search(r'### 1\.|### 2\.|### 3\.|### 4\.|#### P[123]-\d+', part4_content):
            return "fail", "[V3-14] 第四部分必须使用 ### 1. 或 #### P1/P2/P3 结构"
        # 检查是否有禁止的 ### 重构X: 格式（这属于Part5）
        if re.search(r'### 重构\d+:', part4_content):
            return "fail", "[V3-14] 第四部分禁止使用 ### 重构X: 格式"
    
    # V3-15: Code Markdown 格式检查 - Part5 必须使用 ## 第五部分：范式重构
    part5_match = re.search(r'## 第五部分[：:]?\s*范式重构', content)
    if not part5_match:
        return "fail", "[V3-15] 第五部分标题必须为 '## 第五部分：范式重构'"
    
    # V3-16: Code Markdown 格式检查 - Part5 禁止使用 ### 重构X: 格式
    part5_start = content.find('## 第五部分')
    if part5_start != -1:
        after_part5 = content[part5_start:]
        if re.search(r'### 重构\d+:', after_part5):
            return "fail", "[V3-16] 第五部分禁止使用 ### 重构X: 格式，应使用模板规定格式"
    
    # V3-17: Code Markdown 格式检查 - Coach动作前不得有额外前缀
    # 正确的格式：- **Coach 动作**: 内容
    # 错误的格式：- **位置**：... - **问题详情**：... - **Coach 动作**:
    if re.search(r'\*\*位置\*\*[：:]?\s*\n?\s*-\s+\*\*问题详情\*\*', content):
        return "fail", "[V3-17] Coach动作前不得有位置/问题详情等额外字段"
    
    # V3-18: Code Markdown 格式检查 - 禁止在第一部分前添加 Abstract
    first_part_pos = content.find('## 第一部分')
    if first_part_pos > 0:
        before_part1 = content[:first_part_pos]
        if '## Abstract' in before_part1 or '# Abstract' in before_part1:
            return "fail", "[V3-18] 禁止在第一部分前添加 ## Abstract，论文摘感应在第二部分的 **论文摘要** 字段中"
    
    # V3-19: Part2 中文字段检查
    part2_match = re.search(r'## 第二部分[：:]?\s*\n', content)
    if part2_match:
        part3_match = re.search(r'## 第三部分', content)
        part2_content = content[part2_match.end():part3_match.start()] if part3_match else content[part2_match.end():]
        
        # 检查强制中文字段是否包含中文
        chinese_fields = ['聚焦问题', '技术路线', '实验设计', '实验结果', '创新贡献', '不足之处', '总结']
        for field in chinese_fields:
            # 找到字段位置，检查后续内容是否包含中文
            field_match = re.search(rf'\*\*{field}\*\*[：:]?\s*', part2_content)
            if field_match:
                field_content = part2_content[field_match.end():field_match.end()+500]
                # 检查下一个字段前的内容
                next_field_pos = len(field_content)
                for next_field in chinese_fields:
                    pos = field_content.find(f'**{next_field}**')
                    if pos != -1 and pos < next_field_pos:
                        next_field_pos = pos
                field_content = field_content[:next_field_pos].strip()
                # 检查是否包含中文（排除英文标点和空格）
                if field_content and not re.search(r'[\u4e00-\u9fff]', field_content):
                    return "fail", f"[V3-19] Part2 中 **{field}** 必须包含中文内容，当前为纯英文"
    
    return "pass", f"[V3] {paper_id} 检查通过: {filename}"

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python3 verify_v3.py <md_path> <paper_id>")
        sys.exit(1)
    
    md_path = sys.argv[1]
    paper_id = sys.argv[2]
    
    status, message = check_v3(md_path, paper_id)
    print(message)
    
    if status == "fail":
        sys.exit(1)
    else:
        sys.exit(0)
