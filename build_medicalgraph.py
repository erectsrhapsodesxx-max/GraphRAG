  #!/usr/bin/env python3
# coding: utf-8
# File: MedicalGraph.py
# 使用 neo4j 官方驱动 (替代已停止维护的 py2neo)

import os
import json
from neo4j import GraphDatabase


class MedicalGraph:
    def __init__(self):
        cur_dir = '/'.join(os.path.abspath(__file__).split('/')[:-1])
        self.data_path = os.path.join(cur_dir, 'data/medical.json')
        self.driver = GraphDatabase.driver(
            "bolt://localhost:7687",
            auth=("neo4j", "cjy00916")
        )

    def _run(self, query, **params):
        """执行 Cypher 查询"""
        with self.driver.session() as session:
            session.run(query, **params)

    '''读取文件'''
    def read_nodes(self):
        drugs, foods, checks, departments, producers, diseases, symptoms = [], [], [], [], [], [], []
        disease_infos = []
        rels_department, rels_noteat, rels_doeat, rels_recommandeat = [], [], [], []
        rels_commonddrug, rels_recommanddrug, rels_check, rels_drug_producer = [], [], [], []
        rels_symptom, rels_acompany, rels_category = [], [], []

        count = 0
        for data in open(self.data_path, encoding='utf-8'):
            disease_dict = {}
            count += 1
            print(count)
            data_json = json.loads(data)
            disease = data_json['name']
            disease_dict['name'] = disease
            diseases.append(disease)
            disease_dict['desc'] = ''
            disease_dict['prevent'] = ''
            disease_dict['cause'] = ''
            disease_dict['easy_get'] = ''
            disease_dict['cure_department'] = ''
            disease_dict['cure_way'] = ''
            disease_dict['cure_lasttime'] = ''
            disease_dict['symptom'] = ''
            disease_dict['cured_prob'] = ''

            if 'symptom' in data_json:
                symptoms += data_json['symptom']
                for symptom in data_json['symptom']:
                    rels_symptom.append([disease, symptom])

            if 'acompany' in data_json:
                for acompany in data_json['acompany']:
                    rels_acompany.append([disease, acompany])

            if 'desc' in data_json:
                disease_dict['desc'] = data_json['desc']
            if 'prevent' in data_json:
                disease_dict['prevent'] = data_json['prevent']
            if 'cause' in data_json:
                disease_dict['cause'] = data_json['cause']
            if 'get_prob' in data_json:
                disease_dict['get_prob'] = data_json['get_prob']
            if 'easy_get' in data_json:
                disease_dict['easy_get'] = data_json['easy_get']

            if 'cure_department' in data_json:
                cure_department = data_json['cure_department']
                if len(cure_department) == 1:
                    rels_category.append([disease, cure_department[0]])
                if len(cure_department) == 2:
                    big, small = cure_department[0], cure_department[1]
                    rels_department.append([small, big])
                    rels_category.append([disease, small])
                disease_dict['cure_department'] = cure_department
                departments += cure_department

            if 'cure_way' in data_json:
                disease_dict['cure_way'] = data_json['cure_way']
            if 'cure_lasttime' in data_json:
                disease_dict['cure_lasttime'] = data_json['cure_lasttime']
            if 'cured_prob' in data_json:
                disease_dict['cured_prob'] = data_json['cured_prob']

            if 'common_drug' in data_json:
                common_drug = data_json['common_drug']
                for drug in common_drug:
                    rels_commonddrug.append([disease, drug])
                drugs += common_drug

            if 'recommand_drug' in data_json:
                recommand_drug = data_json['recommand_drug']
                drugs += recommand_drug
                for drug in recommand_drug:
                    rels_recommanddrug.append([disease, drug])

            if 'not_eat' in data_json:
                not_eat = data_json['not_eat']
                for _not in not_eat:
                    rels_noteat.append([disease, _not])
                foods += not_eat
                do_eat = data_json['do_eat']
                for _do in do_eat:
                    rels_doeat.append([disease, _do])
                foods += do_eat
                recommand_eat = data_json['recommand_eat']
                for _recommand in recommand_eat:
                    rels_recommandeat.append([disease, _recommand])
                foods += recommand_eat

            if 'check' in data_json:
                check = data_json['check']
                for _check in check:
                    rels_check.append([disease, _check])
                checks += check

            if 'drug_detail' in data_json:
                drug_detail = data_json['drug_detail']
                producer = [i.split('(')[0] for i in drug_detail]
                rels_drug_producer += [[i.split('(')[0], i.split('(')[-1].replace(')', '')] for i in drug_detail]
                producers += producer

            disease_infos.append(disease_dict)

        return (set(drugs), set(foods), set(checks), set(departments), set(producers),
                set(symptoms), set(diseases), disease_infos,
                rels_check, rels_recommandeat, rels_noteat, rels_doeat, rels_department,
                rels_commonddrug, rels_drug_producer, rels_recommanddrug,
                rels_symptom, rels_acompany, rels_category)

    '''创建简单节点（通过 Cypher MERGE）'''
    def create_node(self, label, nodes):
        count = 0
        total = len(nodes)
        with self.driver.session() as session:
            for node_name in nodes:
                session.run(
                    f"MERGE (n:{label} {{name: $name}})",
                    name=node_name
                )
                count += 1
                print(count, total)

    '''创建疾病节点（带属性）'''
    def create_diseases_nodes(self, disease_infos):
        count = 0
        with self.driver.session() as session:
            for d in disease_infos:
                session.run("""
                    MERGE (n:Disease {name: $name})
                    SET n.desc = $desc,
                        n.prevent = $prevent,
                        n.cause = $cause,
                        n.easy_get = $easy_get,
                        n.cure_lasttime = $cure_lasttime,
                        n.cure_department = $cure_department,
                        n.cure_way = $cure_way,
                        n.cured_prob = $cured_prob
                """,
                    name=d['name'], desc=d.get('desc', ''),
                    prevent=d.get('prevent', ''), cause=d.get('cause', ''),
                    easy_get=d.get('easy_get', ''), cure_lasttime=d.get('cure_lasttime', ''),
                    cure_department=d.get('cure_department', ''),
                    cure_way=d.get('cure_way', ''), cured_prob=d.get('cured_prob', '')
                )
                count += 1
                print(count)

    '''创建图谱节点'''
    def create_graphnodes(self):
        (Drugs, Foods, Checks, Departments, Producers, Symptoms, Diseases,
         disease_infos, rels_check, rels_recommandeat, rels_noteat, rels_doeat,
         rels_department, rels_commonddrug, rels_drug_producer, rels_recommanddrug,
         rels_symptom, rels_acompany, rels_category) = self.read_nodes()

        self.create_diseases_nodes(disease_infos)
        self.create_node('Drug', Drugs)
        print(len(Drugs))
        self.create_node('Food', Foods)
        print(len(Foods))
        self.create_node('Check', Checks)
        print(len(Checks))
        self.create_node('Department', Departments)
        print(len(Departments))
        self.create_node('Producer', Producers)
        print(len(Producers))
        self.create_node('Symptom', Symptoms)

    '''创建实体关系边'''
    def create_graphrels(self):
        (Drugs, Foods, Checks, Departments, Producers, Symptoms, Diseases,
         disease_infos, rels_check, rels_recommandeat, rels_noteat, rels_doeat,
         rels_department, rels_commonddrug, rels_drug_producer, rels_recommanddrug,
         rels_symptom, rels_acompany, rels_category) = self.read_nodes()

        self.create_relationship('Disease', 'Food', rels_recommandeat, 'recommand_eat')
        self.create_relationship('Disease', 'Food', rels_noteat, 'no_eat')
        self.create_relationship('Disease', 'Food', rels_doeat, 'do_eat')
        self.create_relationship('Department', 'Department', rels_department, 'belongs_to')
        self.create_relationship('Disease', 'Drug', rels_commonddrug, 'common_drug')
        self.create_relationship('Producer', 'Drug', rels_drug_producer, 'drugs_of')
        self.create_relationship('Disease', 'Drug', rels_recommanddrug, 'recommand_drug')
        self.create_relationship('Disease', 'Check', rels_check, 'need_check')
        self.create_relationship('Disease', 'Symptom', rels_symptom, 'has_symptom')
        self.create_relationship('Disease', 'Disease', rels_acompany, 'acompany_with')
        self.create_relationship('Disease', 'Department', rels_category, 'belongs_to')

    '''创建实体关联边（安全参数化查询）'''
    def create_relationship(self, start_node, end_node, edges, rel_type):
        # 去重
        unique_edges = list(set(['###'.join(edge) for edge in edges]))
        total = len(unique_edges)
        count = 0
        with self.driver.session() as session:
            for edge_str in unique_edges:
                p, q = edge_str.split('###')
                try:
                    session.run(
                        f"MATCH (a:{start_node} {{name: $p}}), (b:{end_node} {{name: $q}}) "
                        f"MERGE (a)-[r:{rel_type}]->(b)",
                        p=p, q=q
                    )
                    count += 1
                    print(rel_type, count, total)
                except Exception as e:
                    print(e)

    '''导出数据到词典文件'''
    def export_data(self):
        (Drugs, Foods, Checks, Departments, Producers, Symptoms, Diseases,
         _, _, _, _, _, _, _, _, _, _, _, _, _) = self.read_nodes()

        for fname, items in [
            ('drug.txt', Drugs), ('food.txt', Foods), ('check.txt', Checks),
            ('department.txt', Departments), ('producer.txt', Producers),
            ('symptoms.txt', Symptoms), ('disease.txt', Diseases)
        ]:
            with open(fname, 'w', encoding='utf-8') as f:
                f.write('\n'.join(items))

    def close(self):
        self.driver.close()


if __name__ == '__main__':
    handler = MedicalGraph()
    print("step1: 导入图谱节点中")
    handler.create_graphnodes()
    print("step2: 导入图谱边中")
    handler.create_graphrels()
    handler.close()