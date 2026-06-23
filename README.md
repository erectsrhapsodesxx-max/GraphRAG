# GraphRAG: 基于知识图谱的医学知识智能检索与问答系统

使用 VS Code + Claude Code 进行医学知识图谱问答系统的前后端 vibe coding 开发，提供聊天主界面，支持多种主流 API 模型的切换。

针对传统医疗 RAG 在复杂疾病关联推理、跨实体语义检索及临床知识碎片化等问题，引入 Neo4j 与 ChromaDB 构建 GraphRAG 医学知识问答系统。将知识图谱的精确检索能力与生成式 AI 的语义理解能力相结合，为用户提供准确、及时的医疗咨询服务。

## 效果图

![问答系统效果图](img/qa_show.png)

## 技术实现

### 1. 医学知识图谱构建与结构化检索
- 基于 LLM 提取 200+ 份医学文档、权威网站数据及疾病拓扑中的实体与关系并存入 Neo4j
- 结合 ChromaDB + text2vec-base-chinese 中文语义向量模型构建多路召回架构
- 采用 LLM 别名对齐与实体抽取解决口语化表达与结构化数据的异构对齐问题，消除手工维护领域词典的扩展瓶颈

### 2. 基于查询分析的动态路由
- 利用实体链接与意图分类器分析查询复杂度
- 基础概念与模糊查询分流至 ChromaDB 向量检索
- 涉及跨实体复杂关联的逻辑分发至 Neo4j 图引擎
- 检索结果不足时由 LLM 自生成 Cypher 兜底
- 显著降低 API 调用成本与响应延迟

### 3. 多跳图推理与查询改写
- 基于 Neo4j 图数据库实现三类多跳推理模式：共享症状跨疾病发现、药品-药厂追溯链、并发症-用药链
- 将隐含关联显式注入上下文
- 结合 LangChain 实现 LLM 查询改写机制，自动进行多轮对话中的指代消解
- 减少传统 RAG 在多轮交互中因上下文断裂导致的检索漂移

## 系统架构

### 检索流程
```
用户问题 → LLM查询改写 → 动态路由分析 → 精确Cypher / 向量语义 / 图关系查询
                                         ↘ NL→Cypher兜底（低命中时）
                                         → 多跳推理 → 上下文融合 → DeepSeek流式生成
```

### 核心引擎
| 引擎 | 技术 | 作用 |
|------|------|------|
| 意图精确检索 | Neo4j + QuestionParser | 意图→Cypher，精确查症状/药品/病因等 |
| 向量语义检索 | ChromaDB + text2vec | 语义匹配实体，处理口语化表达 |
| 多跳图推理 | Neo4j 2-hop/3-hop | 发现跨实体隐含关联 |
| NL→Cypher 兜底 | LangChain + DeepSeek | 检索不足时 LLM 自生成图查询 |

### 知识图谱规模
- 4.4 万医疗实体节点
- 30 万实体关系边
- 实体类型：疾病(Disease)、症状(Symptom)、药品(Drug)、食物(Food)、检查(Check)、科室(Department)、药厂(Producer)
- 关系类型：has_symptom、common_drug、recommand_drug、do_eat、no_eat、need_check、acompany_with、belongs_to、drugs_of

## 技术栈

### 后端
| 组件 | 用途 |
|------|------|
| Python 3.x | 开发语言 |
| Flask + flask_cors | Web 服务框架 |
| LangChain + langchain-openai | LLM 调用框架（ChatOpenAI 兼容 DeepSeek） |
| DeepSeek API (deepseek-chat) | 生成模型 + 实体抽取 + 查询改写 + Cypher生成 |
| Neo4j | 图数据库，存储医学知识图谱 |
| ChromaDB | 向量数据库，实体语义索引 |
| sentence-transformers (shibing624/text2vec-base-chinese) | 中文语义向量模型 |
| python-dotenv | 环境变量管理 |

### 前端
| 组件 | 用途 |
|------|------|
| React 18 | UI 框架 |
| Tailwind CSS | 样式框架 |
| Axios | HTTP 客户端 |
| Headless UI + Heroicons | UI 组件库 |

## 快速开始

### 1. 环境要求
- Python 3.8+
- Node.js 14+
- Neo4j 数据库（本地 bolt://localhost:7687）
- DeepSeek API 密钥

### 2. 安装步骤

1. 克隆仓库
```bash
git clone https://github.com/erectsrhapsodesxx-max/GraphRAG.git
cd GraphRAG
```

2. 后端设置
```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
.venv\Scripts\activate      # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入 DEEPSEEK_API_KEY
```

3. 构建知识图谱
```bash
# 将医疗数据导入 Neo4j（需先准备好 data/medical.json）
python build_medicalgraph.py

# 构建实体向量索引（从 Neo4j 导出 → ChromaDB）
python entity_indexer.py
```

4. 前端设置
```bash
cd frontend
npm install
```

### 3. 运行系统

1. 启动 Neo4j 数据库
2. 启动后端服务
```bash
python graph_qa_system.py
```
3. 启动前端服务
```bash
cd frontend
npm start
```
4. 打开浏览器访问 http://localhost:3000

也可以直接命令行使用（无需启动前端）：
```bash
python chat_deepseek_api.py
```

## 项目结构

```
GraphRAG/
├── chat_deepseek_api.py      # 核心模块：路由/意图/多跳/查询改写/流式问答
├── graph_qa_system.py        # Flask Web 服务（API + 流式响应）
├── build_medicalgraph.py     # Neo4j 知识图谱构建
├── entity_indexer.py         # ChromaDB 实体向量索引构建
├── question_parser.py        # 问题意图 → Cypher 转换器
├── answer_search.py          # Neo4j 查询执行与答案组装
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── components/       # React 组件（ChatInterface 等）
│   │   ├── App.js
│   │   └── index.js
│   ├── public/
│   ├── package.json
│   └── tailwind.config.js
├── dict/                     # 医疗领域词典（图谱构建用）
├── prepare_data/             # 数据采集与预处理工具
├── chroma_db/                # ChromaDB 向量数据库文件
├── img/                      # 截图与文档图片
├── requirements.txt          # Python 依赖
├── .env.example              # 环境变量模板
├── .gitignore
├── LICENSE
└── README.md
```

## 问答示例

系统支持多种类型的医疗问题：

- 疾病症状查询：`乳腺癌的症状有哪些？`
- 病因分析：`为什么有的人会失眠？`
- 治疗方案：`高血压要怎么治？`
- 用药指导：`肝病要吃啥药？`
- 检查项目：`脑膜炎怎么才能查出来？`

每个问题都会经过路由分析→多引擎检索→DeepSeek 生成，并通过流式响应实时展示。

## 使用指南

1. 系统支持以下类型的医疗问题：
   - 疾病症状查询
   - 病因分析
   - 治疗方案建议
   - 用药指导
   - 检查项目说明

2. 使用建议：
   - 使用清晰、具体的描述
   - 一次只问一个问题
   - 对于紧急情况，请立即就医

## 许可证

MIT License · Copyright (c) 2026 Jinyu
