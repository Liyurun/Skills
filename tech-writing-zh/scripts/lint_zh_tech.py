#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tech-writing-zh 扫描脚本（只读）。

扫描中文技术稿（Markdown 或纯文本），报告可能需要处理的地方：AI 套话、
产品名大小写、术语混用、中英混排和标点不统一，以及稿件里的【待补】【待确认】。
跳过 front matter、代码块、行内代码、公式、HTML 注释、URL 和链接地址。
不修改任何文件，只用 Python 标准库（Python 3.8 及以上）。

结果只是线索，需要逐条判断，不要照单全改。

用法：
    python3 lint_zh_tech.py <文件或目录> [更多路径 ...] [--domain auto|general|data-ai|fiction]
                            [--format text|md] [--max N]

范围：general 只查通用模式（A–E 和中文排版）；data-ai 另外查 T 类模式、
产品名大小写和术语混用；fiction 查叙述文字（跳过引号里的对白）和 N 类模式；
auto（默认）按内容判断。
"""

import argparse
import os
import re
import sys
from collections import Counter

TEXT_EXTS = {".md", ".markdown", ".mdx", ".txt"}
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}
CJK = "㐀-䶿一-鿿"

# ---------------------------------------------------------------------------
# 套话规则。可以按团队习惯增删。
# 格式：(编号, 名称, 正则, 提示, 范围)
# 范围：any 任意位置；para_start 段落开头 40 个字以内；para_end 段落最后一句
# 编号对应 references/patterns.md
# ---------------------------------------------------------------------------
PHRASE_RULES = [
    ("A1", "时代背景开场",
     r"随着[^。！？\n]{0,24}(发展|普及|兴起|进步|演进|到来)"
     r"|在(当今|当下|如今)[^。！？\n]{0,16}(时代|浪潮|背景)"
     r"|数字化转型的(浪潮|大潮|背景)"
     r"|在[^。！？\n]{0,12}(大数据|人工智能|AI|数字化|数智化)时代"
     r"|近(几|些)?年来",
     "从具体问题、场景、数字或结论开头（用原文已有的内容）", "para_start"),
    ("A3", "展望式结尾",
     r"让我们拭目以待|未来可期|前景(十分|非常)?广阔|未来已来|充满(无限)?(想象|可能)"
     r"|必将(在|为|成为|迎来|带来)|未来，随着",
     "删掉，停在最后一个具体结论、限制或待解决的问题上", "any"),
    ("A4", "预告和自我描述",
     r"本文将|本章将(介绍|讲解|带)|接下来，?(让)?我们|下面，?(让)?我们(一起)?来"
     r"|下一(节|章|篇)(文章)?(我们)?(将|会)|接下来要解决的(是|问题)"
     r"|让我们(一起)?(来)?(深入|走进|揭开|探索|看看|了解)|带(大家|你|读者)(一起)?(深入|了解|走进|认识)",
     "删掉预告，直接进入内容；书稿的导读要写具体内容", "any"),
    ("A5", "段尾总结句",
     r"^(由此可见|可见|总之|总的来说|一言以蔽之)|这(充分|很好地|有力地)(体现|说明|证明|展示)了",
     "删掉段尾的抽象总结，或把结论并进前一句", "para_end"),
    ("B1", "二元对照",
     r"不是[^。！？；\n]{1,30}?[，,]?\s*而是|不(仅|只)(仅)?是[^。！？；\n]{1,30}?[，,]\s*(更|而|也)是"
     r"|与其说[^。！？；\n]{1,30}?不如说|不在于[^。！？；\n]{1,30}?而在于",
     "A 是虚设的靶子时直接说 B；偶尔一次正常，成串出现才是问题", "any"),
    ("B2", "意义拔高",
     r"标志着[^。！？\n]{0,20}(进入|迈入|开启)|奠定了(坚实的?)?基础|开启了[^。！？\n]{0,12}新篇章"
     r"|新纪元|里程碑|(更|而)是一场[^。！？\n]{0,12}(变革|革命)",
     "保留事实，删掉拔高；需要说明重要性时用原文里的后果", "any"),
    ("B3", "金句和重锤词",
     r"本质上|归根结底|说到底|底层逻辑|是新时代的(石油|电力|生产资料|基础设施)|即(权力|产品|一切)",
     "换成具体主张；全篇最多留一两处", "any"),
    ("B4", "设问自答和假坦白",
     r"那么问题来了|原因(其实)?很简单|答案(其实)?很简单|说实话，|坦白(讲|说)，|为什么(会)?这样(呢)?？"
     r"|试想一下|想象一下|这很正常|别担心",
     "直接陈述", "any"),
    ("B5", "冒号揭示和解读腔",
     r"(关键|核心|秘诀|答案|真相|重点)(就)?(在于|是|只有一个)[：:]|这一点(非常|十分|尤为)?重要"
     r"|需要强调的是|换句话说|换言之",
     "写成普通陈述；观点清楚就删掉解读", "any"),
    ("C1", "宣传腔和汇报腔",
     r"赋能|助力|打造|闭环|抓手|降本增效|全链路|一站式|深度融合|全方位|多维度|颠覆|革命性|重塑|引领|范式"
     r"|持续深化|全面推进|积极探索|取得(了)?显著成效|高质量发展|飞轮|护城河|北极星|赛道",
     "换成具体动作和结果；正常的技术用法（如全链路追踪）可以忽略", "any"),
    ("C2", "路标词和讲义腔",
     r"值得(注意|一提|关注)的是|需要(注意|指出|说明)的是|不难(发现|看出)|众所周知|毋庸置疑|显而易见"
     r"|不可否认|总而言之|综上所述|简单来说|划重点|敲黑板|你会发现|相信(你|大家|读者)已经"
     r"|拆一拆|盘一盘|捋一捋|聊一聊|说白了",
     "删掉路标，直接说内容", "any"),
    ("C3", "模糊归因",
     r"业(界|内)(普遍)?认为|(有|大量)?研究(表明|显示)|据统计|专家(们)?(认为|指出|表示)|有数据显示|(大量)?实践证明",
     "写明出处，或留【待补：出处】", "any"),
    ("E1", "对话残留",
     r"希望(这篇文章|本文|以上内容|这些内容)?(能)?对(你|您|大家)有(所)?帮助"
     r"|如果(你|您)(还)?有(任何)?(其他)?(问题|疑问)|欢迎随时(提问|交流)|好的，以下是|以下是为(你|您)"
     r"|作为(一个)?(AI|人工智能|语言模型)|截至我的(知识|训练)",
     "去掉对话包装，保留内容", "any"),
    ("T4", "夸大和绝对化",
     r"完美(地)?(解决|支持|兼容|实现|适配)|彻底(解决|消除|告别|根治)|零(成本|运维|门槛|代码|延迟)"
     r"|无缝(地)?(集成|对接|衔接|迁移|切换)|(业界|行业)领先|遥遥领先|一键(搞定|解决|完成)|一行代码",
     "删掉修辞夸张；可验证的断言保留，请作者给条件或数据", "any"),
    ("T5", "时效表述",
     r"(目前|当前|现在)最(新|强|先进|主流|流行|好|快)|最先进的|最新(的)?(版本|模型|技术|进展|特性|研究|成果)"
     r"|今年|去年|明年|近期|最近(一|几|两|三)?(年|个月|周)|近(几|些)?年来",
     "加上时间点或版本；没有就留【待确认】", "any"),
    ("D4", "标题党",
     r"一文(读懂|看懂|搞懂|讲透|带你|吃透)|保姆级|万字长文|干货(满满)?|全网最(全|细|详细)|建议收藏|深度好文",
     "标题写清讲什么；书稿一律去掉", "any"),
    ("B10", "句尾虚假深度补语",
     r"[，,](充分|深刻|有力地|也)?(体现|彰显|折射|凸显|映射|昭示)(出|了)",
     "保留事实，删掉补语", "any"),
    ("B11", "戏剧化揭露",
     r"揭开[^。！？\n]{0,20}面纱|撕下[^。！？\n]{0,8}面具|遮羞布|剥开[^。！？\n]{0,8}外衣|真相(是|就是)[：:，]",
     "删掉揭露动作，直接给事实", "any"),
    ("B12", "极值判断",
     r"最(可怕|讽刺|残酷|吊诡|要命)的(是|地方)|真正的(难点|问题|挑战|关键)在于|讽刺的是|最关键的(地方|一点)在于",
     "去掉极值框架，直接说那件事；成簇出现才改", "any"),
    ("B13", "和不存在的人辩论",
     r"有人(可能|也许)?会(说|问|认为|觉得)|我(并)?不是(说|要说)|你可能会(想|问|觉得)|一个很自然的想法是",
     "文中没人这样主张时，删掉假想的反对意见", "any"),
    ("B14", "无动词短句排列",
     r"(没有[^。，！？\n]{1,6}。){2,}|(不是[^。，！？\n]{1,6}。){2,}",
     "合成一个完整的句子", "any"),
    ("B15", "虚假范围",
     r"从[^。，！？\n]{1,15}到[^。，！？\n]{1,15}[，、]\s*从[^。，！？\n]{1,15}到",
     "直接列出实际涉及的几项", "any"),
    ("C5", "绕道动词",
     r"扮演(着|了)?[^。！？\n]{0,10}角色|发挥(着|了)[^。！？\n]{0,8}作用|起(到)?(了)?[^。！？\n]{0,8}的作用|作为[^。！？\n]{0,10}而存在",
     "换成“是”“用于”“负责”等直接的动词", "any"),
    ("C6", "模糊关联",
     r"(密切|息息)相关|紧密相连|密不可分",
     "写出是什么关系；原文没说就留【待确认】；成簇才改", "any"),
    ("C8", "自我标榜的质量声明",
     r"经过(充分|反复|大量)?(的)?验证的|这不是口号|行之有效的|久经考验",
     "写出验证的事实，没有就删掉修饰或留【待补】", "any"),
    # 小说叙事（只在 fiction 范围检查）
    ("N1", "叙述者说破主题",
     r"(终于|这才|一下子)(明白|懂得|意识到)|或许，?这就是|人生就是这样|原来，?所谓|这就是成长",
     "删掉总结句，让事件自己站着", "any"),
    ("N2", "环境当心理镜子",
     r"仿佛(连)?[^。！？\n]{0,8}(在)?为[^。！？\n]{0,6}(哭泣|哀悼|叹息|欢呼|落泪)|连(天空|老天|天)都[^。！？\n]{0,6}(哭|阴沉|落泪)",
     "删掉“仿佛在为……”这层对应", "any"),
    ("N3", "网文式神态套话",
     r"空气(仿佛|似乎)?(凝固|凝结)|时间(仿佛|似乎)?(静止|停止|凝固)|嘴角(勾起|扬起|微微上扬)|"
     r"(眼|眼中|眼里|眼底)闪过一丝|不易察觉|一抹(笑|笑意|弧度|苦笑)|一字一顿|低沉而(有)?磁性",
     "换成平实的写法或删掉", "any"),
    ("N4", "情绪只靠身体反应",
     r"心跳漏了一拍|心跳(加速|如鼓|骤停)|喉咙(发紧|一紧)|手心(沁出|冒出)?(冷)?汗|指节(泛白|发白)|呼吸一滞|深吸一口气",
     "一段里成簇出现时留一处最具体的", "any"),
    ("N6", "叙述者解释人物心理",
     r"(他|她)(知道|明白)，?(这一次|从此|再也)|笑容里(藏着|带着)",
     "删掉心理解释，留动作", "any"),
]


# 只在数据/AI 领域范围检查的规则
DOMAIN_ONLY = {"T1", "T4", "T5", "TY1"}
# 只在小说叙事范围检查的规则
FICTION_ONLY = {"N1", "N2", "N3", "N4", "N5", "N6"}
# 小说叙事范围不适用的通用规则
FICTION_SKIP = {"A1", "C3", "C4", "D2", "TY2", "TY4", "TY6"} | DOMAIN_ONLY
FICTION_HINT_RE = re.compile(r"[他她](说|问|道|想|笑|喊|看着|点了点头|摇了摇头|低声|转身|沉默|走进)|”[，。]?[他她]")
DIALOGUE_RE = re.compile("\u201c[^\u201d\n]*\u201d")
SIMILE_RE = re.compile(r"仿佛|宛如|犹如|如同|好似")
DOMAIN_HINT_RE = re.compile(
    r"大数据|数据仓库|数仓|数据湖|湖仓|大模型|大语言模型|机器学习|深度学习|神经网络|向量|算法|训练|推理"
    r"|(?<![A-Za-z])(AI|LLM|SQL|ETL|GPU|RAG|Agent)(?![A-Za-z])")

INTENSIFIER_RE = re.compile(
    r"(显著|大幅|极大)(地)?(提升|提高|降低|减少|改善|增强|优化)|非常(强大|高效|重要)|至关重要|极其重要")
NUMBER_RE = re.compile(r"[0-9０-９]|[一二两三四五六七八九十百千万]+(倍|成|个百分点)|一半")

# ---------------------------------------------------------------------------
# 产品名和缩写的正文写法。第二项为空时按正确写法做不区分大小写的匹配。
# 只检查正文；代码块和行内代码已被跳过。
# ---------------------------------------------------------------------------
TERMS = [
    ("Hadoop", None), ("HDFS", None), ("MapReduce", r"map\s*reduce"), ("Hive", None),
    ("HBase", None), ("Spark", None), ("PySpark", None), ("Flink", None), ("Kafka", None),
    ("Pulsar", None), ("RocketMQ", None), ("ZooKeeper", r"zoo\s*keeper"), ("Presto", None),
    ("Trino", None), ("Impala", None), ("ClickHouse", r"click\s*house"),
    ("StarRocks", r"star\s*rocks"), ("Elasticsearch", r"elastic\s*search"),
    ("OpenSearch", None), ("Redis", None), ("MySQL", None), ("PostgreSQL", None),
    ("MongoDB", r"mongo\s*db"), ("TiDB", None), ("OceanBase", r"ocean\s*base"),
    ("Iceberg", None), ("Hudi", None), ("Delta Lake", r"delta\s*lake"), ("Paimon", None),
    ("Parquet", None), ("Avro", None), ("DuckDB", r"duck\s*db"), ("Polars", None),
    ("Airflow", None), ("DolphinScheduler", r"dolphin\s*scheduler"), ("DataX", None),
    ("SeaTunnel", r"sea\s*tunnel"), ("Debezium", None), ("BigQuery", r"big\s*query"),
    ("Databricks", None), ("Redshift", None), ("MaxCompute", r"max\s*compute"),
    ("Kubernetes", None), ("Docker", None), ("PyTorch", None), ("TensorFlow", r"tensor\s*flow"),
    ("Keras", None), ("scikit-learn", None), ("XGBoost", None), ("LightGBM", None),
    ("NumPy", None), ("Jupyter", None), ("MLflow", None), ("Hugging Face", r"hugging\s*face"),
    ("LangChain", r"lang\s*chain"), ("LlamaIndex", r"llama\s*index"), ("vLLM", None),
    ("SGLang", None), ("Ollama", None), ("DeepSpeed", r"deep\s*speed"), ("ONNX", None),
    ("CUDA", None), ("NVIDIA", None), ("Milvus", None), ("Faiss", None), ("pgvector", None),
    ("OpenAI", r"open\s*ai"), ("ChatGPT", r"chat\s*gpt"), ("DeepSeek", r"deep\s*seek"),
    ("Qwen", None), ("GitHub", None), ("GitLab", None), ("JavaScript", r"java\s*script"),
    ("TypeScript", r"type\s*script"), ("macOS", None),
    ("SQL", None), ("JSON", None), ("YAML", None), ("API", None), ("SDK", None),
    ("GPU", None), ("CPU", None), ("LLM", None), ("RAG", None), ("ETL", None),
    ("OLAP", None), ("OLTP", None), ("QPS", None), ("LoRA", None), ("RLHF", None),
]
TERM_LB = r"(?<![A-Za-z0-9_\-./@])"
TERM_LA = r"(?![A-Za-z0-9_\-]|\.[A-Za-z0-9])"

# ---------------------------------------------------------------------------
# 同一概念的不同叫法：(概念, 中文叫法, 英文写法)
# ---------------------------------------------------------------------------
VARIANT_GROUPS = [
    ("大语言模型",
     [("大模型", r"大模型"), ("大语言模型", r"大语言模型")],
     [("LLM", r"(?<![A-Za-z])LLMs?(?![A-Za-z])")]),
    ("湖仓一体",
     [("湖仓一体", r"湖仓一体")],
     [("Lakehouse", r"(?<![A-Za-z])Lakehouse"), ("LakeHouse", r"(?<![A-Za-z])LakeHouse"),
      ("lakehouse", r"(?<![A-Za-z])lakehouse"), ("Lake House", r"(?<![A-Za-z])Lake\s+House")]),
    ("智能体",
     [("智能体", r"智能体")],
     [("Agent", r"(?<![A-Za-z])(?:AI\s*)?[Aa]gents?(?![A-Za-z])")]),
    ("提示词",
     [("提示词", r"提示词"), ("提示语", r"提示语")],
     [("Prompt", r"(?<![A-Za-z])[Pp]rompts?(?![A-Za-z])")]),
    ("检索增强生成",
     [("检索增强生成", r"检索增强生成")],
     [("RAG", r"(?<![A-Za-z])RAG(?![A-Za-z])")]),
    ("微调",
     [("微调", r"微调"), ("精调", r"精调")],
     [("fine-tuning", r"(?<![A-Za-z])[Ff]ine[\s-]?[Tt]un(?:e|ed|ing)(?![A-Za-z])")]),
    ("Kubernetes",
     [],
     [("Kubernetes", r"(?<![A-Za-z])Kubernetes(?![A-Za-z])"),
      ("K8s", r"(?<![A-Za-z0-9])[Kk]8[Ss](?![A-Za-z0-9])")]),
    ("token",
     [("词元", r"词元")],
     [("token", r"(?<![A-Za-z])[Tt]okens?(?![A-Za-z])")]),
]

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s<>\"'）)】，。；]+")
LINK_TARGET_RE = re.compile(r"\]\(([^)\n]*)\)")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]*>")
INLINE_MATH_RE = re.compile(r"\$(?!\s)[^$\n]+?(?<!\s)\$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s+")
BOLD_LABEL_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s+\*\*[^*\n]{1,24}\*\*\s*[：:]")
BOLD_RE = re.compile(r"\*\*[^*\n]+?\*\*")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕]")
ARROW_RE = re.compile("[←-⇿]")
MARK_RE = re.compile(r"【(待补|待确认)[^】]*】")
UNSPACED_RE = re.compile("(?=[%s][A-Za-z0-9]|[A-Za-z0-9][%s])" % (CJK, CJK))
SPACED_RE = re.compile("(?=[%s] [A-Za-z0-9]|[A-Za-z0-9] [%s])" % (CJK, CJK))
HALF_PUNCT_RE = re.compile("[%s](?:[,;!?]|:(?!//))|[A-Za-z0-9)](?:[,;])(?=[%s])" % (CJK, CJK))
SINGLE_DASH_RE = re.compile("(?<!—)[—–](?!—)")
DOUBLE_HYPHEN_RE = re.compile("[%s]\\s*--\\s*[%s]" % (CJK, CJK))
PLURAL_RE = re.compile(r"(?<![A-Za-z])([A-Z]{2,}s)(?![A-Za-z])")
CJK_RE = re.compile("[%s]" % CJK)
SENTENCE_END_RE = re.compile(r"(?<=[。！？])")


def blank_span(s, start, end):
    return s[:start] + " " * (end - start) + s[end:]


def mask_inline(line):
    """把行内代码、公式、URL、链接地址和 HTML 标签换成等长空格，保持列号不变。"""
    for regex in (INLINE_CODE_RE, INLINE_MATH_RE, URL_RE, HTML_TAG_RE):
        for m in list(regex.finditer(line)):
            line = blank_span(line, m.start(), m.end())
    for m in list(LINK_TARGET_RE.finditer(line)):
        line = blank_span(line, m.start(1), m.end(1))
    return line


def mask_document(lines):
    """跳过 front matter、代码块、公式块和 HTML 注释，返回与原文等长的行列表。"""
    masked = []
    in_front = bool(lines) and lines[0].strip() == "---"
    fence = None
    in_math = False
    in_comment = False
    for idx, line in enumerate(lines):
        if in_front:
            masked.append(" " * len(line))
            if idx > 0 and line.strip() in ("---", "..."):
                in_front = False
            continue
        if fence:
            masked.append(" " * len(line))
            m = FENCE_RE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and line.strip() == m.group(1).strip():
                fence = None
            continue
        m = FENCE_RE.match(line)
        if m:
            fence = m.group(1)
            masked.append(" " * len(line))
            continue
        if line.strip() == "$$":
            in_math = not in_math
            masked.append(" " * len(line))
            continue
        if in_math:
            masked.append(" " * len(line))
            continue
        out = line
        if in_comment:
            end = out.find("-->")
            if end == -1:
                masked.append(" " * len(line))
                continue
            out = " " * (end + 3) + out[end + 3:]
            in_comment = False
        while True:
            start = out.find("<!--")
            if start == -1:
                break
            end = out.find("-->", start + 4)
            if end == -1:
                out = out[:start] + " " * (len(out) - start)
                in_comment = True
                break
            out = blank_span(out, start, end + 3)
        masked.append(mask_inline(out))
    return masked


def read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc)
        except UnicodeDecodeError:
            continue
    return None, None


def snippet(line, start, end, width=40):
    left = max(0, start - 10)
    right = min(len(line), max(end + 14, left + width))
    text = line[left:right].strip()
    if left > 0:
        text = "…" + text
    if right < len(line):
        text = text + "…"
    return text


def classify(line):
    stripped = line.strip()
    if not stripped:
        return "blank"
    if HEADING_RE.match(line):
        return "heading"
    if stripped.startswith("|"):
        return "table"
    if LIST_RE.match(line):
        return "list"
    return "prose"


def build_paragraphs(masked):
    """连续的正文行组成段落，返回 [(起始行号, [行号...], 合并后的文本)]。行号从 0 开始。"""
    paragraphs = []
    current = []
    for idx, line in enumerate(masked):
        if classify(line) == "prose":
            current.append(idx)
        else:
            if current:
                paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    result = []
    for idxs in paragraphs:
        text = "".join(re.sub(r"^\s*(>\s*)*", "", masked[i]).strip() for i in idxs)
        result.append((idxs[0], idxs, text))
    return result


class Scanner:
    def __init__(self):
        self.phrase_rules = [(rid, name, re.compile(rx), hint, scope)
                             for rid, name, rx, hint, scope in PHRASE_RULES]
        self.term_rules = []
        for canonical, body in TERMS:
            if body is None:
                body = re.escape(canonical).replace(r"\ ", r"\s+")
            self.term_rules.append(
                (canonical, re.compile(TERM_LB + "(" + body + ")" + TERM_LA, re.IGNORECASE)))
        self.variant_groups = []
        for concept, zh, en in VARIANT_GROUPS:
            self.variant_groups.append((
                concept,
                [(label, re.compile(rx)) for label, rx in zh],
                [(label, re.compile(rx)) for label, rx in en]))

    def detect_domain(self, masked):
        text = "\n".join(masked)
        hits = len(DOMAIN_HINT_RE.findall(text))
        for _, regex in self.term_rules:
            hits += len(regex.findall(text))
            if hits >= 3:
                break
        if hits >= 3:
            return "data-ai"
        if len(FICTION_HINT_RE.findall(text)) >= 3:
            return "fiction"
        return "general"

    def scan(self, original, domain="auto"):
        lines = original.splitlines()
        masked = mask_document(lines)
        if domain == "auto":
            domain = self.detect_domain(masked)
        self.domain = domain
        if domain == "fiction":
            # 人物对白原则上不改，扫描时跳过引号里的内容
            masked = [DIALOGUE_RE.sub(lambda m: " " * len(m.group(0)), line) for line in masked]
        issues = {}  # (行号, 列号) -> issue，用来合并同一位置的多条规则

        def add(line_no, col, end, rid, name, hint):
            if domain != "data-ai" and rid in DOMAIN_ONLY:
                return
            if domain != "fiction" and rid in FICTION_ONLY:
                return
            if domain == "fiction" and rid in FICTION_SKIP:
                return
            key = (line_no, col)
            quote = lines[line_no].lstrip().startswith(">")
            if key in issues:
                issue = issues[key]
                if rid not in issue["rules"]:
                    issue["rules"].append(rid)
                    issue["names"].append(name)
                    issue["hints"].append(hint)
                return
            issues[key] = {
                "line": line_no + 1, "col": col, "rules": [rid], "names": [name],
                "hints": [hint], "text": snippet(lines[line_no], col, end), "quote": quote,
            }

        # 逐行规则。同一行同一条套话规则只报第一处。
        bold_label_run = []
        for i, line in enumerate(masked):
            kind = classify(line)
            if kind == "blank":
                continue
            for rid, name, regex, hint, scope in self.phrase_rules:
                if scope != "any":
                    continue
                m = regex.search(line)
                if m:
                    add(i, m.start(), m.end(), rid, name, hint)
            for canonical, regex in self.term_rules:
                for m in regex.finditer(line):
                    if m.group(1) != canonical:
                        add(i, m.start(1), m.end(1), "TY1", "产品名大小写",
                            "正文写作 %s（代码里照原样）" % canonical)
            for m in INTENSIFIER_RE.finditer(line):
                sentence = self._sentence_at(line, m.start())
                if not NUMBER_RE.search(sentence):
                    add(i, m.start(), m.end(), "C4", "空挂的程度词",
                        "没有数字支撑；删掉程度词或留【待补】请作者给数据")
            if CJK_RE.search(line):
                for m in PLURAL_RE.finditer(line):
                    add(i, m.start(1), m.end(1), "TY6", "中文里的英文复数",
                        "中文句子里的英文名词不加复数，写 %s" % m.group(1)[:-1])
                for m in SINGLE_DASH_RE.finditer(line):
                    if self._near_cjk(line, m.start(), m.end()):
                        add(i, m.start(), m.end(), "TY5", "破折号写法",
                            "中文破折号写作“——”（两个字符）")
                for m in DOUBLE_HYPHEN_RE.finditer(line):
                    add(i, m.start(), m.end(), "TY5", "破折号写法",
                        "不要用 -- 代替中文破折号")
                for m in HALF_PUNCT_RE.finditer(line):
                    add(i, m.start(), m.end(), "TY3", "中文句子里的半角标点",
                        "中文句子里用全角标点（，；：？！）")
            if kind == "heading":
                for m in EMOJI_RE.finditer(line):
                    add(i, m.start(), m.end(), "D1", "装饰性 emoji", "标题里去掉 emoji")
                for m in ARROW_RE.finditer(line):
                    add(i, m.start(), m.end(), "D1", "装饰性箭头", "标题里去掉装饰箭头")
            elif kind == "list":
                for m in EMOJI_RE.finditer(line):
                    add(i, m.start(), m.end(), "D1", "装饰性 emoji", "列表项里去掉装饰 emoji")
            # 粗体标签列表：连续 3 条以上（中间可以有空行）
            if BOLD_LABEL_RE.match(line):
                bold_label_run.append(i)
            elif kind != "blank":
                self._flush_bold_run(bold_label_run, add, masked)
                bold_label_run = []
        self._flush_bold_run(bold_label_run, add, masked)

        # A9 标题后复述标题：标题下第一句很短，并且包含标题文字
        for i, line in enumerate(masked):
            if classify(line) != "heading":
                continue
            title = re.sub(r"^\s{0,3}#{1,6}\s+", "", line).strip().strip("#").strip()
            title = re.sub(r"^[0-9.\s]+|^第[一二三四五六七八九十0-9]+[章节]\s*", "", title)
            j = i + 1
            while j < len(masked) and not masked[j].strip():
                j += 1
            if len(title) >= 2 and j < len(masked) and classify(masked[j]) == "prose":
                first_sentence = re.split(r"[。！？]", masked[j].strip())[0]
                if title in first_sentence and len(first_sentence) <= len(title) + 16:
                    col = masked[j].find(first_sentence)
                    add(j, col, col + len(first_sentence), "A9", "标题后复述标题",
                        "删掉这句，从第一句实际内容开始")

        # 段落规则
        for first, idxs, text in build_paragraphs(masked):
            similes = SIMILE_RE.findall(text)
            if len(similes) >= 3:
                add(first, 0, 2, "N5", "比喻堆叠",
                    "这一段用了 %d 处“仿佛/宛如/犹如/如同”，留最贴切的一个" % len(similes))
            first_line = masked[first]
            offset = len(first_line) - len(re.sub(r"^\s*(>\s*)*", "", first_line))
            for rid, name, regex, hint, scope in self.phrase_rules:
                if scope == "para_start":
                    m = regex.search(text[:60])
                    if m and m.start() <= 40 and m.end() + offset <= len(first_line):
                        add(first, m.start() + offset, m.end() + offset, rid, name, hint)
                elif scope == "para_end":
                    sentences = [s for s in SENTENCE_END_RE.split(text) if s.strip()]
                    if len(sentences) >= 2:
                        last = sentences[-1].strip()
                        if regex.search(last):
                            last_idx = idxs[-1]
                            col = max(0, masked[last_idx].find(last[:6]))
                            add(last_idx, col, col + len(last), rid, name, hint)
            if "首先" in text and re.search(r"其次|再次|最后", text):
                col = max(0, first_line.find("首先"))
                add(first, col, col + 2, "C2", "顺序路标",
                    "不是操作步骤时，去掉“首先、其次、最后”，按内容自然衔接")
            bolds = BOLD_RE.findall(text)
            if len(bolds) >= 3:
                add(first, offset, offset + 2, "D2", "粗体太多",
                    "这一段有 %d 处加粗，每节最多留一两处" % len(bolds))

        file_notes = [n for n in self._file_level(masked)
                      if not (domain != "data-ai" and n[0] in DOMAIN_ONLY)
                      and not (domain == "fiction" and n[0] in FICTION_SKIP)]
        marks = []
        for i, line in enumerate(masked):
            for m in MARK_RE.finditer(line):
                marks.append((i + 1, m.group(0)))
        ordered = sorted(issues.values(), key=lambda x: (x["line"], x["col"]))
        return ordered, file_notes, marks, domain

    @staticmethod
    def _sentence_at(line, pos):
        start = max(line.rfind(ch, 0, pos) for ch in "。！？；")
        ends = [line.find(ch, pos) for ch in "。！？；"]
        ends = [e for e in ends if e != -1]
        end = min(ends) if ends else len(line)
        return line[start + 1:end]

    @staticmethod
    def _near_cjk(line, start, end):
        left = line[:start].rstrip()[-1:]
        right = line[end:].lstrip()[:1]
        return bool(CJK_RE.match(left or " ") or CJK_RE.match(right or " "))

    @staticmethod
    def _flush_bold_run(run, add, masked):
        if len(run) >= 3:
            first = run[0]
            add(first, 0, len(masked[first]), "D1", "粗体标签列表",
                "连续 %d 条“**标签**：解释”；标签只是重复内容时改写成段落" % len(run))

    def _file_level(self, masked):
        notes = []
        text = "\n".join(masked)

        for concept, zh, en in self.variant_groups:
            zh_counts = [(label, len(rx.findall(text))) for label, rx in zh]
            en_counts = [(label, len(rx.findall(text))) for label, rx in en]
            zh_present = [(l, c) for l, c in zh_counts if c]
            en_present = [(l, c) for l, c in en_counts if c]
            detail = "、".join("%s %d 处" % (l, c) for l, c in zh_present + en_present)
            if len(zh_present) >= 2:
                notes.append(("T1", "术语混用", "%s：%s。中文叫法不统一，选一种全文沿用" % (concept, detail)))
            elif len(en_present) >= 2:
                notes.append(("T1", "术语混用", "%s：%s。英文写法不统一" % (concept, detail)))
            elif zh_present and en_present and not self._introduced(text, zh, en):
                notes.append(("T1", "术语混用",
                              "%s：%s。中英文叫法混用，没找到“中文（英文）”形式的首次说明" % (concept, detail)))

        spaced = []
        unspaced = []
        for i, line in enumerate(masked):
            for m in SPACED_RE.finditer(line):
                spaced.append(i + 1)
            for m in UNSPACED_RE.finditer(line):
                unspaced.append(i + 1)
        if spaced and unspaced:
            minority, label = (unspaced, "无空格") if len(unspaced) <= len(spaced) else (spaced, "有空格")
            examples = "、".join(str(n) for n in sorted(set(minority))[:6])
            notes.append(("TY2", "中英间距不统一",
                          "中文与英文、数字之间有空格 %d 处、无空格 %d 处；少数一方（%s）在第 %s 行等"
                          % (len(spaced), len(unspaced), label, examples)))

        full_paren = text.count("（")
        half_paren = len(re.findall("[%s]\\(|\\([%s]" % (CJK, CJK), text))
        if full_paren and half_paren:
            notes.append(("TY3", "括号全半角混用",
                          "中文语境里全角括号 %d 处、半角括号 %d 处，统一用一种" % (full_paren, half_paren)))

        curly = len(re.findall("[“”]", text))
        corner = len(re.findall("[「」]", text))
        straight = len(re.findall('[%s]"|"[%s]' % (CJK, CJK), text))
        styles = [(n, c) for n, c in (("“”", curly), ("「」", corner), ('半角 "', straight)) if c]
        if len(styles) >= 2:
            notes.append(("TY4", "引号混用",
                          "、".join("%s %d 处" % (n, c) for n, c in styles) + "，全文只用一种"))

        dashes = text.count("——")
        cjk_chars = len(CJK_RE.findall(text))
        if cjk_chars >= 500 and dashes * 1000.0 / cjk_chars > 4:
            notes.append(("TY5", "破折号偏多",
                          "每千字约 %.1f 处“——”，检查有没有当连接拐杖用（B9）" % (dashes * 1000.0 / cjk_chars)))
        return notes

    @staticmethod
    def _introduced(text, zh, en):
        for _, zr in zh:
            for m in zr.finditer(text):
                pm = re.match(r"\s*[（(]([^）)\n]{0,60})[）)]", text[m.end():m.end() + 66])
                if pm and any(er.search(pm.group(1)) for _, er in en):
                    return True
        for _, er in en:
            for m in er.finditer(text):
                pm = re.match(r"\s*[（(]([^）)\n]{0,36})[）)]", text[m.end():m.end() + 42])
                if pm and any(zr.search(pm.group(1)) for _, zr in zh):
                    return True
        return False


def collect_files(paths):
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
                for name in sorted(names):
                    if os.path.splitext(name)[1].lower() in TEXT_EXTS:
                        files.append(os.path.join(root, name))
        else:
            print("找不到：%s" % p, file=sys.stderr)
    return files


def render(results, fmt, max_per_rule):
    out = []
    total = 0
    if fmt == "md":
        out.append("# tech-writing-zh 扫描结果")
        out.append("")
        out.append("以下都是线索，需要逐条判断，不要照单全改。")
    else:
        out.append("tech-writing-zh 扫描结果：以下都是线索，需要逐条判断，不要照单全改。")
    domain_names = {"general": "通用", "data-ai": "数据/AI 领域", "fiction": "小说叙事"}
    for path, enc, issues, notes, marks, domain in results:
        counts = Counter()
        for issue in issues:
            for rid, name in zip(issue["rules"], issue["names"]):
                counts["%s %s" % (rid, name)] += 1
        total += len(issues) + len(notes)
        enc_note = "（范围：%s%s）" % (domain_names[domain], "" if enc == "utf-8" else "，编码 %s" % enc)
        out.append("")
        if fmt == "md":
            out.append("## %s%s" % (path, enc_note))
        else:
            out.append("文件：%s%s" % (path, enc_note))
        if not issues and not notes and not marks:
            out.append("  没有发现线索。" if fmt != "md" else "没有发现线索。")
            continue
        if counts:
            summary = "｜".join("%s ×%d" % (k, v) for k, v in counts.most_common())
            out.append(("统计：" if fmt == "md" else "  统计：") + summary)
        shown = Counter()
        hidden = 0
        lines_out = []
        for issue in issues:
            key = issue["rules"][0]
            if shown[key] >= max_per_rule:
                hidden += 1
                continue
            shown[key] += 1
            label = "/".join(issue["rules"]) + " " + "/".join(issue["names"])
            quote = "（引用块，确认是否为引文）" if issue["quote"] else ""
            hint = "；".join(dict.fromkeys(issue["hints"]))
            if fmt == "md":
                lines_out.append("- 第 %d 行 [%s]%s：%s  \n  提示：%s"
                                 % (issue["line"], label, quote, issue["text"], hint))
            else:
                lines_out.append("    第 %d 行 [%s]%s %s" % (issue["line"], label, quote, issue["text"]))
                lines_out.append("      提示：%s" % hint)
        if lines_out:
            out.append("" if fmt == "md" else "  逐行：")
            if fmt == "md":
                out.append("### 逐行")
            out.extend(lines_out)
            if hidden:
                out.append(("" if fmt == "md" else "    ") +
                           "（另有 %d 条同类线索未显示，可用 --max 调大）" % hidden)
        if notes:
            if fmt == "md":
                out.append("")
                out.append("### 全文")
                for rid, name, text in notes:
                    out.append("- [%s %s] %s" % (rid, name, text))
            else:
                out.append("  全文：")
                for rid, name, text in notes:
                    out.append("    [%s %s] %s" % (rid, name, text))
        if marks:
            mark_lines = sorted(set(n for n, _ in marks))
            where = "、".join(str(n) for n in mark_lines[:20])
            more = "等" if len(mark_lines) > 20 else ""
            line = "待补/待确认：%d 处（第 %s 行%s）" % (len(marks), where, more)
            out.append(("" if fmt != "md" else "\n") + ("- " if fmt == "md" else "  ") + line)
    out.append("")
    out.append("共扫描 %d 个文件，线索 %d 条。" % (len(results), total))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="tech-writing-zh 只读扫描：报告中文技术稿里的 AI 套话、术语和排版线索。")
    parser.add_argument("paths", nargs="+", help="要扫描的文件或目录（.md .markdown .mdx .txt）")
    parser.add_argument("--domain", choices=["auto", "general", "data-ai", "fiction"], default="auto",
                        help="检查范围：auto 按内容判断（默认），general 通用，data-ai 数据/AI 领域，fiction 小说叙事")
    parser.add_argument("--format", choices=["text", "md"], default="text", help="输出格式，默认 text")
    parser.add_argument("--max", type=int, default=15, help="每个文件每条规则最多显示几条，默认 15")
    args = parser.parse_args(argv)

    files = collect_files(args.paths)
    if not files:
        print("没有找到可扫描的文件。", file=sys.stderr)
        return 1
    scanner = Scanner()
    results = []
    for path in files:
        text, enc = read_text(path)
        if text is None:
            print("无法识别编码，已跳过：%s" % path, file=sys.stderr)
            continue
        issues, notes, marks, domain = scanner.scan(text, args.domain)
        results.append((path, enc, issues, notes, marks, domain))
    print(render(results, args.format, max(1, args.max)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
