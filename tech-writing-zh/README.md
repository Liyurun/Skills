# tech-writing-zh

中文去 AI 味 skill：逐条检测文章里明显的 AI 措辞和句式，并直接改掉。不重写结构，不扩写，不调整文风，没命中的文字原样保留。

版本：2.1.0（2026-11-06）

## 三种范围

| 范围 | 适用 | 检测内容 |
|---|---|---|
| 通用 | 任何中文文章 | patterns.md 的 A–E 全部模式：篇章套路、句式套路、宣传腔和路标词、装饰性格式、聊天残留 |
| 数据/AI 领域 | 大数据、数据工程、AI、机器学习、大模型方向的文章和书稿 | A–E 全部模式，加上 T 类模式（术语统一、易混淆术语、数据基准、夸大、时效、比喻等）和 tech-terms.md 的写法规则（产品名大小写等） |
| 小说叙事 | 小说、故事、有情节的叙事散文 | narrative.md 规定的 A–E 子集，加上 N 类模式（说破主题、环境当心理镜子、网文式神态套话、身体反应堆叠、比喻堆叠等）；人物对白原则上不改，不新增情节和人物 |

不指定时按内容自动判断。也可以直接说“用通用范围”“按数据 AI 领域检查”或“按小说处理”。

每次都会把所在范围的全部模式逐条过一遍：

- 【强】模式出现一次就改；
- 【弱】模式成簇出现才改；
- 【只提示】模式涉及结构或内容缺口（比如每节都是同一个骨架、方案只讲好处），不动正文，列进“需要你确认”。

三条底线：不编造事实和数据，缺了就留【待补】；不改技术含义，数字、代码、链接、专有名词原样保留，拿不准的留【待确认】；不矫枉过正。

## 目录

```
tech-writing-zh/
├── SKILL.md                    # 入口：两种范围、做法、底线、输出
├── README.md                   # 本文件（agent 运行时不读）
├── references/
│   ├── patterns.md             # 模式库：A–E 通用，T 数据/AI 领域，附改前改后示例
│   ├── tech-terms.md           # 数据/AI 领域的术语写法、产品名大小写、易混淆术语、数字单位、中英混排
│   └── narrative.md            # 小说叙事分支：适用规则和 N 类模式
├── scripts/
│   └── lint_zh_tech.py         # 只读扫描脚本（Python 3.8+，只用标准库）
└── evals/
    └── smoke-tests.md          # 迁移后自测用例
```

## 安装

无论放在哪里，都要保证 `SKILL.md` 位于 `tech-writing-zh/` 目录的根下。

- Claude Code：放到项目的 `.claude/skills/` 或个人的 `~/.claude/skills/` 下。
- Codex：放到项目的 `.agents/skills/` 或个人的 `~/.codex/skills/` 下。
- Claude.ai、Claude Desktop：在设置的 Skills 页面上传 zip。
- DW：新建 skill，名称填 `tech-writing-zh`（必须和 SKILL.md 里的 name 一致），按原有相对路径上传全部文件。

## 用法示例

- 帮我把这篇文章去一下 AI 味：（粘贴正文）
- 检查 docs/chapter-03.md 里的 AI 痕迹并改掉
- 按数据 AI 领域检查这篇 Flink 调优文章
- 按小说处理，去掉这一章的 AI 味

## 扫描脚本

```
python3 tech-writing-zh/scripts/lint_zh_tech.py docs/
python3 tech-writing-zh/scripts/lint_zh_tech.py chapter-03.md --domain data-ai --format md > lint-report.md
```

`--domain` 可选 auto（默认）、general、data-ai、fiction。fiction 范围会跳过引号里的人物对白。脚本按模式编号列出疑似命中的位置（A–E、T 对应 patterns.md，N 对应 narrative.md，TY 对应 tech-terms.md 的排版和写法规则），并列出稿件里的【待补】【待确认】。它只读不写，跳过 front matter、代码块、行内代码、公式、HTML 注释和链接地址，能识别 UTF-8 和 GB18030 编码。脚本只能抓固定说法，结果要逐条判断。

规则表在脚本开头（`PHRASE_RULES`、`TERMS`、`VARIANT_GROUPS`、`DOMAIN_ONLY`、`FICTION_ONLY`、`FICTION_SKIP`），可以按团队习惯增删。

## 迁移后自测

`evals/smoke-tests.md` 有 11 个用例，换平台后逐个跑一遍，对照通过标准检查。

## 设计来源

综合了 blader/humanizer v3.1.0（MIT）、De-AI-Prompt-Enhancer 的 de-AI-writing v2.1、ai-zixun/humanizer-zh v1.3.0（MIT）、hardikpandya/stop-slop（MIT）和 DW 上的 shopee-ai-unslop 的做法，文字和示例为重新撰写，示例全部遵守“改后不新增事实”。没有采用：运行时联网拉规则、自动安装第三方包、模仿在世作者、收录第三方范文、“删光副词”一类绝对规则、以压低 AI 检测率为目标。

## 版本记录

- 2.1.0（2026-11-06）：新增 A9、B10–B15、C5–C8 共 11 个模式，扩充 A4、B4、C1、C2 的说法；新增小说叙事分支（narrative.md，N1–N11），扫描脚本增加 fiction 范围并跳过人物对白。
- 2.0.0（2026-11-06）：简化为单一任务（检测并修改明显的 AI 措辞），去掉审阅、轻改、深改、起草、翻译、风格档案等操作区分；改为通用和数据/AI 领域两种范围，每次逐条检查范围内全部模式；结构类模式改为只提示；扫描脚本增加 `--domain`。
- 1.0.0（2026-11-05）：首个版本。
