# 术语、写法与排版

项目有自己的术语表或写作规范时，以项目为准。本文件是默认约定。

## 1. 术语总则

- 首次出现写“中文（English）”，有通用缩写的写“中文（English，缩写）”，之后只用一种写法。例：检索增强生成（Retrieval-Augmented Generation，RAG）。
- 产品名、项目名、公司名不翻译，按官方写法。
- 没有公认中文译名的术语保留英文，不硬造译名。拿不准时保留英文，首次出现加一句解释。
- 中文句子里的英文名词不加复数：写“多个 GPU”“三个 API”，不写“多个 GPUs”。
- 表名、字段名、函数名、类名、参数、命令、文件名用行内代码标出。
- 下面的大小写规则只管正文。代码里的包名和导入语句（如 `import torch`、`pip install pyspark`）照代码原样写。

## 2. 正文里的官方写法

大数据存储与计算：Hadoop、HDFS、YARN、MapReduce、Hive、HBase、Spark、PySpark、Spark SQL、Flink、Flink CDC、Kafka、Pulsar、RocketMQ、ZooKeeper、Presto、Trino、Impala、ClickHouse、Apache Doris、StarRocks、Apache Druid、Apache Kylin、Elasticsearch、OpenSearch、Redis、MySQL、PostgreSQL、MongoDB、TiDB、OceanBase

湖仓与文件格式：Apache Iceberg、Apache Hudi、Delta Lake、Apache Paimon、Parquet、ORC、Avro、Apache Arrow、DuckDB、Polars

调度与数据集成：Airflow、DolphinScheduler、dbt、DataX、SeaTunnel、Debezium

云与平台：Snowflake、BigQuery、Databricks、Amazon S3、Amazon Redshift、MaxCompute、Kubernetes、Docker

AI 框架与工具：PyTorch、TensorFlow、JAX、Keras、scikit-learn、XGBoost、LightGBM、pandas、NumPy、Jupyter、MLflow、Ray、Hugging Face、Transformers（库）、LangChain、LlamaIndex、vLLM、SGLang、Ollama、DeepSpeed、ONNX、CUDA、NVIDIA

向量检索：Milvus、Faiss、pgvector

模型与公司：OpenAI、ChatGPT、Anthropic、Claude、Gemini、Llama、Qwen、DeepSeek

常用缩写：AI、LLM、RAG、MCP、LoRA、QLoRA、RLHF、SFT、MoE、GPU、CPU、TPU、API、SDK、SQL、JSON、YAML、ETL、ELT、CDC、OLAP、OLTP、QPS、TPS、SLA

其他：GitHub、GitLab、Python、Java、Scala、JavaScript、TypeScript、macOS、Linux

常见误写：Clickhouse、Zookeeper、ElasticSearch、Hbase、Pytorch、Tensorflow、Huggingface、Langchain、Github、Mysql、Postgresql、Mongodb、Deepseek、Llamaindex、VLLM、Openai、Chatgpt、Starrocks、Duckdb、Mlflow、Numpy、Xgboost、Nvidia、Javascript。

Kubernetes 在正式文稿里写全称；口语材料里用 K8s 也可以，但全文只用一种。

## 3. 易混淆术语（改写时不得互换）

| 词 | 区别 |
|---|---|
| 延迟（latency）/ 吞吐（throughput） | 延迟是单个请求花的时间，单位是 ms、s；吞吐是单位时间处理的量，单位是 QPS、条/秒、MB/s |
| 平均延迟 / P99 延迟 | P99 是 99% 的请求都不超过的延迟，两者不能互换 |
| 准确率（accuracy）/ 精确率（precision）/ 召回率（recall） | 三个不同的指标；F1 是精确率和召回率的调和平均 |
| 精度（数值精度，如 FP16、BF16）/ 精确率（指标） | 英文都可能写作 precision，按语境区分 |
| 召回（推荐、检索里的一个阶段）/ 召回率（指标） | 一个是流程环节，一个是评估指标 |
| 预训练 / 微调 / 推理 | 预训练和微调都会更新参数，推理不更新参数 |
| 参数量 / 训练 token 数 / 上下文长度 | 分别描述模型大小、训练数据量、单次输入能容纳的 token 数 |
| token / 字、词 | 一个 token 不等于一个汉字或一个词，比例取决于分词器 |
| 量化 / 蒸馏 / 剪枝 | 三种不同的模型压缩方法 |
| 分区（partition）/ 分桶（bucket）/ 分片（shard） | 在不同系统里含义不同，按原文所指的系统保留 |
| 至少一次 / 恰好一次 / 至多一次 | 三种不同的投递或处理语义 |
| 强一致性 / 最终一致性 | 一致性保证的强弱不同 |
| 批处理 / 流处理 / 微批 | 微批用小批次处理流式数据，和逐条处理的流处理不是一回事 |
| 离线 / 实时 / 准实时 | 时效要求不同，原文怎么写就怎么保留 |
| 数据仓库 / 数据湖 / 湖仓一体 | 三种不同的架构，不能互相替代 |
| 并发 / 并行 | 并发是同一时段内处理多个任务，并行是同一时刻在多个处理单元上执行 |
| 副本 / 备份 | 副本用于高可用和读扩展，备份用于恢复 |
| 数据倾斜 / 数据漂移 | 前者是计算时数据分布不均，后者是数据分布随时间变化 |
| 向量数据库 / 向量索引（如 HNSW、IVF） | 一个是系统，一个是系统里的索引结构 |
| 数仓分层名称（ODS、DWD、DWS、ADS 等） | 按团队约定原样保留，不翻译，不合并 |

原文疑似用错时，只在【待确认】里说明，不改。

## 4. 数字、单位、日期和时效

- 数字、单位、百分比、版本号、配置值原样保留，不换算，不四舍五入。用户要求统一换算时再改。
- 数字和单位的写法跟随项目约定；没有约定时全文统一一种（比如统一写“100 ms”或统一写“100ms”），% 和 ° 紧跟数字。
- 性能数据尽量带上对比基线、数据量、硬件或集群规模、软件版本、指标口径（平均值、P95、P99）。原文缺这些，在“需要你确认”里提示，不替作者补。
- 收益类数字要有比较单位：每次、每天、每人、每周还是累计。
- 日期写法全文统一，如“2026 年 3 月 5 日”。书稿里的“今年”“去年”“最近”换成具体时间（来自原文或作者），否则留【待确认】。
- “目前最新”“当前最强”“主流”这类说法要有时间点或版本，没有就留【待确认】。

## 5. 中英混排与标点

- 中文正文用全角标点（，。；：？！）；整句英文内部用半角标点。
- 中文和英文、数字之间是否加空格，跟随稿件现有的写法；稿件里两种写法都有时，统一成加一个半角空格。全角标点前后不加空格。
- 引号：中文正文默认用“”，嵌套用‘’；项目或出版社要求「」时统一用「」，嵌套用『』。全文只用一种。
- 破折号写作“——”，省略号写作“……”，按《标点符号用法》（GB/T 15834）使用。不机械删除，只清理当连接拐杖的用法。
- 中文语境的括号用全角（），全文一致。
- 列表项末尾的标点全文统一：要么都不加，要么都用句号。
