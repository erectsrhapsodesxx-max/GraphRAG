# GraphRAG 技术栈学习笔记

> 基于项目：GraphRAG 医疗知识图谱问答系统  
> 学习日期：2026-06-15

---

## 一、技术栈全景图

```
┌─────────────────────────────────────────────────────┐
│                    前端 (Frontend)                    │
│  React 18  +  Tailwind CSS  +  Axios                │
│  用户界面     样式框架         HTTP 请求              │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / SSE 流式
┌──────────────────────┴──────────────────────────────┐
│                    后端 (Backend)                     │
│                                                      │
│  Flask ──── Python Web 框架，提供 REST API            │
│  jieba ──── 中文分词，提取问题关键词                   │
│  Neo4j ──── 图数据库，存储医学知识图谱                 │
│  DeepSeek API ── LLM，基于检索结果生成自然语言回答     │
│  python-dotenv ── 环境变量管理                        │
└──────────────────────┬──────────────────────────────┘
                       │ Bolt 协议
┌──────────────────────┴──────────────────────────────┐
│                 数据库层 (Database)                    │
│                                                      │
│  Neo4j 图数据库 — 4.4万节点 + 30万关系边              │
│  MongoDB — 数据清洗阶段的中间存储（爬虫管线）           │
└─────────────────────────────────────────────────────┘
```

---

## 二、各技术栈知识点总结

### 2.1 Flask — Web 框架

**是什么**：Python 的轻量级 Web 框架，核心是路由 + 请求/响应处理。

**在项目中的作用**：
- 提供 `POST /ask` 接口接收用户问题
- 提供 `GET /health` 健康检查接口
- 通过 **Server-Sent Events (SSE)** 协议实现流式响应

**核心代码模式**：

```python
from flask import Flask, request, Response, stream_with_context

app = Flask(__name__)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()          # 解析 JSON 请求体
    question = data.get('question')    # 取出用户问题

    def generate():                    # 生成器函数，逐块产出内容
        for chunk in handler.get_answer_stream(question):
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"       # 流结束标记

    return Response(
        stream_with_context(generate()),  # 流式返回
        mimetype='text/event-stream'      # SSE MIME 类型
    )
```

**关键知识点**：

| 概念 | 说明 |
|------|------|
| `stream_with_context()` | Flask 的流式上下文管理器，保证在生成器运行期间请求上下文不丢失 |
| `text/event-stream` | SSE 协议的 MIME 类型，告诉浏览器这是服务器推送的流 |
| `data: {...}\n\n` | SSE 标准格式，每个事件以 `data:` 开头，以双换行结尾 |
| CORS | 跨域资源共享，允许前端 `localhost:3000` 访问后端 `localhost:5000` |

---

### 2.2 Server-Sent Events (SSE) — 流式传输协议

**是什么**：HTTP 长连接技术，服务器可以持续向客户端推送数据流。比 WebSocket 更简单，只支持单向（服务器 → 客户端）。

**在本项目中的工作流程**：

```
客户端 (React)                          服务器 (Flask)
     │                                      │
     │──── POST /ask {"question": "..."} ──▶│
     │                                      │
     │          HTTP 200 + Connection: keep-alive
     │◀────────────────────────────────────│
     │                                      │
     │◀─── data: {"content": "高血压"} ─────│  ← 第一个 chunk
     │◀─── data: {"content": "的常见"} ─────│  ← 第二个 chunk
     │◀─── data: {"content": "症状包括"} ───│  ← 第三个 chunk
     │◀─── data: {"content": "..."} ───────│  ← 持续流式输出
     │◀─── data: [DONE] ──────────────────│  ← 流结束
     │                                      │
```

**前端接收 SSE 的代码逻辑**（`ChatInterface.js`）：

```javascript
const response = await fetch('http://localhost:5000/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: input })
});

const reader = response.body.getReader();  // 获取 ReadableStream
const decoder = new TextDecoder();          // 字节 → 文本解码器
let buffer = '';

while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    // 按 \n 分割处理每个 SSE 事件
    const lines = buffer.split('\n');
    buffer = lines.pop();  // 最后一个可能不完整，保留到下次

    for (const line of lines) {
        if (line.startsWith('data: ')) {
            const data = line.slice(6);  // 去掉 'data: ' 前缀
            if (data === '[DONE]') return;
            const parsed = JSON.parse(data);
            // 增量追加到当前 AI 消息末尾
            setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1].content += parsed.content;
                return updated;
            });
        }
    }
}
```

