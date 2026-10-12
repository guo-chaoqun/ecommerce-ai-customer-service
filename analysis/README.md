# 电商智能客服 RAG 系统 — 评测数据分析项目

## 项目简介

对一套电商智能客服 RAG（检索增强生成）工作流进行**系统化评测与数据分析**，覆盖 505 条用例、6 个业务分支、5 种测试类型、3 个难度等级。项目目标是回答四个核心问题：

1. **系统整体表现如何？**（描述性统计）
2. **为什么失败？**（失败归因分析）
3. **什么因素影响通过率？**（相关性分析）
4. **先修什么性价比最高？**（优化优先级 + 对比验证）

## 项目结构

```
ecommerce_rag_eval/
├── README.md                          # 本文件
├── requirements.txt                   # Python 依赖
├── meta.json                         # 基线元数据（实验管理）
│
├── eval_output/                      # 原始评测结果
│   ├── eval_results_20260807_142126.csv
│   └── eval_results_20260807_142126.json
│
├── baselines/                        # 基线档案
│   └── baseline_v1/
│       ├── meta.json                 # 基线 v1 元数据
│       ├── eval_set_v1.jsonl         # 505 条评测集快照
│       └── prompt_templates/         # 各 LLM 节点提示词版本
│
├── notebooks/                        # 分析 Notebook（按顺序执行）
│   ├── 01_data_loading.ipynb        # 数据加载与校验
│   ├── 02_descriptive_analysis.ipynb # 模块一：描述性统计
│   ├── 03_failure_analysis.ipynb   # 模块二：失败归因
│   ├── 04_correlation_analysis.ipynb# 模块三：相关性分析
│   ├── 05_priority_analysis.ipynb  # 模块四：优化优先级
│   ├── 06_comparison_analysis.ipynb# 模块五：Baseline vs Optimized
│   └── 07_summary_report.ipynb    # 汇总报告
│
├── charts/                           # 可视化图表输出（Notebook 自动生成）
│   ├── 01_branch_pass_rate.png
│   ├── 02_test_type_pass_rate.png
│   ├── 03_dimension_radar.png
│   ├── 04_difficulty_type_heatmap.png
│   ├── 05_score_boxplot.png
│   └── 06_dashboard.png
│
├── analysis_output/                  # 中间分析结果（Notebook 自动生成）
│   ├── module1_cleaned_data.csv
│   ├── module1_branch_stats.csv
│   ├── module1_type_stats.csv
│   ├── module1_dimension_stats.csv
│   └── module1_failed_cases.csv
│
└── regression_sets/                  # 回归测试集
    └── regression_set_v1.json
```

## 快速开始

### 1. 环境准备

```bash
# 建议使用虚拟环境
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装 graphviz 系统依赖（可选，用于模块二决策树）
# Ubuntu: sudo apt-get install graphviz
# Mac:    brew install graphviz
# Windows: 下载 https://graphviz.org/download/
```

### 2. 启动 Jupyter

```bash
# ⚠️ 必须从项目根目录启动，Notebook 内的相对路径基于此
cd ecommerce_rag_eval
jupyter notebook notebooks/01_data_loading.ipynb
```

### 3. 执行顺序

按编号顺序执行 7 个 Notebook：

| 顺序 | Notebook | 模块 | 耗时 | 核心产出 |
|:---:|---|---|:---:|---|
| 1 | `01_data_loading.ipynb` | 数据加载 | 30 min | 干净的 DataFrame + 校验报告 |
| 2 | `02_descriptive_analysis.ipynb` | 模块一：描述性统计 | 1 day | 6 张图表 + 统计摘要 |
| 3 | `03_failure_analysis.ipynb` | 模块二：失败归因 | 1.5 day | 43 条失败根因标注 |
| 4 | `04_correlation_analysis.ipynb` | 模块三：相关性 | 1 day | 相关性热力图 + 因果链 |
| 5 | `05_priority_analysis.ipynb` | 模块四：优化优先级 | 0.5 day | ROI 矩阵 + 优化路径 |
| 6 | `06_comparison_analysis.ipynb` | 模块五：对比验证 | 0.5 day | McNemar 检验报告 |
| 7 | `07_summary_report.ipynb` | 汇总报告 | 1 day | 项目总结 + 关键发现 |

**总计约 2.5-3 周。**

## Baseline v1 核心指标

| 指标 | 数值 |
|---|:---:|
| 总用例数 | 505 |
| 意图识别准确率 | 93.7% |
| 分支路由准确率 | 91.5% |
| 要点覆盖率 | 87.1% |
| 幻觉率 | 2.6% |
| 违禁表述违规率 | 0.8% |

### 各分支通过率

| 分支 | 通过率 | 均分 | 总数 |
|---|:---:|:---:|:---:|
| 订单查询 | 100.0% | 5.00 | 36 |
| 商品咨询 | 95.4% | 4.83 | 281 |
| 其他 | 93.9% | 4.69 | 49 |
| 退款换货 | 87.1% | 4.56 | 70 |
| 投诉 | 78.7% | 4.23 | 47 |
| 边界混淆 | 63.6% | 3.95 | 22 |

### 各测试类型通过率

