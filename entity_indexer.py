"""
实体索引构建器 — 从 Neo4j 读取所有实体，向量化后存入 ChromaDB
使用方式: python entity_indexer.py（首次运行或图谱更新后运行一次）
"""
import os
import sys
import chromadb
from chromadb.utils import embedding_functions
from neo4j import GraphDatabase

sys.stdout.reconfigure(encoding='utf-8')

# ── 配置 ──────────────────────────────────────────
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "cjy00916"
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "medical_entities"

# 中文语义向量模型（专为中文优化，轻量高效）
EMBEDDING_MODEL = "shibing624/text2vec-base-chinese"


def build_index():
    """主流程：Neo4j 导出 → 向量化 → 存入 ChromaDB"""

    # ── 1. 连接 Neo4j，读取所有实体 ──
    print("[1/4] 连接 Neo4j...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    documents = []    # 向量化的文本
    metadatas = []    # 实体类型标签
    ids = []          # 唯一 ID

    with driver.session() as session:
        # 读取所有节点及其标签
        result = session.run("MATCH (n) RETURN DISTINCT n.name AS name, labels(n) AS labels")
        records = list(result)
        print(f"      从 Neo4j 读取到 {len(records)} 个实体节点")

        for r in records:
            name = r["name"]
            label = r["labels"][0] if r["labels"] else "Unknown"

            # Disease 节点有描述属性，一并纳入向量检索
            if label == "Disease":
                desc_result = session.run(
                    "MATCH (d:Disease {name: $name}) RETURN d.desc AS desc LIMIT 1",
                    name=name
                )
                desc_record = desc_result.single()
                desc = desc_record["desc"] if desc_record and desc_record["desc"] else ""
                text = f"{name}：{desc}" if desc else name
            else:
                text = name

            documents.append(text)
            metadatas.append({"type": label, "name": name})
            ids.append(f"{label}_{name}")

    driver.close()

    # ── 2. 初始化 ChromaDB + 中文 embedding ──
    print("[2/4] 加载中文语义模型...")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )

    print("[3/4] 创建 ChromaDB 索引...")
    client = chromadb.PersistentClient(path=CHROMA_PATH)

    # 如果已有旧索引，删除重建
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"description": "GraphRAG 医疗实体向量索引"}
    )

    # ── 3. 批量写入（分批，避免内存溢出） ──
    BATCH_SIZE = 500
    total = len(documents)
    for i in range(0, total, BATCH_SIZE):
        batch_end = min(i + BATCH_SIZE, total)
        collection.add(
            documents=documents[i:batch_end],
            metadatas=metadatas[i:batch_end],
            ids=ids[i:batch_end]
        )
        print(f"      已写入 {batch_end}/{total} 条")

    # ── 4. 测试检索 ──
    print("[4/4] 测试语义检索...")
    test_queries = ["心脏血管堵了", "脑袋疼", "血糖高怎么办"]
    for q in test_queries:
        results = collection.query(query_texts=[q], n_results=3)
        matched = results["documents"][0]
        distances = results["distances"][0]
        matched_str = "  →  ".join(
            f"{doc[:30]}({1-dist:.2f})" for doc, dist in zip(matched, distances)
        )
        print(f"  '{q}' {matched_str}")

    print(f"\n✅ 索引构建完成！共 {total} 个实体写入 {CHROMA_PATH}/")
    print("   后续在 chat_deepseek_api.py 中自动加载此索引进行双引擎检索。")


if __name__ == "__main__":
    build_index()