**为什么用 SSE 而不是 WebSocket？**
- SSE 基于 HTTP，更简单，不需要额外的握手协议
- 本项目只需要服务器推送 → 客户端接收的单向流
- Flask 原生支持 SSE，无需额外依赖

---

### 2.3 jieba — 中文分词

**是什么**：Python 中文分词库，支持精确模式、全模式、搜索引擎模式。

**在项目中的作用**：从用户问题中提取关键词，用于查询 Neo4j。

**使用方式**：

```python
import jieba

# 精确模式（默认）：把文本精确切分开，不存在冗余
words = jieba.lcut("高血压有哪些常见症状")
# 结果: ['高血压', '有', '哪些', '常见', '症状']

# 取第一个词作为检索关键词
keyword = words[0]  # '高血压'
```

**分词模式对比**：

| 模式 | 特点 | 示例 |
|------|------|------|
| 精确模式 `lcut()` | 最精确，无冗余 | `['高血压', '有', '哪些', '常见', '症状']` |
| 全模式 `lcut(..., cut_all=True)` | 所有可能词都输出 | `['高血', '高血压', '血压', '有', '哪些', '常见', '症状']` |
| 搜索引擎模式 `lcut_for_search()` | 在精确基础上再切 | `['高血', '血压', '高血压', '有', '哪些', '常见', '症状']` |

---

### 2.4 DeepSeek API — 大语言模型

**是什么**：DeepSeek 提供的云端大模型 API，兼容 OpenAI 接口格式。

**在项目中的作用**：接收 "上下文 + 问题" 的 Prompt，流式生成自然语言回答。

**调用代码**：

```python
import requests

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

data = {
    "model": "deepseek-chat",      # 模型名称
    "messages": [
        {"role": "system", "content": "你是一个专业的医疗助手。"},
        {"role": "user", "content": prompt}  # prompt = 上下文 + 问题
    ],
    "temperature": 0.7,            # 创造性参数 (0=确定, 1=随机)
    "stream": True                 # 开启流式输出
}

response = requests.post(
    "https://api.deepseek.com/v1/chat/completions",
    headers=headers,
    json=data,
    stream=True                     # 流式读取
)

# 解析 SSE 响应
for line in response.iter_lines():
    if line.startswith('data: '):
        chunk = json.loads(line[6:])
        content = chunk['choices'][0]['delta']['content']
        yield content  # 逐块产出文本
```

**Prompt 模板**（本项目使用的 RAG 模式）：

```
你是一个专业的医疗助手。使用以下信息来回答问题。
如果无法从提供的信息中找到答案，请说明你不知道。

上下文信息:
{从 Neo4j 知识图谱检索到的实体名称}

问题: {用户问题}

请提供准确、专业的回答，并尽可能引用相关的医疗知识。
```

---

### 2.5 React + Tailwind CSS + Axios — 前端三件套

| 技术 | 版本 | 作用 |
|------|------|------|
| **React** | 18.3 | 组件化 UI 框架，管理聊天界面的状态和渲染 |
| **Tailwind CSS** | 3.4 | 原子化 CSS 框架，直接在 HTML 类名中写样式 |
| **Axios** | 1.8 | HTTP 客户端（虽然当前代码实际用 `fetch`） |

**前端组件结构**：

```
App.js
└── ChatInterface.js   ← 所有核心逻辑都在这里
    ├── 健康检查 (GET /health)
    ├── 消息列表 (messages state)
    ├── SSE 流式读取
    ├── 增量渲染
    └── UI 布局（Tailwind）
```

**React 状态管理核心**：

```javascript
const [messages, setMessages] = useState([]);   // 消息列表
const [input, setInput] = useState('');          // 输入框
const [isLoading, setIsLoading] = useState(false); // 加载状态
const [error, setError] = useState(null);         // 错误信息
```

---

## 三、Neo4j 图数据库深度讲解

### 3.1 什么是图数据库？

**关系型数据库 vs 图数据库**：

```
关系型数据库 (MySQL)                    图数据库 (Neo4j)
─────────────────────────              ─────────────────────
│ 用户表 │ 订单表 │ 商品表 │           │   (User)──[购买]──▶(Product)
│  id    │  user_id│ id     │           │     │                │
│  name  │  product│ name   │           │  [关注]           [属于]
│        │  _id    │        │           │     │                │
└────────┴─────────┴────────┘           │   (User)         (Category)
     ↑ 多表 JOIN 开销大 ↑               └── 关系即一等公民，直接遍历 ──┘
```

