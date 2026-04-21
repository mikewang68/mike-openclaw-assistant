"""
Knowledge Pipeline 配置
所有路径和凭证集中在这里
"""

# ===== Obsidian 路径 =====
OBSIDIAN = "/obsidian"
PDF_DIR   = f"{OBSIDIAN}/01_Input/04_PDF"
MD_DIR    = f"{OBSIDIAN}/01_Input/05_PDF2MD"
REVIEW_DIR = f"{OBSIDIAN}/02_Output/04_论文"

# ===== MongoDB =====
MONGO_URI    = "mongodb://stock:681123@192.168.1.2:27017/admin"
MONGO_DB     = "knowledge_graph"

# ===== Neo4j =====
NEO4J_URI      = "bolt://192.168.1.2:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "StrongPassword123"

# ===== MiniMax API（通过 OpenClaw Gateway 调用）=====
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"

# ===== Pipeline 设置 =====
MAX_INFERRED      = 100   # 每次推理最多生成多少条 inferred triple
REASONER_HOPS     = 2     # 推理深度（1=1跳，2=2跳）
REASONER_MIN_CONF = 0.35  # 推理置信度阈值（低于此值的结果被过滤）
