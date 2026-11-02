"""Freeze 100 independently sourced Chinese encyclopedia introductions for retrieval evaluation.

This is a source-discovery script, not part of the serving path. The checked-in
corpus and manifest make later runs offline and reproducible. Wikimedia text is
attributed in every file; questions are intentionally simple entity-retrieval
probes and must not be reported as answer-quality results.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests


# A few titles contain spaces, so list them explicitly instead of splitting.
TITLES = [
    "艾萨克·牛顿", "阿尔伯特·爱因斯坦", "伽利略·伽利莱", "查尔斯·达尔文", "格雷戈尔·孟德尔",
    "迈克尔·法拉第", "詹姆斯·克拉克·麦克斯韦", "尼古拉·特斯拉", "理查德·费曼", "艾伦·图灵",
    "牛顿运动定律", "相对论", "量子力学", "电磁学", "热力学", "光电效应", "引力波", "黑洞", "超导体", "半导体",
    "细胞", "DNA", "RNA", "基因", "蛋白质", "生态系统", "自然选择", "疫苗", "光合作用", "线粒体",
    "计算机科学", "操作系统", "互联网", "数据库", "人工智能", "机器学习", "编程语言", "计算机网络", "密码学", "分布式计算",
    "微积分", "线性代数", "概率论", "统计学", "图论", "数论", "集合论", "拓扑学", "几何学", "算法",
    "太平洋", "大西洋", "印度洋", "喜马拉雅山脉", "亚马逊河", "撒哈拉沙漠", "青藏高原", "南极洲", "地中海", "台湾海峡",
    "太阳", "月球", "地球", "火星", "木星", "土星", "金星", "水星", "海王星", "银河系",
    "工业革命", "法国大革命", "文艺复兴", "启蒙运动", "第一次世界大战", "第二次世界大战", "丝绸之路", "秦朝", "唐朝", "宋朝",
    "抗生素", "青霉素", "糖尿病", "高血压", "免疫系统", "病毒", "细菌", "流行病学", "公共卫生", "临床试验",
    "蒸汽机", "内燃机", "电动机", "发电机", "飞机", "铁路", "卫星", "机器人", "太阳能电池", "锂离子电池",
    "红楼梦", "西游记", "三国演义", "水浒传", "哈姆雷特", "唐吉诃德", "战争与和平", "百年孤独", "诗经", "论语",
    "元素周期表", "原子", "分子", "化学反应", "催化剂", "聚合物", "碳", "氧",
    "水", "氢", "氮", "宇宙", "地质学", "地震", "海啸", "火山", "生态学", "气候变化", "生物多样性", "太阳系",
]


def main() -> None:
    root = Path(__file__).parent / "fixtures"
    dest = root / "corpus_100"
    dest.mkdir(exist_ok=True)
    manifest = []
    session = requests.Session()
    session.headers["User-Agent"] = "ZhiyiResearchEval/1.0 (educational local corpus; contact: https://github.com/1084217636)"
    for title in TITLES:
        if len(manifest) >= 100:
            break
        url = "https://zh.wikipedia.org/api/rest_v1/page/summary/" + quote(title)
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            page = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"skip {title}: {type(exc).__name__}")
            continue
        text = re.sub(r"\s+", " ", page.get("extract", "")).strip()
        if page.get("type") != "standard" or len(text) < 85:
            print(f"skip {title}: short or disambiguation")
            continue
        canonical = page.get("title", title)
        if any(row["title"] == canonical for row in manifest):
            continue
        source_id = f"wiki-{len(manifest) + 1:03d}"
        source_url = page.get("content_urls", {}).get("desktop", {}).get("page", url)
        (dest / f"{source_id}.md").write_text(
            f"# {canonical}\n\n{text}\n\n来源：维基百科贡献者，{source_url}\n"
            "许可：CC BY-SA 4.0，https://creativecommons.org/licenses/by-sa/4.0/\n",
            encoding="utf-8",
        )
        manifest.append({"source_id": source_id, "title": canonical, "source_url": source_url,
                         "page_id": page.get("pageid")})
        print(f"{len(manifest):03d} {canonical}")
        time.sleep(0.25)
    if len(manifest) != 100:
        raise RuntimeError(f"Only {len(manifest)} suitable independent pages; add topics and rerun")
    (root / "corpus_100_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gold = [{"question": f"请介绍{row['title']}的定义和主要背景。",
             "relevant_doc_ids": [row["source_id"]], "source_title": row["title"]}
            for row in manifest]
    (root / "gold" / "retrieval_100docs.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote 100 documents and 100 source-ID retrieval probes")


if __name__ == "__main__":
    main()