**图数据库的核心优势**：
- **关系是第一等公民**：数据之间的连接和数据本身同等重要
- **遍历速度快**：多跳查询是 O(1) 而非关系库的 O(n³) 级 JOIN
- **天然适合知识图谱**：实体 = 节点，关系 = 边

### 3.2 Neo4j 核心概念

```
┌──────────────────────────────────────────────────┐
│                  Neo4j 核心四要素                   │
├──────────┬───────────────────────────────────────┤
│ 节点 Node │ 代表一个实体，可以有多个标签 (Label)     │
│          │ 例: (高血压) 标签为 :Disease            │
│          │ 可以有属性: {name, desc, cause, ...}    │
├──────────┼───────────────────────────────────────┤
│ 关系 Rel │ 连接两个节点的有向边，必须有类型 (Type)   │
│          │ 例: (高血压)-[:has_symptom]->(头痛)     │
├──────────┼───────────────────────────────────────┤
│ 属性 Prop│ 键值对，可以挂在节点上，也可以挂在关系上    │
│          │ 例: 节点属性 {name: "高血压", cause: "..."}│
├──────────┼───────────────────────────────────────┤
│ 标签 Label│ 给节点分类的标记，一个节点可以有多个标签   │
│          │ 例: :Disease, :Drug, :Symptom          │
└──────────┴───────────────────────────────────────┘
```

**Cypher 查询语言**（Neo4j 的 SQL 等价物）：

```cypher
-- SQL: SELECT * FROM disease JOIN symptom ON ...
-- Cypher 用 (节点)-[关系]->(节点) 的图模式，直观得多：

-- 查询高血压的所有症状
MATCH (d:Disease {name: '高血压'})-[r:has_symptom]->(s:Symptom)
RETURN d.name, r.name, s.name

-- 查询同时治高血压和心脏病的药物（两跳查询）
MATCH (drug:Drug)-[:common_drug]-(d1:Disease {name: '高血压'}),
      (drug)-[:common_drug]-(d2:Disease {name: '心脏病'})
RETURN drug.name
```

### 3.3 本项目的 Neo4j 数据模型

**节点类型（7 种 Label）**：

```
┌──────────┬──────────┬────────────────────────────────┐
│  标签     │  数量    │  属性                           │
├──────────┼──────────┼────────────────────────────────┤
│ Disease  │ ~8,800   │ name, desc, cause, prevent,    │
│          │          │ cure_way, cure_lasttime,        │
│          │          │ cured_prob, easy_get,           │
│          │          │ cure_department                 │
│ Symptom  │ ~6,000+  │ name                            │
│ Drug     │ ~10,000+ │ name                            │
│ Food     │ ~5,000+  │ name                            │
│ Check    │ ~3,000+  │ name                            │
│ Department│ ~50+    │ name                            │
│ Producer │ ~10,000+ │ name                            │
└──────────┴──────────┴────────────────────────────────┘
```

**关系类型（10 种）**：

```
Disease ──[has_symptom]──▶ Symptom      疾病 → 症状
Disease ──[acompany_with]──▶ Disease    疾病 → 并发症
Disease ──[common_drug]──▶ Drug         疾病 → 常用药品
Disease ──[recommand_drug]──▶ Drug      疾病 → 推荐药品
Disease ──[need_check]──▶ Check         疾病 → 需要做的检查
Disease ──[do_eat]──▶ Food              疾病 → 宜吃的食物
Disease ──[no_eat]──▶ Food              疾病 → 忌吃的食物
Disease ──[recommand_eat]──▶ Food       疾病 → 推荐食谱
Disease ──[belongs_to]──▶ Department    疾病 → 所属科室
Producer ──[drugs_of]──▶ Drug           药厂 → 生产的药品
```

**可视化示例（一小块知识图谱）**：

```
                         ┌──────────┐
                         │   头痛    │
                         │ :Symptom │
                         └────┬─────┘
                              │ has_symptom
                              ▲
          ┌───────────────────┼───────────────────┐
          │ has_symptom       │              has_symptom
          │                   │                   │
    ┌─────┴──────┐    ┌──────┴──────┐    ┌───────┴──────┐
    │   高血压    │◀──▶│    糖尿病    │◀──▶│    心脏病     │
    │  :Disease  │    │  :Disease   │    │  :Disease    │
    └──┬───┬───┬─┘    └──┬────┬─────┘    └──┬─────┬─────┘
       │   │   │         │    │             │     │
       │   │   │  common_drug │  do_eat    │     │ common_drug
       ▼   │   ▼         ▼    ▼            ▼     ▼
  ┌────┐  │ ┌────┐  ┌────┐ ┌──────┐  ┌────┐  ┌────┐
  │硝苯│  │ │心电│  │二甲│ │苦瓜  │  │阿司│  │心电│
  │地平│  │ │图  │  │双胍│ │:Food │  │匹林│  │图  │
  │:Drug│ │:Check│ │:Drug│ └──────┘ │:Drug│ │:Check│
  └────┘  │ └────┘  └────┘          └────┘  └────┘
         │
    recommand_drug
         │
         ▼
    ┌────────┐
    │ 卡托普利│
    │  :Drug  │
    └────────┘
```

