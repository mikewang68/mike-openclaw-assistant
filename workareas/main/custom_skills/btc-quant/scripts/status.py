#!/usr/bin/env python3
"""
btc_quant_status.py — BTC 量化状态查询工具
"""
from pymongo import MongoClient
import sys

MONGO_URI = "mongodb://stock:681123@192.168.1.2:27017/admin"
DB = "crypto"

client = MongoClient(MONGO_URI)
db = client[DB]

def show_positions():
    positions = list(db.sim_positions.find())
    if not positions:
        print("📊 当前无持仓")
    else:
        for p in positions:
            print(f"📊 持仓: {p.get('direction', '')} | 入场: {p.get('entry_price', '')} | 数量: {p.get('quantity', '')}")

def show_trades(limit=10):
    trades = list(db.sim_trades.find().sort("opened_at", -1).limit(limit))
    print(f"\n📜 最近 {len(trades)} 条交易记录:")
    for t in trades:
        status = "✅ 平仓" if t.get("closed") else "🔒 持仓中"
        pnl = f"盈亏: {t.get('pnl', 0):.4f}" if t.get("pnl") is not None else ""
        print(f"  {status} | {t.get('direction', '')} | 开仓: {t.get('entry_price', '')} | {pnl}")

def show_signals():
    strategies = ["BBRSI_MACD", "RSI_MACD_Crossover", "EMA_Cross", "RSI_Volume"]
    print("\n📡 各策略最近信号:")
    for s in strategies:
        latest = db[s].find_one(sort=[("timestamp", -1)])
        if latest:
            sig = latest.get("signal", "无")
            price = latest.get("close", latest.get("price", ""))
            print(f"  {s}: {sig} @ {price}")
        else:
            print(f"  {s}: 无数据")

def show_news(count=5):
    news = list(db.crypto_news.find().sort("saved_at", -1).limit(count))
    print(f"\n📰 最近 {count} 条舆情:")
    for n in news:
        print(f"  [{n.get('source', '')}] {n.get('title', '')[:50]}")

def main():
    print("=" * 50)
    print("BTC 量化交易状态")
    print("=" * 50)
    show_positions()
    show_trades()
    show_signals()
    show_news()

if __name__ == "__main__":
    main()
