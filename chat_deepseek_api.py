import os
# 必须在 sentence-transformers import 之前设置，避免 HuggingFace 连接超时
os.environ["HF_HUB_OFFLINE"] = "1"

import sys
import logging
import json
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from neo4j import GraphDatabase
import chromadb
from chromadb.utils import embedding_functions
from question_parser import QuestionPaser

# 设置默认编码为utf-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 禁用Neo4j警告日志
logging.getLogger("neo4j").setLevel(logging.ERROR)


class GraphRAGHandler:
    def __init__(self):
        # 加载环境变量
        load_dotenv(encoding='utf-8')

        # Deepseek API参数
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        print(f"API Key: {'已设置' if self.api_key else '未设置'}")

        if not self.api_key:
            raise ValueError("请在.env文件中设置DEEPSEEK_API_KEY")

        # Neo4j连接参数
        self.uri = "bolt://localhost:7687"
        self.user = "neo4j"
        self.password = "cjy00916"

        # 尝试连接Neo4j
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.online_mode = True
            print("Neo4j 连接成功")
        except Exception as e:
            print(f"Neo4j连接失败，切换到离线模式: {str(e)}")
            self.online_mode = False

        # ── ChromaDB 向量检索引擎初始化 ──
        try:
            self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="shibing624/text2vec-base-chinese"
            )
            self.chroma_client = chromadb.PersistentClient(path="./chroma_db")
            self.collection = self.chroma_client.get_collection(
                "medical_entities", embedding_function=self.ef
            )
            self.vector_mode = True
            print("ChromaDB 向量检索引擎就绪")
        except Exception as e:
            print(f"ChromaDB 未就绪，使用纯关键词检索: {str(e)}")
            self.vector_mode = False

        # ── Cypher 解析器初始化 ──
        try:
            self.parser = QuestionPaser()
            self.intent_mode = True
            print("Cypher 解析器就绪")
        except Exception as e:
            print(f"Cypher 解析器未就绪: {str(e)}")
            self.intent_mode = False

        # ── 会话记忆（多轮对话上下文）──
        self.chat_history = []   # [(question, [entities]), ...] 最近 N 轮
        self._last_classify = {"args": {}, "question_types": []}  # LLM 实体抽取缓存

        # ── LangChain ChatOpenAI (DeepSeek 兼容) ──
        self.llm = ChatOpenAI(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key=self.api_key,
            temperature=0.7,
            streaming=True
        )
        print("LangChain ChatOpenAI 就绪 (DeepSeek)")

        # ── LLM 自生成 Cypher 兜底（替代 Neo4jGraph，无需 APOC 插件）──
        self.nl_graph_enabled = self.online_mode

    # ══════════════════════════════════════════════════════════
    #  意图驱动的精确 Cypher 检索
    # ══════════════════════════════════════════════════════════

    def intent_retrieval(self, question, fallback_entities):
        """
        意图识别 → Cypher 精确查询
        如果识别到具体意图（如 disease_symptom），生成精确 Cypher 查询；
        如果识别失败，降级返回 fallback_entities
        """
        if not self.intent_mode:
            return [], "意图引擎未就绪"

        try:
            # 1. 复用 LLM 实体抽取结果（_route_question 中已缓存）
            classify_res = getattr(self, '_last_classify', None)
            if not classify_res or not classify_res.get('question_types'):
                classify_res = self._llm_extract_entities(question)
            question_types = classify_res.get('question_types', [])
            entities = classify_res.get('args', {})

            if not question_types or question_types == ['others']:
                return [], f"未识别到明确意图 (entities: {list(entities.keys())})"

            # 2. 生成精确 Cypher
            sqls = self.parser.parser_main(classify_res)

            # 3. 执行 Cypher 查询
            intent_context = []
            with self.driver.session() as session:
                for sql_group in sqls:
                    qtype = sql_group['question_type']
                    for cypher in sql_group.get('sql', []):
                        try:
                            result = session.run(cypher)
                            records = list(result)
                            if records:
                                if qtype == 'disease_symptom':
                                    symptoms = [r['n.name'] for r in records]
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的症状: {', '.join(symptoms)}"
                                    )
                                elif qtype == 'disease_drug':
                                    drugs = [r['n.name'] for r in records]
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的药品: {', '.join(drugs)}"
                                    )
                                elif qtype == 'disease_cause':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的病因: {records[0]['m.cause']}"
                                    )
                                elif qtype == 'disease_prevent':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的预防: {records[0]['m.prevent']}"
                                    )
                                elif qtype == 'disease_cureway':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的治疗: {records[0]['m.cure_way']}"
                                    )
                                elif qtype == 'disease_cureprob':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的治愈率: {records[0]['m.cured_prob']}"
                                    )
                                elif qtype == 'disease_lasttime':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的周期: {records[0]['m.cure_lasttime']}"
                                    )
                                elif qtype == 'disease_easyget':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的易感人群: {records[0]['m.easy_get']}"
                                    )
                                elif qtype == 'disease_acompany':
                                    acompany = [r['n.name'] for r in records]
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的并发症: {', '.join(acompany)}"
                                    )
                                elif qtype == 'disease_do_food':
                                    foods = [r['n.name'] for r in records]
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的宜吃食物: {', '.join(foods)}"
                                    )
                                elif qtype == 'disease_not_food':
                                    foods = [r['n.name'] for r in records]
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的忌吃食物: {', '.join(foods)}"
                                    )
                                elif qtype == 'disease_check':
                                    checks = [r['n.name'] for r in records]
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的检查: {', '.join(checks)}"
                                    )
                                elif qtype == 'disease_desc':
                                    intent_context.append(
                                        f"【{records[0]['m.name']}】的描述: {records[0]['m.desc']}"
                                    )
                                elif qtype in ('symptom_disease', 'drug_disease',
                                               'food_do_disease', 'food_not_disease',
                                               'check_disease'):
                                    diseases = [r['m.name'] for r in records]
                                    intent_context.append(
                                        f"相关疾病: {', '.join(diseases)}"
                                    )
                                else:
                                    # 其他类型：通用记录
                                    for r in records:
                                        vals = [str(v) for v in r.values()]
                                        intent_context.append(' | '.join(vals))
                        except Exception as e:
                            print(f"Cypher 执行失败 [{qtype}]: {e}")

            source = f"意图识别 → {question_types} (实体: {list(entities.keys())})"
            return intent_context, source

        except Exception as e:
            print(f"意图检索失败: {e}")
            return [], f"意图检索异常: {str(e)}"

    # ══════════════════════════════════════════════════════════
    #  多跳图推理：发现跨实体隐含关联
    # ══════════════════════════════════════════════════════════

    def multi_hop_retrieval(self, entity_names, max_depth=2):
        """
        多跳图推理：
        在单跳检索结果基础上，沿图谱关系再走一跳，
        发现隐含的跨实体关联。

        三组模式:
        1. 共享症状 → 哪些其他疾病也有类似症状？
        2. 药品-药厂链 → 治疗该病的药由哪些药厂生产？
        3. 并发症-用药链 → 该病的并发症用什么药？
        """
        if not entity_names:
            return [], "无实体可做多跳"

        hop_context = []
        hop_source = []

        with self.driver.session() as session:
            for entity in entity_names[:5]:  # 限制实体数，控制检索成本

                # ── 模式1: 共享症状的疾病发现 ──
                # X → 症状 ← 其他病（2-hop)
                try:
                    r = session.run("""
                        MATCH (d1 {name: $n})-[:has_symptom]->(s:Symptom)
                              <-[:has_symptom]-(d2)
                        WHERE d1 <> d2
                        RETURN s.name AS symptom, collect(DISTINCT d2.name) AS diseases
                        LIMIT 5
                    """, n=entity)
                    for record in r:
                        symptom = record["symptom"]
                        diseases = record["diseases"][:3]  # 每种症状最多列 3 个
                        if diseases:
                            hop_context.append(
                                f'[2-hop] {entity} + {symptom}'
                                f' -> shared with: {", ".join(diseases)}'
                            )
                except Exception as e:
                    pass

                # ── 模式2: 药品 → 药厂链 ──
                # 疾病 → 药品 → 药厂 (2-hop)
                try:
                    r = session.run("""
                        MATCH (d {name: $n})-[:common_drug|recommand_drug]->
                              (drug:Drug)-[:drugs_of]->(p:Producer)
                        RETURN DISTINCT drug.name AS drug, p.name AS producer
                        LIMIT 5
                    """, n=entity)
                    pairs = [(rec["drug"], rec["producer"]) for rec in r]
                    if pairs:
                        chain = "; ".join(f"{d}({p})" for d, p in pairs)
                        hop_context.append(
                            f'[2-hop] {entity} drug-producer chain: {chain}'
                        )
                except Exception as e:
                    pass

                # ── 模式3: 并发症 → 药品链 ──
                # 疾病 → 并发症 → 并发症的常用药 (2-hop to 3-hop)
                try:
                    r = session.run("""
                        MATCH (d1 {name: $n})-[:acompany_with]->(d2:Disease)
                        OPTIONAL MATCH (d2)-[:common_drug]->(drug:Drug)
                        RETURN d2.name AS complication,
                               collect(DISTINCT drug.name)[0..3] AS drugs
                        LIMIT 5
                    """, n=entity)
                    for record in r:
                        comp = record["complication"]
                        drugs = record["drugs"]
                        if drugs:
                            hop_context.append(
                                f'[2-hop] {entity} -> complication {comp}'
                                f' -> drugs: {", ".join(drugs)}'
                            )
                except Exception as e:
                    pass

        if hop_context:
            hop_source.append(f"多跳推理({len(hop_context)}条关联发现)")
        else:
            hop_source.append("多跳推理(无新发现)")

        return hop_context, "; ".join(hop_source)

    # ══════════════════════════════════════════════════════════
    #  NL→Cypher 兜底：LLM 自生成图查询
    # ══════════════════════════════════════════════════════════

    def _nl_graph_fallback(self, question):
        """
        当意图分类和向量检索都效果差时，让 ChatOpenAI 看 Schema
        自生成 Cypher，然后用 Neo4j driver 执行，返回结构化结果。
        不依赖 APOC 插件，只用到 LangChain + 官方 neo4j 驱动。
        """
        if not self.nl_graph_enabled:
            return [], "LLM→Cypher 不可用"

        try:
            # ── Step 1: 让 LLM 根据 Schema 生成 Cypher ──
            schema_prompt = f"""你是一个 Neo4j Cypher 专家。以下是医学知识图谱的 Schema：

节点标签: Disease(疾病), Symptom(症状), Drug(药品), Food(食物), Check(检查), Department(科室), Producer(药厂)
关系: (Disease)-[has_symptom]->(Symptom)
      (Disease)-[acompany_with]->(Disease)
      (Disease)-[common_drug]->(Drug)
      (Disease)-[recommand_drug]->(Drug)
      (Disease)-[need_check]->(Check)
      (Disease)-[do_eat]->(Food)
      (Disease)-[no_eat]->(Food)
      (Disease)-[belongs_to]->(Department)
      (Producer)-[drugs_of]->(Drug)

所有节点都有 name 属性。Disease 还有 desc, cause, prevent, cure_way, cured_prob 属性。

用户问题: "{question}"

请只写一条 Cypher 查询（不要解释，不要 markdown 格式），用 CONTAINS 做模糊匹配。
查询示例: MATCH (d:Disease)-[r:has_symptom]->(s:Symptom) WHERE d.name CONTAINS '感冒' RETURN d.name, s.name LIMIT 10"""

            cypher_response = self.llm.invoke(schema_prompt)
            cypher = cypher_response.content.strip()

            # 清理 LLM 可能多输出的 markdown 标记
            for marker in ['```cypher', '```', 'cypher']:
                cypher = cypher.replace(marker, '').strip()

            if not cypher.upper().startswith('MATCH'):
                return [], f"LLM 未生成有效 Cypher: {cypher[:50]}..."

            # ── Step 2: 执行 Cypher ──
            with self.driver.session() as session:
                result = session.run(cypher)
                records = list(result)

            if not records:
                return [], f"NL→Cypher 无结果 (已执行: {cypher[:60]}...)"

            # ── Step 3: 格式化 ──
            context = []
            for row in records[:10]:
                formatted = " | ".join(str(v) for v in row.values())
                context.append(formatted)

            return context, f"NL→Cypher 兜底 → {len(context)} 条结果"

        except Exception as e:
            return [], f"NL→Cypher 异常: {str(e)[:80]}"

    # ══════════════════════════════════════════════════════════
    #  LLM 查询改写：多轮对话记忆
    # ══════════════════════════════════════════════════════════

    def _rewrite_query(self, question):
        """
        检测追问场景 → LLM 改写为独立完整问题 → 投入检索
        只保留最近 2 轮简史，Token 开销约 100/次
        """
        # 追问特征检测
        pronouns = ['它', '他', '她', '其', '这', '这个', '该', '那', '那个',
                    '他们', '它们', '她们', '这些', '那些', '是否', '会不会',
                    '有没有', '还会', '怎么办', '如何预防', '怎么治疗']
        has_pronoun = any(p in question for p in pronouns)
        is_ambiguous = len(question) < 15 and not any(
            kw in question for kw in ['症状', '药', '检查', '病因', '治疗', '预防', '手术']
        )

        if not (has_pronoun or is_ambiguous) or not self.chat_history:
            return question

        # 取最近 2 轮简史
        recent = self.chat_history[-2:]
        history_lines = []
        for q, entities in recent:
            entity_str = "、".join(entities) if entities else q[:20]
            history_lines.append(f"用户问了关于{entity_str}的问题")

        history_text = "；".join(history_lines)

        try:
            rewrite_prompt = (
                f"对话历史: {history_text}\n"
                f"用户追问: {question}\n"
                f"把追问改写为独立的完整问题（一句话）:"
            )
            result = self.llm.invoke(rewrite_prompt)
            rewritten = result.content.strip()
            # 安全检查：改写后不能太离谱
            if len(rewritten) > len(question) + 5 and len(rewritten) < 100:
                self._last_rewrite = rewritten
                return rewritten
        except Exception as e:
            print(f"查询改写失败: {e}")

        return question

    # ══════════════════════════════════════════════════════════
    #  LLM 实体抽取：ChromaDB 发现实体 + LLM 分类类型
    #  替代 Aho-Corasick 词典匹配，支持 2 万+实体规模
    # ══════════════════════════════════════════════════════════

    def _llm_extract_entities(self, question):
        """
        ChromaDB 向量搜候选实体 → LLM 判断实体类型与意图
        返回格式兼容 classifier.classify():
          {"args": {"高血压": ["disease"]}, "question_types": ["disease_symptom"]}
        """
        result = {"args": {}, "question_types": []}

        # ── Step 1: ChromaDB 向量搜候选实体 ──
        candidates = []
        if self.vector_mode:
            try:
                vr = self.collection.query(query_texts=[question], n_results=10)
                for doc, dist, meta in zip(vr["documents"][0], vr["distances"][0], vr["metadatas"][0]):
                    if dist < 0.7:
                        candidates.append({
                            "name": meta["name"],
                            "type": meta.get("type", "Unknown"),
                            "score": round(1 - dist, 2)
                        })
            except Exception:
                pass

        if not candidates:
            return result

        # ── Step 2: LLM 分类实体类型 + 判断意图 ──
        candidate_text = "\n".join(
            f"- {c['name']} (ChromaDB标注: {c['type']})" for c in candidates[:8]
        )
        classify_prompt = f"""分析以下用户问题，从候选实体中选出真正相关的，并标注每个实体的正确类型。

候选实体（来自向量检索，类型可能不准）:
{candidate_text}

用户问题: "{question}"

可用类型: disease(疾病), symptom(症状), drug(药品), food(食物), check(检查), department(科室)

用户意图（选一个最匹配的）:
disease_symptom(查症状), disease_drug(查药), disease_check(查检查),
disease_cause(查病因), disease_prevent(查预防), disease_cureway(查治疗),
disease_acompany(查并发症), disease_do_food(查饮食), disease_desc(查描述)

只输出 JSON（不要解释）:
{{"entities": [{{"name":"...", "type":"..."}}], "intent":"disease_symptom"}}"""

        try:
            response = self.llm.invoke(classify_prompt)
            parsed = json.loads(response.content.strip())
        except Exception:
            return result

        # ── Step 3: 转成 parser 兼容格式 ──
        args = {}
        for e in parsed.get("entities", []):
            name = e["name"]
            etype = e["type"]
            if name not in args:
                args[name] = []
            args[name].append(etype)

        intent = parsed.get("intent", "others")
        question_types = [intent] if intent != "others" else []

        return {"args": args, "question_types": question_types}

    # ══════════════════════════════════════════════════════════
    #  动态路由：根据问题特征决定检索路径
    # ══════════════════════════════════════════════════════════

    def _route_question(self, question):
        """
        分析问题特征 → 决定走哪条检索路径
        返回: {
            "route": "precise" | "semantic" | "full",
            "intent_found": bool,
            "has_entity": bool,
            "is_short": bool
        }
        """
        # 特征1: ChromaDB + LLM 抽取实体与意图
        intent_found = False
        has_entity = False
        try:
            classify_res = self._llm_extract_entities(question)
            self._last_classify = classify_res  # 缓存，供 intent_retrieval 复用
            question_types = classify_res.get('question_types', [])
            entities = classify_res.get('args', {})
            intent_found = bool(question_types) and question_types != ['others']
            has_entity = bool(entities)
        except Exception:
            self._last_classify = {"args": {}, "question_types": []}

        # 特征2: 问题复杂度（短问题=概念查询，长问题=复杂推理）
        is_short = len(question) <= 6

        # ── 路由决策 ──
        if intent_found and has_entity:
            # 意图明确 + 实体清晰 → 精确查询，跳过多跳和向量
            route = "precise"
        elif intent_found and not has_entity:
            # 意图明确但无实体（如纯症状描述）→ 向量语义找实体 + 意图查关系
            route = "semantic"
        elif is_short:
            # 短问题 → 可能查概念/定义，向量就够了
            route = "simple"
        else:
            # 模糊复杂 → 全引擎
            route = "full"

        return {
            "route": route,
            "intent_found": intent_found,
            "has_entity": has_entity,
            "is_short": is_short
        }

    # ══════════════════════════════════════════════════════════
    #  路由驱动的混合检索
    # ══════════════════════════════════════════════════════════

    def hybrid_retrieval(self, question):
        """
        路由驱动的混合检索：
        1. 先跑路由分析问题特征
        2. 根据路由决策选择性执行检索引擎
        3. 合并结果返回
        """
        if not self.online_mode:
            return {"entities": [], "context": "", "source": "离线模式"}

        # ── Step 0: LLM 查询改写（多轮对话记忆）──
        original_question = question
        rewritten = self._rewrite_query(question)
        if rewritten != question:
            source_hint = f"LLM改写: {original_question} → {rewritten}"
            question = rewritten
        else:
            source_hint = ""

        # ── Step 1: 路由分析 ──
        route_info = self._route_question(question)
        route = route_info["route"]
        source_parts = [f"路由: {route}"]
        all_context = []
        entity_names = []

        # ── Step 2: 按路由执行检索 ──

        if route == "precise":
            # 意图明确 → 只跑意图精确查询（最省）
            intent_context, intent_src = self.intent_retrieval(question, [])
            all_context.extend(intent_context)
            source_parts.append(intent_src)
            # 从意图上下文中提取实体名（如 "【高血压】的症状: ..."）
            for ctx in intent_context:
                if ctx.startswith('【') and '】' in ctx:
                    name = ctx.split('】')[0].replace('【', '')
                    if name not in entity_names:
                        entity_names.append(name)

        elif route == "semantic":
            # 意图明确但缺实体 → 向量找实体 + 意图查关系
            if self.vector_mode:
                try:
                    vr = self.collection.query(query_texts=[question], n_results=5)
                    for doc, dist, meta in zip(vr["documents"][0], vr["distances"][0], vr["metadatas"][0]):
                        if dist < 0.7:
                            entity_names.append(meta["name"])
                    source_parts.append(f"向量补充 → {len(entity_names)} 个实体")
                except Exception:
                    pass
            intent_context, intent_src = self.intent_retrieval(question, entity_names)
            all_context.extend(intent_context)
            graph_ctx = self._graph_relation_query(entity_names[:3])
            all_context.extend(graph_ctx)
            source_parts.append(intent_src)

        elif route == "simple":
            # 短概念查询 → 只需向量（最快）
            if self.vector_mode:
                try:
                    vr = self.collection.query(query_texts=[question], n_results=5)
                    for doc, dist, meta in zip(vr["documents"][0], vr["distances"][0], vr["metadatas"][0]):
                        if dist < 0.7:
                            entity_names.append(meta["name"])
                    source_parts.append(f"向量语义 → {len(entity_names)} 个实体")
                except Exception:
                    pass
            graph_ctx = self._graph_relation_query(entity_names[:3])
            all_context.extend(graph_ctx)

        else:  # route == "full"
            # 模糊复杂 → 全引擎保障
            # 意图
            intent_context, intent_src = self.intent_retrieval(question, [])
            all_context.extend(intent_context)
            source_parts.append(intent_src)
            # 向量
            if self.vector_mode:
                try:
                    vr = self.collection.query(query_texts=[question], n_results=5)
                    for doc, dist, meta in zip(vr["documents"][0], vr["distances"][0], vr["metadatas"][0]):
                        if dist < 0.7:
                            entity_names.append(meta["name"])
                    source_parts.append(f"向量补充 → {len(entity_names)} 个实体")
                except Exception:
                    pass
            # 图关系查询
            graph_ctx = self._graph_relation_query(entity_names[:5])
            all_context.extend(graph_ctx)
            source_parts.append(f"向量+图 → {len(entity_names)} 个实体")

            # NL→Cypher 兜底：检索结果太少时，让 LLM 自生成 Cypher
            if len(all_context) <= 3 and self.nl_graph_enabled:
                nl_ctx, nl_src = self._nl_graph_fallback(question)
                all_context.extend(nl_ctx)
                source_parts.append(nl_src)

        # ── Step 3: 多跳推理（仅 precise 和 full 路由执行）──
        if route in ("precise", "full"):
            hop_ctx, hop_src = self.multi_hop_retrieval(entity_names[:3])
            all_context.extend(hop_ctx)
            source_parts.append(hop_src)

        if not all_context:
            all_context = ["未检索到相关信息，请基于通用医学知识回答。"]

        # ── 更新会话记忆 ──
        if entity_names:
            self.chat_history.append((original_question, entity_names[:5]))
            if len(self.chat_history) > 5:  # 只保留最近 5 轮
                self.chat_history.pop(0)

        source = "; ".join(source_parts)
        if source_hint:
            source = f"{source_hint} | {source}"

        return {
            "entities": entity_names,
            "context": all_context,
            "source": source,
            "route": route
        }

    def _graph_relation_query(self, entity_names):
        """从 Neo4j 查询实体的关系信息（症状、药品、食物、检查）"""
        graph_context = []
        with self.driver.session() as session:
            for entity in entity_names:
                try:
                    r = session.run(
                        "MATCH (d {name: $n})-[r:has_symptom]->(s:Symptom) "
                        "RETURN s.name AS s LIMIT 5", n=entity
                    )
                    symptoms = [rec["s"] for rec in r]
                    if symptoms:
                        graph_context.append(f"【{entity}】的症状: {', '.join(symptoms)}")
                except Exception:
                    pass
                try:
                    r = session.run(
                        "MATCH (d {name: $n})-[r:common_drug|recommand_drug]->(drug:Drug) "
                        "RETURN DISTINCT drug.name AS d LIMIT 5", n=entity
                    )
                    drugs = [rec["d"] for rec in r]
                    if drugs:
                        graph_context.append(f"【{entity}】的药品: {', '.join(drugs)}")
                except Exception:
                    pass
                try:
                    r = session.run(
                        "MATCH (d {name: $n})-[r:do_eat]->(f:Food) "
                        "RETURN f.name AS f LIMIT 3", n=entity
                    )
                    foods = [rec["f"] for rec in r]
                    if foods:
                        graph_context.append(f"【{entity}】宜吃: {', '.join(foods)}")
                except Exception:
                    pass
                try:
                    r = session.run(
                        "MATCH (d {name: $n})-[r:need_check]->(c:Check) "
                        "RETURN c.name AS c LIMIT 3", n=entity
                    )
                    checks = [rec["c"] for rec in r]
                    if checks:
                        graph_context.append(f"【{entity}】检查: {', '.join(checks)}")
                except Exception:
                    pass
        return graph_context

    # ══════════════════════════════════════════════════════════
    #  流式问答
    # ══════════════════════════════════════════════════════════

    def get_answer_stream(self, question):
        try:
            # 检索
            retrieval = self.hybrid_retrieval(question)

            # 构建 Prompt
            if isinstance(retrieval["context"], list):
                context_text = "\n".join(retrieval["context"])
            else:
                context_text = retrieval["context"]

            source_tag = f"[检索来源: {retrieval['source']}]"

            prompt = f"""你是一个专业的医疗助手。请严格基于以下从医学知识图谱中检索到的信息回答问题。
如果检索信息与问题不相关或不足以回答，请如实说明。

{source_tag}

检索到的医学知识:
{context_text}

用户问题: {question}

请提供准确、专业的回答，并在可行时引用检索到的具体信息。
回答:"""

            messages = [
                {"role": "system", "content": "你是一个专业的医疗助手，严格基于检索到的知识图谱信息回答问题。"},
                {"role": "user", "content": prompt}
            ]

            # LangChain ChatOpenAI 流式调用
            for chunk in self.llm.stream(messages):
                if chunk.content:
                    yield chunk.content

        except Exception as e:
            print(f"生成回答时出错: {str(e)}")
            yield f"抱歉，处理您的问题时出现错误: {str(e)}"

    def get_answer(self, question):
        return ''.join(self.get_answer_stream(question))

    def __del__(self):
        if hasattr(self, 'driver') and self.online_mode:
            self.driver.close()


def main():
    try:
        handler = GraphRAGHandler()
        print("\n=== 医疗知识问答系统 (双引擎检索) ===")
        print("输入 'quit' 或 'exit' 退出程序")

        while True:
            question = input("\n请输入您的问题: ").strip()

            if question.lower() in ['quit', 'exit']:
                print("感谢使用，再见！")
                break

            if not question:
                print("问题不能为空，请重新输入")
                continue

            print("\n正在思考...")
            for chunk in handler.get_answer_stream(question):
                print(chunk, end='', flush=True)
            print("\n")

    except Exception as e:
        print(f"程序运行出错: {str(e)}")


if __name__ == "__main__":
    main()