---

## 四、GraphRAG 工作原理——Neo4j 在项目中的完整工作流

### 4.1 什么是 GraphRAG？

**RAG（检索增强生成）** 的标准模式：

```
用户问题 → 向量检索（找相似文档）→ 把文档片断作为上下文 → LLM 生成答案
```

**GraphRAG** 的改进——用**知识图谱**代替向量检索：

```
用户问题 → 知识图谱检索（找关联实体和关系）→ 把结构化知识作为上下文 → LLM 生成答案
                  ↑
            这就是 Neo4j 的职责
```

### 4.2 两套管线对比

本项目实际上实现了两套检索管线，都用 Neo4j：

#### 管线 A（当前激活）：简单关键词检索

```python
# chat_deepseek_api.py - get_relevant_info()
def get_relevant_info(self, question):
    keyword = jieba.lcut(question)[0]  # 分词取第一个词
    query = """
        MATCH (n)
        WHERE n.name CONTAINS $keyword   # 模糊匹配
        RETURN n.name as name
        LIMIT 5
    """
    result = session.run(query, keyword=keyword)
    return [record for record in result]
```

**执行流程**：

```
用户问: "高血压有哪些症状？"
        │
        ▼
   jieba 分词: ["高血压", "有", "哪些", "症状"]
        │
        ▼
   提取关键词: "高血压"
        │
        ▼
   Neo4j Cypher: MATCH (n) WHERE n.name CONTAINS "高血压" RETURN n LIMIT 5
        │
        ▼
   检索结果: ["高血压", "高血压性心脏病", "高血压肾病", "妊娠高血压", ...]
        │
        ▼
   拼入 Prompt → DeepSeek 生成回答
```

**特点**：
- ✅ 简单快速，容错率高
- ❌ 只查实体名称，没利用图谱的**关系**结构
- ❌ 无法精确回答 "高血压用什么药？" 这类需要关系查询的问题

#### 管线 B（未接入 Web）：结构化关系检索

```python
# question_classifier.py → question_parser.py → answer_search.py

# 第一步：意图分类
classifier.classify("高血压有哪些症状？")
# → {"args": {"高血压": ["disease"]}, "question_types": ["disease_symptom"]}

# 第二步：生成 Cypher
parser.sql_transfer("disease_symptom", {"高血压": ["disease"]})
# → MATCH (m:Disease)-[r:has_symptom]->(n:Symptom)
#   WHERE m.name = '高血压'
#   RETURN m.name, r.name, n.name

# 第三步：格式化回答
searcher.answer_prettify("disease_symptom", [("高血压", "has_symptom", "头痛"), ...])
# → "高血压的症状包括：头痛；眩晕；心悸；..."
```

**执行流程**：

```
用户问: "高血压有哪些症状？"
        │
        ▼
   Aho-Corasick 实体抽取: {"高血压": ["disease"]}
        │
        ▼
   意图分类: disease_symptom（16种意图之一）
        │
        ▼
   生成精确 Cypher:
   MATCH (d:Disease {name: "高血压"})-[r:has_symptom]->(s:Symptom)
   RETURN d.name, r.name, s.name
        │
        ▼
   结构化结果:
   高血压 --[has_symptom]--> 头痛
   高血压 --[has_symptom]--> 眩晕
   高血压 --[has_symptom]--> 心悸
        │
        ▼
   模板化回答: "高血压的症状包括：头痛；眩晕；心悸；..."
```

**特点**：
- ✅ 精确利用图谱**关系**，回答更精准
- ✅ 16 种问题类型覆盖全面
- ❌ 没有流式输出
- ❌ 未接入 Flask Web 服务

### 4.3 为什么 GraphRAG 比纯 LLM 更好？

