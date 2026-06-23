
import os
import sys
import requests
import logging
from dotenv import load_dotenv
from neo4j import GraphDatabase

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.getLogger("neo4j").setLevel(logging.ERROR)

class GraphRAGHandler:
    def __init__(self):
        load_dotenv(encoding='utf-8')

        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("请在 .env 文件中设置 DEEPSEEK_API_KEY")

        self.uri = "bolt://localhost:7687"
        self.user = "neo4j"
        self.password = "cjy00916"

        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def extract_subgraph(self, question):
        keyword = question.split()[0]

        query = '''
        MATCH path=(e1)-[r]-(e2)
        WHERE e1.name CONTAINS $keyword OR e2.name CONTAINS $keyword
        RETURN e1.name AS src, type(r) AS rel, e2.name AS tgt
        LIMIT 10
        '''
        try:
            with self.driver.session() as session:
                result = session.run(query, keyword=keyword)
                triples = [(record["src"], record["rel"], record["tgt"]) for record in result]
                return triples
        except Exception as e:
            #print(f"子图查询失败: {e}")
            return []

    def format_context(self, triples):
        if not triples:
            return "无相关医疗知识。"
        return "\n".join([f"{h} --[{r}]--> {t}" for h, r, t in triples])

    def get_answer(self, question):
        triples = self.extract_subgraph(question)
        context = self.format_context(triples)

        prompt = f"""你是一个专业的医疗问答助手。
使用以下知识图谱中的结构化医学知识来回答用户的问题。如果找不到答案，请说明你不知道。

知识图谱内容：
{context}

问题：{question}

请提供专业、准确、简洁的回答：
"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是一个专业的医疗问答助手。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7
        }

        try:
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
            else:
                return f"API调用失败: {response.text}"
        except Exception as e:
            return f"调用API出错: {e}"

    def __del__(self):
        if hasattr(self, 'driver'):
            self.driver.close()

def main():
    try:
        handler = GraphRAGHandler()
        print("\n=== 医疗知识问答系统（GraphRAG 模式）===")
        print("输入 'quit' 或 'exit' 退出程序")

        while True:
            question = input("\n请输入您的问题: ").strip()
            if question.lower() in ['quit', 'exit']:
                print("感谢使用，再见！")
                break
            if not question:
                print("问题不能为空，请重新输入")
                continue

            # print("\n思考中...")
            answer = handler.get_answer(question)
            print(f"\n💬 回答: {answer}")

    except Exception as e:
        print(f"程序运行出错: {str(e)}")

if __name__ == "__main__":
    main()