| 类型 | 通过率 |
|---|:---:|
| single_turn | 94.8% |
| boundary | 88.6% |
| multi_turn | 82.4% |
| adversarial | 79.2% |
| prohibited | 73.9% |

## 五个分析模块

### 模块一：描述性统计分析
建立系统质量全景图。通过 pandas groupby / pivot_table 做分桶切片，用 scipy 计算均值/中位数/标准差/分位数，matplotlib + seaborn 输出柱状图、雷达图、热力图、箱线图。

**面试金句**：*"订单查询 100% 是因为走 HTTP 模拟接口不经过 LLM 生成，而边界混淆 63.6% 是因为分类器路由决策树未硬化。"*

### 模块二：失败归因分析
将 43 条失败用例按 RAG 三层失败模型分类：检索失败（Layer 1）→ 上下文失败（Layer 2）→ 生成失败（Layer 3）。区分"系统性根因"与"个案根因"。

**面试金句**：*"43 条失败中，30% 是路由错误（分类器送错分支），25% 是生成幻觉（Rerank 额度耗尽后 LLM 自由发挥），20% 是检索失败（知识库为空），15% 是要点遗漏（投诉分支未硬化动作框架），10% 是用例/裁判问题。"*

### 模块三：相关性分析
用 Spearman 秩相关、Pearson 相关、卡方检验分析维度间关联。重点发现"路由分数与信息完整性的相关系数"等因果链线索。

**面试金句**：*"分支路由分数与信息完整性的相关系数为 0.82，说明路由错误会连锁导致信息缺失——这不是两个独立问题，而是因果链。"*

### 模块四：优化优先级分析
构建"影响面 × 修复难度"四象限矩阵，用气泡图展示 ROI。排序优化路径（P0 → P1 → P2）。

**面试金句**：*"虽然边界混淆通过率最低（63.6%），但只影响 22 条用例；投诉分支 78.7% 看似更高，但影响 47 条用例且修复成本更低（仅需改 prompt），所以优先修投诉。"*

### 模块五：对比分析（Baseline vs Optimized）
优化工作流后重跑评测，用 **McNemar 精确检验**（非普通 z 检验）验证提升的统计显著性。检测回归用例。

**面试金句**：*"优化后通过率从 91.5% 提升到 96.2%。普通 z 检验 p=0.56 会误判为不显著，但 McNemar 精确检验（聚焦不一致对）p=0.01，证明提升统计显著。同时检测到 2 条回归用例。"*

## 面试叙事框架

> "我做了一个电商智能客服 RAG 系统的评测数据分析项目。首先设计了 505 条评测用例覆盖 6 个业务分支和 5 种测试类型，搭建了自动化评测框架跑出基线数据（意图识别 93.7%、路由 91.5%、要点覆盖 87.1%、幻觉率 2.6%）。通过描述性统计发现边界混淆分支通过率最低（63.6%），通过失败归因分析定位到 3 个系统性根因——其中最关键的是分类器输入变量错配。我用影响面 × 修复难度矩阵排了优化优先级，修复后通过率从 91.5% 提升到 96%+，并用 McNemar 检验验证了提升的统计显著性（p<0.01）。整个项目沉淀了 50 条回归测试用例，形成持续迭代闭环。"

## 关键技术点

- **RAG 三层失败模型**：检索失败 → 上下文失败 → 生成失败
- **McNemar 精确检验**：配对分类数据的显著性检验，控制题目难度混杂变量
- **影响面 × 修复难度矩阵**：ROI 驱动的优化优先级决策
- **回归测试集沉淀**：防止修 Bug 引入新 Bug 的"事故疫苗库"
- **基线管理**：meta.json 固化"数据集版本 + 知识库版本 + 系统版本 + 全套指标"

## 重要提醒

### ⚠️ Jupyter 相对路径
Jupyter Notebook 的相对路径**基于 kernel 启动时的当前工作目录（CWD），而非 .ipynb 文件所在目录**。必须在每个 Notebook 开头加路径处理：

```python
import os
# 切换到项目根目录（确保 eval_output/ 等相对路径正确）
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath('__file__'))))
```

**始终从项目根目录启动 Jupyter**：
```bash
cd ecommerce_rag_eval
jupyter notebook notebooks/02_descriptive_analysis.ipynb
```

### ⚠️ 通过率判定标准
模块一默认 `branch_routing >= 4` 为通过（与 Notebook 代码一致），但模块五做 McNemar 检验时**必须统一为 5/5 满分判定**，否则检验结果不可比。在 `06_comparison_analysis.ipynb` 开头明确定义：

```python
PASS_THRESHOLD = 5  # 与整体评测报告一致
```

## 产出物清单

| 产出物 | 格式 | 面试用途 |
|---|---|---|
| 评测数据集（505 条） | JSON | 展示数据规模和 schema 设计能力 |
| 分析 Notebook（7 个） | .ipynb | 核心交付物，展示代码和分析过程 |
| 可视化图表集（6+ 张） | PNG/HTML | 简历附件或面试展示 |
| 分析报告 | HTML/PDF | 项目总结，1-2 页摘要 |
| meta.json 基线文件 | JSON | 展示实验管理规范 |
| 回归测试集 | JSON | 展示持续迭代闭环思维 |
| GitHub 仓库 | Git | 展示代码组织和文档能力 |

## License

MIT