```
场景：病人问 "高血压用什么药？"

┌─────────────────────────────────────────────────────────┐
│ 纯 LLM (DeepSeek 直接回答)                              │
│                                                         │
│ → 可能输出：                                             │
│   "高血压常用药物有硝苯地平、卡托普利...（基于模型记忆）"    │
│   ❌ 可能过时、不准确、幻觉                               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ GraphRAG (Neo4j 检索 + LLM 生成)                        │
│                                                         │
│ → Neo4j 精确检索：                                       │
│   MATCH (d:Disease {name:'高血压'})-[r:common_drug]->    │
│         (n:Drug) RETURN n                                │
│ → 得到经过医学专家审核的结构化数据：                        │
│   {硝苯地平, 卡托普利, 缬沙坦, 氢氯噻嗪, ...}             │
│ → LLM 基于这些知识生成：                                  │
│   "高血压常用药物包括：硝苯地平、卡托普利...（基于知识图谱）" │
│   ✅ 准确、可溯源、无幻觉                                  │
└─────────────────────────────────────────────────────────┘
```

**三句话总结 GraphRAG 的本质**：

1. **图谱负责"记"**——把结构化知识存成节点和关系，保证准确
2. **LLM 负责"说"**——把检索到的结构化知识用自然语言表达出来
3. **两者分工**——检索保证准确，生成保证流畅

---

## 五、知识图谱构建管线（完整数据流）

```
                    ┌─────────────┐
                    │ 寻医问药网     │  jib.xywy.com
                    │ 11,000+ 疾病页│
                    └──────┬──────┘
                           │ urllib 爬取
                           ▼
                    ┌─────────────┐
                    │  MongoDB    │  原始 HTML + 结构化字段
                    │  data 库    │
                    └──────┬──────┘
                           │ build_data.py 清洗转换
                           │      + max_cut.py 双向最大匹配分词
                           ▼
                    ┌─────────────┐
                    │ medical.json│  每行一个疾病的完整 JSON
                    │             │  {name, desc, cause, symptom, drug, food...}
                    └──────┬──────┘
                           │ build_medicalgraph.py
                           │     读取并创建节点和关系
                           ▼
                    ┌─────────────┐
                    │   Neo4j     │
                    │ 图数据库     │
                    │             │
                    │ 7 种节点类型  │
                    │ 10 种关系类型 │
                    │ 4.4万节点    │
                    │ 30万关系边   │
                    └─────────────┘
```

**build_medicalgraph.py 核心逻辑**：

```python
class MedicalGraph:
    def read_nodes(self):
        # 读取 medical.json，提取 7 种实体
        # 存入 set 去重: Drugs, Foods, Checks, Departments, ...
        # 提取 12 种关系存入列表

    def create_diseases_nodes(self, disease_infos):
        # 创建 Disease 节点，带 8 个属性
        # CREATE (:Disease {name: '高血压', desc: '...', cause: '...', ...})

    def create_graphrels(self):
        # 创建关系边
        # MATCH (a:Disease {name: '高血压'}), (b:Symptom {name: '头痛'})
        # CREATE (a)-[r:has_symptom]->(b)
```

---

## 六、关键技术要点总结

| 层面 | 技术 | 核心要点 |
|------|------|----------|
| 前端 UI | React 18 | 组件化，函数式组件 + Hooks，`useState`/`useEffect` |
| 前端样式 | Tailwind CSS 3 | 原子类名，`bg-blue-500` / `rounded-lg` / `shadow-sm` |
| 前端通信 | Fetch + SSE | `response.body.getReader()` 手动解析流，增量更新 UI |
| 后端框架 | Flask | 轻量路由，`stream_with_context` 支持 SSE |
| 中文分词 | jieba | `lcut()` 精确模式分词，提取检索关键词 |
| 图数据库 | Neo4j | Cypher 查询语言，`(节点)-[关系]->(节点)` 图模式匹配 |
| 大模型 | DeepSeek API | 兼容 OpenAI 格式，`stream: true` 开启流式，温度参数 0.7 |
| RAG 模式 | GraphRAG | 知识图谱检索 → 拼入 Prompt → LLM 生成 |
| 意图分类 | Aho-Corasick | O(n) 多模式匹配，16 种医疗问题类型 |
| 数据清洗 | 双向最大匹配分词 | 正向 + 反向取单字词少的结果，用于合并病症名 |

---

## 参考资料

- [Neo4j 官方文档](https://neo4j.com/docs/)
- [Cypher 查询语言手册](https://neo4j.com/docs/cypher-manual/current/)
- [DeepSeek API 文档](https://platform.deepseek.com/api-docs/)
- [Flask SSE 实现](https://flask.palletsprojects.com/en/stable/patterns/streaming/)
- [jieba 中文分词](https://github.com/fxsjy/jieba)