"""
Binance 数据拉取模块
功能：K线数据、历史数据、账户信息
"""

import os
import time
import sqlite3
import requests
import hmac
import hashlib
import yaml
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import pandas as pd

# 从YAML加载配置
_CONFIG_PATH = "/program/stock/config/binance.yaml"

def _load_config():
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)

def _get_config():
    cfg = _load_config()
    return cfg["binance"]

CONFIG = _get_config()

CACHE_DIR = "/program/stock/data/klines"
os.makedirs(CACHE_DIR, exist_ok=True)


def _sign(params: dict) -> str:
    """HMAC SHA256 签名"""
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        CONFIG["secret_key"].encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()


def _public_request(endpoint: str, params: dict = None) -> dict:
    """发送公开请求（无需签名）"""
    url = f"{CONFIG['base_url']}{endpoint}"
    params = params or {}
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        raise Exception(f"Binance API Error {r.status_code}: {r.text}")
    return r.json()


def _sign(params: dict) -> str:
    """HMAC SHA256 签名"""
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        CONFIG["secret_key"].encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()


def _request(method: str, endpoint: str, params: dict = None) -> dict:
    """发送带签名的请求（私有端点）"""
    url = f"{CONFIG['base_url']}{endpoint}"
    headers = {"X-MBX-APIKEY": CONFIG["api_key"]}

    ts = int(time.time() * 1000)
    params = params or {}
    params["timestamp"] = ts
    params["signature"] = _sign(params)

    if method == "GET":
        r = requests.get(url, params=params, headers=headers, timeout=10)
    else:
        r = requests.post(url, params=params, headers=headers, timeout=10)

    if r.status_code != 200:
        raise Exception(f"Binance API Error {r.status_code}: {r.text}")

    return r.json()


def get_klines(symbol: str, interval: str, limit: int = 1000,
                start_time: int = None, end_time: int = None) -> List[dict]:
    """
    获取K线数据
    symbol: BTCUSDT, ETHUSDT
    interval: 1m, 5m, 15m, 1h, 4h, 1d
    limit: 1-1000
    返回: [{"open_time", "open", "high", "low", "close", "volume", ...}]
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time

    data = _public_request("/v3/klines", params)

    # 解析K线数据
    # Binance返回: [open_time, open, high, low, close, volume, close_time, ...]
    result = []
    for k in data:
        result.append({
            "open_time": datetime.fromtimestamp(k[0] / 1000),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": datetime.fromtimestamp(k[6] / 1000),
            "quote_volume": float(k[7]),
            "symbol": symbol,
            "interval": interval,
        })
    return result


def get_account_info() -> dict:
    """获取账户信息（余额）"""
    return _request("GET", "/v3/account")


def get_balance(asset: str = None) -> dict:
    """获取指定资产余额，或全部余额"""
    account = get_account_info()
    balances = account.get("balances", [])
    if asset:
        for b in balances:
            if b["asset"] == asset:
                return {"asset": asset, "free": float(b["free"]), "locked": float(b["locked"])}
        return {"asset": asset, "free": 0, "locked": 0}
    return balances


def get_symbol_ticker(symbol: str) -> dict:
    """获取最新价格"""
    r = requests.get(
        f"{CONFIG['base_url']}/v3/ticker/24hr",
        params={"symbol": symbol},
        timeout=10
    )
    d = r.json()
    return {
        "symbol": d["symbol"],
        "last_price": float(d["lastPrice"]),
        "volume": float(d["volume"]),
        "quote_volume": float(d["quoteVolume"]),
        "price_change_pct": float(d["priceChangePercent"]),
    }


def fetch_and_cache_klines(symbol: str, interval: str,
                           start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    拉取K线数据并缓存到SQLite
    start_date/end_date: "2024-01-01" 格式
    """
    db_path = os.path.join(CACHE_DIR, f"{symbol}_{interval}.db")
    table = f"klines_{symbol.lower()}"

    # 确定时间范围
    end_ts = None
    start_ts = None

    if end_date:
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
    if start_date:
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)

    all_klines = []

    # 分段拉取（每次1000根），从最新往前推
    current_end = end_ts
    iterations = 0

    while True:
        iterations += 1
        if iterations > 100:  # 防止无限循环
            print(f"[BinanceFetcher] Warning: hit 100 iterations, stopping")
            break

        if current_end and start_ts and current_end <= start_ts:
            break

        # 只传 start_time（向前翻页），不用 end_time 避免API冲突
        klines = get_klines(
            symbol, interval,
            limit=1000,
            start_time=current_end
        )
        if not klines:
            break

        all_klines.extend(klines)

        # 获取更早的数据
        earliest_ts = klines[0]["open_time"].timestamp() * 1000
        if start_ts and earliest_ts <= start_ts:
            break

        current_end = int(earliest_ts) - 1

        if len(klines) < 1000:
            break

        time.sleep(0.2)  # 避免频率限制

    if not all_klines:
        print(f"[BinanceFetcher] No data fetched for {symbol} {interval}")
        return pd.DataFrame()

    # 转为DataFrame
    df = pd.DataFrame(all_klines)
    df["date"] = df["open_time"].dt.strftime("%Y-%m-%d")
    df = df[["date", "open", "high", "low", "close", "volume", "quote_volume", "symbol", "interval"]]

    # 存入SQLite
    conn = sqlite3.connect(db_path)
    df.to_sql(table, conn, if_exists="replace", index=False)
    conn.close()

    print(f"[BinanceFetcher] Cached {len(df)} klines for {symbol} {interval} -> {db_path}")
    return df


def load_cached_klines(symbol: str, interval: str) -> pd.DataFrame:
    """从SQLite加载已缓存的K线数据"""
    db_path = os.path.join(CACHE_DIR, f"{symbol}_{interval}.db")
    table = f"klines_{symbol.lower()}"

    if not os.path.exists(db_path):
        return pd.DataFrame()

    conn = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {table} ORDER BY date", conn)
    conn.close()

    # 转换日期格式
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)

    return df


if __name__ == "__main__":
    print("=== Binance Data Fetcher Test ===")

    # 测试1: 获取BTC价格
    print("\n[1] BTCUSDT 24hr ticker:")
    ticker = get_symbol_ticker("BTCUSDT")
    print(f"  Last: ${ticker['last_price']:,.2f}, Vol: {ticker['quote_volume']:,.0f} USDT")

    # 测试2: 拉取最近K线
    print("\n[2] Fetch recent BTCUSDT 1h klines:")
    df = fetch_and_cache_klines("BTCUSDT", "1h")
    if not df.empty:
        print(f"  Got {len(df)} rows, latest: {df.iloc[-1]['date']} close=${df.iloc[-1]['close']}")

    # 测试3: 拉取日线
    print("\n[3] Fetch BTCUSDT 1d klines:")
    df_d = fetch_and_cache_klines("BTCUSDT", "1d", start_date="2024-01-01")
    if not df_d.empty:
        print(f"  Got {len(df_d)} rows, from {df_d.iloc[0]['date']} to {df_d.iloc[-1]['date']}")

    print("\n✅ Binance fetcher test complete")
