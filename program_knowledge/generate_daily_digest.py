"""
generate_daily_digest.py — 每日综合知识摘要

每天 06:30 pipeline 末尾执行（在 --reasoner 之后）
从4个数据源生成综合摘要，写入 /tmp/kg_digest.json
供 12:00 cron agent 读取并推送 Telegram

4个数据源：
1. 论文KG：knowledge_graph.inferred_triples
2. Follow Builders：knowledge_graph.triples (source='follow_builders')
3. 股票量化：stock.stock_kg + stock.stock_signals
4. AI双向学习：agent_memory.predictions + agent_memory.growth_log

用法：
    python3 generate_daily_digest.py
    python3 generate_daily_digest.py --dry-run
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# ─── MongoDB 连接 ─────────────────────────────────────────
def get_mongo_client():
    from pymongo import MongoClient
    return MongoClient('mongodb://stock:681123@192.168.1.2:27017/admin')

# ─── 数据源1: 论文KG ─────────────────────────────────────
def get_paper_kg_summary():
    """从 knowledge_graph.inferred_triples 获取论文KG统计"""
    try:
        client = get_mongo_client()
        db = client['knowledge_graph']
        
        all_inferred = list(db.inferred_triples.find(
            {"type": "inferred"},
            {"_id": 0, "subject": 1, "relation": 1, "object": 1, "confidence": 1, "rule_tag": 1, "inferred_at": 1}
        ))
        
        total = len(all_inferred)
        
        # 置信度分布
        conf_bins = {"0.9+": 0, "0.8-0.9": 0, "0.7-0.8": 0}
        for t in all_inferred:
            c = t.get("confidence", 0)
            if c >= 0.9: conf_bins["0.9+"] += 1
            elif c >= 0.8: conf_bins["0.8-0.9"] += 1
            elif c >= 0.7: conf_bins["0.7-0.8"] += 1
        
        # 关系分布
        rel_counts = Counter(t.get("relation", "?") for t in all_inferred)
        
        # 今日新增（inferred_at 是 datetime 或 str）
        today_str = datetime.now().strftime("%Y-%m-%d")
        recent = [t for t in all_inferred if today_str in str(t.get("inferred_at", ""))]
        
        # Top5 高置信度
        top5 = sorted(all_inferred, key=lambda x: x.get("confidence", 0), reverse=True)[:5]
        
        client.close()
        return {
            "total": total,
            "recent_count": len(recent),
            "conf_bins": conf_bins,
            "top_relations": dict(rel_counts.most_common(5)),
            "top5": [{"subject": t["subject"], "relation": t["relation"], "object": t["object"], "confidence": round(t.get("confidence", 0), 3)} for t in top5]
        }
    except Exception as e:
        return {"error": str(e), "total": 0, "recent_count": 0}

# ─── 数据源2: Follow Builders ─────────────────────────────
def get_follow_builders_summary():
    """从 knowledge_graph.triples 获取 follow_builders 数据"""
    try:
        client = get_mongo_client()
        db = client['knowledge_graph']
        
        fb_triples = list(db.triples.find(
            {"source": "follow_builders"},
            {"_id": 0, "subject": 1, "relation": 1, "object": 1, "date": 1}
        ).sort("date", -1).limit(50))
        
        total = db.triples.count_documents({"source": "follow_builders"})
        
        # 今日新增
        today_str = datetime.now().strftime("%Y-%m-%d")
        recent = [t for t in fb_triples if today_str in str(t.get("date", ""))]
        
        # 关系分布
        rel_counts = Counter(t.get("relation", "?") for t in fb_triples)
        
        # 最新5条
        latest5 = fb_triples[:5]
        
        client.close()
        return {
            "total": total,
            "recent_count": len(recent),
            "top_relations": dict(rel_counts.most_common(5)),
            "latest5": [{"subject": t["subject"], "relation": t["relation"], "object": t["object"]} for t in latest5]
        }
    except Exception as e:
        return {"error": str(e), "total": 0, "recent_count": 0}

# ─── 数据源3: 股票量化 ────────────────────────────────────
def get_stock_quant_summary():
    """从 stock.stock_kg 和 stock.stock_signals 获取股票KG统计"""
    try:
        client = get_mongo_client()
        db = client['stock']
        
        # KG规模
        kg_total = db.stock_kg.count_documents({})
        
        # 今日信号（从stock_signals读取，_id就是日期）
        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        today_doc = db.stock_signals.find_one({"_id": today_str})
        if not today_doc:
            today_doc = db.stock_signals.find_one({"_id": yesterday_str})
        
        recent_signals_count = 0
        top5_today = []
        hot_concepts = []
        if today_doc:
            buy = today_doc.get("buy_signals", [])
            recent_signals_count = len(buy)
            top5_today = [
                {"code": s["code"], "name": s["name"], "score": s.get("score", 0), 
                 "signal": s.get("signal", ""), "timing": s.get("timing_signal", ""),
                 "industry": s.get("industry", "")}
                for s in buy[:5]
            ]
            hot_concepts = today_doc.get("hot_concepts", [])[:10]
        
        # 本周活跃信号
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_docs = list(db.stock_signals.find(
            {"_id": {"$gte": week_ago}},
            {"buy_signals": 1, "_id": 1}
        ))
        week_signals = []
        for doc in week_docs:
            for s in doc.get("buy_signals", []):
                week_signals.append({"code": s["code"], "name": s["name"], "score": s.get("score", 0), "date": doc["_id"]})
        week_signals.sort(key=lambda x: x["score"], reverse=True)
        week_top5 = week_signals[:5]
        
        # 概念统计
        hot_concepts_data = list(db.stock_concepts.find(
            {"hot_rank": {"$gt": 0, "$lte": 50}},
            {"_id": 1, "hot_rank": 1, "stock_count": 1, "category": 1}
        ).sort("hot_rank", 1).limit(10))
        hot_concepts_list = [{"concept": c["_id"], "rank": c.get("hot_rank", 0), "category": c.get("category", "")} for c in hot_concepts_data]
        
        client.close()
        return {
            "kg_total": kg_total,
            "recent_signals_count": recent_signals_count,
            "top5_today": top5_today,
            "week_top5": week_top5,
            "hot_concepts": hot_concepts_list if hot_concepts_list else [{"concept": c["concept"], "rank": i+1} for i, c in enumerate(hot_concepts[:5])]
        }
    except Exception as e:
        return {"error": str(e), "kg_total": 0}

# ─── 数据源4: AI双向学习 ──────────────────────────────────
def get_agent_learning_summary():
    """从 agent_memory.predictions 获取AI自我学习统计（单文档结构）"""
    try:
        client = get_mongo_client()
        db = client['agent_memory']
        
        # predictions 集合只有一个文档 _id='mike-predictions'
        pred_doc = db.predictions.find_one({"_id": "mike-predictions"})
        
        if not pred_doc:
            return {"prediction_count": 0, "accuracy": {}, "recent_predictions": []}
        
        patterns = pred_doc.get("patterns", {})
        history = pred_doc.get("history", {})
        
        # 从 patterns 提取准确率（computed completion_rate）
        accuracy = {}
        for k, v in patterns.items():
            if isinstance(v, dict) and 'completion_rate' in v:
                accuracy[k] = {
                    "rate": round(v['completion_rate'], 2),
                    "description": v.get('description', '')
                }
        
        # 从 history 提取最近记录
        recent_pred_texts = []
        for cat, entries in history.items():
            for e in entries[-3:]:  # 每类取最近3条
                correct_mark = '✅' if e.get('correct') == True else ('❌' if e.get('correct') == False else '⏳')
                ts = str(e.get('recorded_at', ''))[:16]
                recent_pred_texts.append(f"{cat}: {correct_mark} {e.get('predicted','')[:30]} ({ts})")
        
        # growth_log
        growth_entries = list(db.growth_log.find(
            {}, {"_id": 0, "type": 1, "description": 1, "recorded_at": 1}
        ).sort("recorded_at", -1).limit(5))
        
        # persona (_id='mike')
        persona = db.persona.find_one({"_id": "mike"}, {"_id": 0, "updated_at": 1, "expertise": 1, "goals": 1})
        
        # 统计总预测次数
        total_preds = sum(len(entries) for entries in history.values())
        
        client.close()
        return {
            "prediction_count": total_preds,
            "accuracy": accuracy,
            "recent_predictions": recent_pred_texts[-10:],  # 最近10条
            "growth_entries_count": len(growth_entries),
            "recent_growth": [f"[{g.get('type','')}] {g.get('description','')[:50]}" for g in growth_entries[:3]],
            "persona_updated": str(persona.get("updated_at", "unknown"))[:10] if persona else "unknown",
            "expertise_summary": list(persona.get("expertise", {}).keys()) if persona else []
        }
    except Exception as e:
        return {"error": str(e), "prediction_count": 0}


# ─── JSON保存 ─────────────────────────────────────────────
def save_json_report(data: dict, path: str = "/tmp/kg_digest.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"📄 JSON 报告已保存: {path}")
    return path


# ─── 主流程 ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="每日综合知识摘要生成")
    parser.add_argument("--dry-run", action="store_true", help="不推送，只打印")
    args = parser.parse_args()

    print(f"\n🧠 每日综合知识摘要 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. 收集4个数据源
    print("\n📊 收集数据...")

    print("  [1/4] 论文KG (knowledge_graph.inferred_triples)...")
    paper_kg = get_paper_kg_summary()
    print(f"       推理三元组: {paper_kg.get('total', 0)} | 今日新增: {paper_kg.get('recent_count', 0)}")
    if paper_kg.get('top5'):
        for t in paper_kg['top5'][:2]:
            print(f"       - {t['subject']} --{t['relation']}--> {t['object']} (conf={t['confidence']})")

    print("  [2/4] Follow Builders (knowledge_graph.triples, source=follow_builders)...")
    fb = get_follow_builders_summary()
    print(f"       总三元组: {fb.get('total', 0)} | 今日新增: {fb.get('recent_count', 0)}")
    if fb.get('latest5'):
        for t in fb['latest5'][:2]:
            print(f"       - {t['subject']} --{t['relation']}--> {t['object']}")

    print("  [3/4] 股票量化 (stock.stock_kg + stock.stock_signals)...")
    stock = get_stock_quant_summary()
    print(f"       KG规模: {stock.get('kg_total', 0)} | 今日信号: {stock.get('recent_signals_count', 0)}")
    if stock.get('top5_today'):
        for s in stock['top5_today'][:3]:
            print(f"       - {s['code']} {s['name']} 评分:{s['score']} 状态:{s.get('timing','')}")

    print("  [4/4] AI双向学习 (agent_memory.predictions + growth_log)...")
    agent = get_agent_learning_summary()
    print(f"       预测记录: {agent.get('prediction_count', 0)}")
    if agent.get('accuracy'):
        for cat, a in agent['accuracy'].items():
            print(f"       - {cat}: {a['rate']*100:.0f}%")

    # 2. 保存JSON（供12:00 cron agent读取）
    json_data = {
        "generated_at": datetime.now().isoformat(),
        "paper_kg": paper_kg,
        "follow_builders": fb,
        "stock_quant": stock,
        "agent_learning": agent,
    }
    json_path = save_json_report(json_data)

    print(f"\n✅ 每日综合摘要完成（JSON: {json_path}）")
    print("   12:00 cron agent 将读取此文件生成 Telegram 摘要")


if __name__ == "__main__":
    main()