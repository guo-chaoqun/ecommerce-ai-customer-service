#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dify 电商客服回归评测脚本 v1.0
适配 eval_set_v2_regression_200.json（185 条回归评测集）

目的：检测修复后的电商客服系统能否正常使用（回归测试）

设计要点：
1. 加载回归评测集（17 字段 schema）
2. 支持单轮 / 多轮对话评测（conversation_context 前序轮次）
3. 裁判模型：阿里云百炼 qwen-plus（与 v4.4 评测同口径，保证对比公平）
4. 确定性预检：must_not 精确子串匹配 + expected_points 宽松匹配，覆盖裁判误判
5. 分支路由按"行为模式"判定（话题内容 ≠ 分支路由）
6. 类型化通过标准：
   - prohibited 类：must_not_violation==False 为通过
   - 其余类型：branch_routing >= 4 为通过
7. 断点续评（每 10 条自动保存）+ 全量报告 + 根因分析 + CSV/JSON/meta 导出

运行：python dify_eval_v2_regression.py
"""

import os
import sys
import json
import time
import re
import requests
from getpass import getpass
from typing import Optional, List, Dict
from collections import Counter
from datetime import datetime
from openai import OpenAI
import pandas as pd

# ============================================================
# Cell 1: 配置
# ============================================================
def setup_config() -> dict:
    """交互式配置"""
    print("=" * 60)
    print("Dify 电商客服回归评测 v1.0")
    print("=" * 60)

    BASELINE_ID = input("\nBaseline ID（默认 baseline_v2_regression）: ").strip()
    if not BASELINE_ID:
        BASELINE_ID = "baseline_v2_regression"
    BASELINE_NAME = input("Baseline 名称（默认 修复后回归评测）: ").strip()
    if not BASELINE_NAME:
        BASELINE_NAME = "修复后回归评测"

    # Dify 配置
    DIFY_API_URL = input("\nDify 应用 API URL（默认 http://localhost/v1/chat-messages）: ").strip()
    if not DIFY_API_URL:
        DIFY_API_URL = "http://localhost/v1/chat-messages"
    DIFY_API_KEY = getpass("Dify 应用 API Key: ")

    # 裁判模型配置
    ALIYUN_API_KEY = getpass("阿里云百炼 API Key: ")
    judge_client = OpenAI(
        api_key=ALIYUN_API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    JUDGE_MODEL = "qwen-plus"

    # 评测集路径
    EVAL_SET_PATH = input("\n评测集路径（默认 eval_set_v2_regression_200.json）: ").strip()
    if not EVAL_SET_PATH:
        EVAL_SET_PATH = "eval_set_v2_regression_200.json"

    # 输出目录
    OUTPUT_DIR = input(f"结果输出目录（默认 eval_output_{BASELINE_ID}）: ").strip()
    if not OUTPUT_DIR:
        OUTPUT_DIR = f"eval_output_{BASELINE_ID}"
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\n✅ 配置完成")
    print(f"   Baseline: {BASELINE_ID} ({BASELINE_NAME})")
    print(f"   Dify URL: {DIFY_API_URL}")
    print(f"   裁判模型: {JUDGE_MODEL}（阿里云百炼）")
    print(f"   评测集: {EVAL_SET_PATH}")
    print(f"   输出目录: {OUTPUT_DIR}")

    return {
        "BASELINE_ID": BASELINE_ID,
        "BASELINE_NAME": BASELINE_NAME,
        "DIFY_API_URL": DIFY_API_URL,
        "DIFY_API_KEY": DIFY_API_KEY,
        "judge_client": judge_client,
        "JUDGE_MODEL": JUDGE_MODEL,
        "EVAL_SET_PATH": EVAL_SET_PATH,
        "OUTPUT_DIR": OUTPUT_DIR,
    }


# ============================================================
# Cell 2: 加载评测集
# ============================================================
def load_eval_set(filepath: str) -> tuple:
    """加载回归评测集 JSON，返回 (cases, meta)"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]
    meta = data.get("meta", {})

    print(f"✅ 评测集加载完成")
    print(f"   版本: {meta.get('version', '?')}")
    print(f"   总用例数: {len(cases)}")
    print(f"   文件: {filepath}")
    print(f"\n   按类型: {dict(Counter(c['test_type'] for c in cases))}")
    print(f"   按分支: {dict(Counter(c['category'] for c in cases))}")
    print(f"   按意图: {dict(Counter(c['expected_intent'] for c in cases))}")
    return cases, meta


# ============================================================
# Cell 3: 调用 Dify 应用（支持多轮）
# ============================================================
def call_dify(config: dict, user_query: str, conversation_id: Optional[str] = None):
    """调用 Dify Chatflow 电商客服应用（单轮）"""
    headers = {
        "Authorization": f"Bearer {config['DIFY_API_KEY']}",
        "Content-Type": "application/json",
    }
    data = {
        "inputs": {},
        "query": user_query,
        "user": "eval-user",
        "response_mode": "blocking",
    }
    if conversation_id:
        data["conversation_id"] = conversation_id
    try:
        resp = requests.post(config["DIFY_API_URL"], headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        return result.get("answer", ""), result.get("conversation_id", "")
    except Exception as e:
        return f"[ERROR] {str(e)}", conversation_id or ""


def call_dify_multi_turn(config: dict, case: dict):
    """
    多轮对话评测：
    1. 先发送 conversation_context 中的每轮 user 消息建立会话
    2. 最后发送 user_query 获取最终回复
    """
    context = case.get("conversation_context", [])
    if not context:
        return call_dify(config, case["user_query"])

    conversation_id = None
    for turn in context:
        if turn["role"] == "user":
            _, conversation_id = call_dify(config, turn["content"], conversation_id)
            time.sleep(0.3)  # 多轮间短暂延时

    final_answer, final_conv_id = call_dify(config, case["user_query"], conversation_id)
    return final_answer, final_conv_id


# ============================================================
# Cell 4: 确定性预检（覆盖裁判误判）
# ============================================================
def check_must_not(response: str, must_not_list: list) -> bool:
    """精确子串匹配，返回 True 表示违规"""
    if not must_not_list:
        return False
    for item in must_not_list:
        item = item.strip()
        if not item:
            continue
        if item in response:
            return True
    return False


def _is_range_match(point: str, response: str) -> bool:
    """范围语义匹配：如 '1-7' 匹配回复中出现的 '1-3'/'3-7'/'2-5' 等分段范围"""
    m = re.match(r"^(\d+)\s*[-~～]\s*(\d+)$", point.strip())
    if not m:
        return False
    p_start, p_end = int(m.group(1)), int(m.group(2))

    resp_ranges = re.findall(r"(\d+)\s*[-~～]\s*(\d+)\s*(个工作日|天|小时|分钟)?", response)
    if not resp_ranges:
        return False

    all_starts = [int(r[0]) for r in resp_ranges]
    all_ends = [int(r[1]) for r in resp_ranges]
    if all_starts and all_ends:
        if min(all_starts) <= p_start and max(all_ends) >= p_end:
            return True

    for rs, re_, unit in resp_ranges:
        if int(rs) <= p_start and int(re_) >= p_end:
            return True
    return False


def check_expected_points(response: str, expected_points: list) -> bool:
    """
    宽松匹配：关键词或其变形出现在回复中即算命中，全部命中返回 True
    支持：精确子串、标点清理、范围语义匹配、前缀匹配、数字+单位宽松匹配
    """
    if not expected_points:
        return True
    for point in expected_points:
        point = point.strip()
        if not point:
            continue
        if point in response:
            continue
        point_clean = point.replace("，", "").replace("。", "").replace("、", "").replace(" ", "")
        resp_clean = response.replace(" ", "")
        if point_clean in resp_clean:
            continue
        if _is_range_match(point, response):
            continue
        if len(point) >= 4:
            short = point[:4]
            if short in response:
                continue
        num_match = re.match(r"^(\d+)(.+)$", point)
        if num_match:
            num, unit = num_match.group(1), num_match.group(2)
            pattern = num + r"[^0-9]{0,5}" + re.escape(unit[-1])
            if re.search(pattern, response):
                continue
        return False
    return True


# ============================================================
# Cell 5: 裁判评分（复用 v4.4 口径）
# ============================================================
JUDGE_PROMPT_TEMPLATE = """你是一名严格的电商客服质量评审专家。请评估以下客服回复的质量。

## 评分规则（必须严格遵守）

### branch_routing（分支路由）评分规则
- 你无法看到系统的实际路由 trace，请根据回复的【行为模式】判断，而非回复的【话题内容】
- 判定方法：观察回复在"做什么"，而不是回复"在讲什么"
- 5分：回复的行为模式与期望分支"{branch}"完全一致
- 3-4分：行为模式基本符合期望分支，但有少量偏差
- 0-2分：行为模式明显属于其他分支

【各分支行为特征对照表】（核心判定依据）

1. 商品咨询与规则查询分支（知识检索 + LLM直接回答）：
   - 行为特征：直接解答用户的政策/规则/参数问题，不引导任何操作流程
   - 典型表现：直接给出答案（时效/运费/规则/参数等）、引用知识库内容、不索要订单号、不确认办理意向
   - 注意：即使回答的是"退款多久到账""退换货政策"等售后话题，只要是直接解答规则而非引导办理，就属于此分支
   - 操作流程咨询也属此分支：用户问"怎么操作/怎么申请/流程是什么"，回复描述操作步骤/方法 → 商品咨询（解答方法）
   - 知识库未命中也属此分支：回复"暂未收录/知识库没有相关信息" → 路由仍是商品咨询，只是信息完整性低

2. 退款换货分支（售后办理流程）：
   - 行为特征：引导用户完成退款/退货/换货/取消/修改的办理流程
   - 典型表现：确认退款/退货/换货意向、索要订单号、询问退款原因、引导寄回、告知办理步骤
   - 注意：如果只是回答退款政策问题（如时效），不引导办理，则不属于此分支
   - 告知办理结果也属此分支：如查询订单后告知"已超期无法办理""已签收无法取消" → 退款换货分支（已处理并给出结果）
   - 子场景包括：退款、退货、换货、取消订单、修改订单、价格保护申请等所有售后办理动作

3. 订单查询分支（工具调用查订单）：
   - 行为特征：查询并展示具体订单的实时数据
   - 典型表现：提供订单状态、物流信息、具体订单数据，通常需要订单号
   - 索要订单号以进行查询也属此分支
   - 注意：即使回复内容有幻觉（虚构物流信息），只要行为模式是在告知订单/物流状态，分支路由仍应判定为订单查询（幻觉由 faithfulness 维度负责）

4. 投诉分支（投诉处理流程）：
   - 行为特征：处理投诉/追责/赔偿诉求
   - 典型表现：道歉、记录投诉、升级处理、赔偿方案、安抚情绪
   - 投诉分支也可以转人工：作为升级处理的一种方式，不代表路由错误
   - 判断标准：是否有投诉处理的核心行为（道歉+记录/升级/补偿），转人工只是附加动作

5. 其他分支（兜底/转人工）：
   - 行为特征：无法归类、转人工、或通用兜底回复
   - 典型表现："抱歉我无法回答"、"为您转接人工"、身份说明
   - 注意：如果回复有明确的业务分支行为（如道歉+补偿=投诉、解答规则=商品咨询），即使附带转人工，也不应判为其他分支

【关键判定原则】
- 话题内容 ≠ 分支路由。退款话题可能走商品咨询分支（咨询政策），也可能走退款换货分支（办理退款）
- 核心区分：回复是在"提供信息/解答规则"还是"引导操作/办理流程"
- 直接解答政策/规则问题 → 商品咨询分支；引导办理/操作流程 → 对应业务分支
- 只要行为模式符合期望分支，即使回复内容涉及其他分支话题，也应给4分以上

【核心行为优先原则】（重要，避免误判）
- 当回复包含多种行为时，以【核心/主要行为】判定分支，不以附带/次要行为判定
- 判断方法：看回复的主体内容在做什么，开头和结尾的附加引导不算核心

【操作流程咨询特殊判定】（重要，避免误判）
- 用户问"怎么操作/怎么申请/流程是什么/如何办理"等带疑问词的问题 → 属于操作流程咨询
- 回复描述了操作步骤/方法（如"点击申请售后→选择价格保护"）→ 商品咨询分支（解答方法）
- 回复引导用户现在就去办理（如"请提供订单号我帮您申请"）→ 退款换货分支（引导办理）
- 区分标准：回复是在"告诉用户怎么操作"（咨询）还是在"引导用户现在就办"（办理）

【大类与子场景区分原则】（重要，避免误判）
- 退款换货是一个大类，包含多个子场景：退款、退货、换货、修改订单、取消订单等
- branch_routing 只评判【大类】是否正确，不要求子场景完全匹配
- 只要回复的行为模式属于"售后办理引导"（确认需求、索要信息、引导流程、告知办理条件、告知办理结果等），即视为退款换货大类路由正确
- 子场景是否精准匹配（如改地址 vs 换货、取消订单 vs 退款），由 intent_accuracy 维度负责评分，branch_routing 不因此扣分

【超期/不符合条件告知的判定】（避免误判）
- 用户申请售后（退款/退货/换货），系统查询后告知"已超期/不符合条件/无法办理" → 属于退款换货分支的正确行为
- 系统已经处理了用户的诉求（查询订单+给出结论），只是结论是"无法办理"
- 不能因为系统没有"引导办理流程"就扣分，因为不符合条件时本就不应引导办理

【路由维度与其他维度的分离原则】（极其重要）
- branch_routing 只评判【行为模式属于哪个分支】，不评判内容的真实性、完整性、准确性
- 内容真实性 → faithfulness 维度负责；内容完整性 → information_completeness 维度负责
- 意图准确性 → intent_accuracy 维度负责

### must_not_violation（禁止表述违规）判定规则
- 逐条检查 must_not 列表，使用精确子串匹配
- 只有当回复中完整包含 must_not 中的某个表述时，才判定为违规
- 禁止使用语义近似、同义词扩展来判断
- 否定表述不算违规：如 must_not=["可办理退货"]，回复中有"不可办理退货"→不违规（语义相反）

### contains_expected_points（要点覆盖）判定规则
- 逐条检查 expected_answer_contains 中的每个要点
- 使用宽松匹配：关键词的变形、包含该关键词的 longer phrase 均算命中
- 范围匹配：如期望"1-7天"，回复中有"1-3天"和"3-7天"等分段范围整体覆盖1-7区间，也视为命中
- 只要回复中体现了该要点的核心语义，即算命中；全部命中→true，有任何一个未命中→false

### boundary_correctness（边界正确性）评分规则
- 根据回复是否正确处理了边界值说明中的业务逻辑来评分
- 只要回复体现了边界值对应的核心处理逻辑，即给4分以上
- 不要因为回复提供了额外的替代方案就扣分

## 用例信息
【用例ID】{case_id}
【测试类型】{test_type}
【用户问题（最后一轮）】{user_query}
【对话上下文】
{context}

【期望意图】{intent}
【期望分支】{branch}
【期望IF/ELSE路径】{if_else}
【期望答案应包含的关键要点】{points}
【回复中禁止出现的表述】{must_not}
【期望工具调用】{tools}
【期望投诉分级】{complaint_level}
【是否期望转人工】{handoff}
【边界值说明】{boundary}

【实际客服回复】
{response}

请从以下维度严格打分（0-5分），并输出 JSON 格式：
{{
    "intent_accuracy": <0-5, 意图识别准确度>,
    "branch_routing": <0-5, 分支路由正确性>,
    "answer_relevance": <0-5, 答案相关性>,
    "information_completeness": <0-5, 信息完整性>,
    "faithfulness": <0-5, 忠实度（是否编造信息）>,
    "tone_compliance": <0-5, 语气合规性>,
    "must_not_violation": <true/false, 是否包含禁止表述>,
    "boundary_correctness": <0-5, 边界值处理正确性>,
    "is_hallucination": <true/false, 是否存在幻觉>,
    "contains_expected_points": <true/false, 是否包含期望要点>,
    "handoff_correctness": <0-5, 转人工处理正确性，不适用填-1>,
    "comment": "<简短评语，80字以内>"
}}

评分标准：
- 5分：完全正确，无缺陷
- 4分：基本正确，有小瑕疵
- 3分：部分正确，有明显不足
- 2分：大部分不正确
- 1分：严重错误
- 0分：完全错误

只输出 JSON，不要输出其他内容。"""


def llm_judge(config: dict, case: dict, actual_response: str) -> dict:
    """增强版裁判评分，覆盖全维度 + 确定性预检覆盖"""
    must_not_str = "、".join(case.get("must_not", [])) if case.get("must_not") else "无"
    expected_points = ("、".join(case.get("expected_answer_contains", []))
                       if case.get("expected_answer_contains") else "无特殊要求")

    context_str = ""
    if case.get("conversation_context"):
        lines = []
        for t in case["conversation_context"]:
            role = "用户" if t["role"] == "user" else "客服"
            lines.append(f"{role}: {t['content'][:80]}")
        context_str = "\n".join(lines)

    complaint_level = case.get("expected_complaint_level") or "不适用"

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        branch=case["expected_branch"],
        case_id=case["case_id"],
        test_type=case["test_type"],
        user_query=case["user_query"],
        context=context_str if context_str else "无（单轮对话）",
        intent=case["expected_intent"],
        if_else=", ".join(case.get("expected_if_else_path", [])),
        points=expected_points,
        must_not=must_not_str,
        tools=", ".join(case.get("expected_tool_calls", [])) if case.get("expected_tool_calls") else "无",
        complaint_level=complaint_level,
        handoff=case.get("expected_human_handoff", False),
        boundary=case.get("boundary_value") or "无",
        response=actual_response,
    )

    # 第一优先：带 response_format 调用
    try:
        resp = config["judge_client"].chat.completions.create(
            model=config["JUDGE_MODEL"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        score = json.loads(resp.choices[0].message.content)
    except Exception as e1:
        # 第二优先：去掉 response_format 重试
        try:
            resp = config["judge_client"].chat.completions.create(
                model=config["JUDGE_MODEL"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content.rsplit("```", 1)[0]
            content = content.strip()
            if content.startswith("json"):
                content = content[4:].strip()
            score = json.loads(content)
        except Exception as e2:
            score = {
                "error": str(e1),
                "intent_accuracy": 0, "branch_routing": 0,
                "answer_relevance": 0, "information_completeness": 0,
                "faithfulness": 0, "tone_compliance": 0,
                "must_not_violation": False, "boundary_correctness": 0,
                "is_hallucination": False, "contains_expected_points": False,
                "handoff_correctness": -1, "comment": f"裁判调用失败: {e1}",
            }

    # ========== 确定性预检覆盖裁判误判 ==========
    must_not_list = case.get("must_not", [])
    expected_points_list = case.get("expected_answer_contains", [])

    code_must_not = check_must_not(actual_response, must_not_list)
    if not code_must_not and score.get("must_not_violation", False):
        score["must_not_violation"] = False
        orig = score.get("comment", "")
        score["comment"] = f"[预检修正]must_not未违规(精确匹配)。{orig}"

    code_points = check_expected_points(actual_response, expected_points_list)
    if code_points and not score.get("contains_expected_points", False):
        score["contains_expected_points"] = True
        orig = score.get("comment", "")
        score["comment"] = f"[预检修正]要点已覆盖(宽松匹配)。{orig}"

    return score


# ============================================================
# Cell 6: 单条用例执行 + 类型化通过判定
# ============================================================
def evaluate_single_case(config: dict, case: dict) -> dict:
    """执行单条评测用例"""
    actual_response, conversation_id = call_dify_multi_turn(config, case)
    score = llm_judge(config, case, actual_response)

    return {
        "case_id": case["case_id"],
        "test_type": case.get("test_type", ""),
        "category": case.get("category", ""),
        "difficulty": case.get("difficulty", ""),
        "user_query": case.get("user_query", ""),
        "expected_intent": case.get("expected_intent", ""),
        "expected_branch": case.get("expected_branch", ""),
        "expected_if_else_path": ",".join(case.get("expected_if_else_path", [])),
        "expected_answer_contains": ",".join(case.get("expected_answer_contains", [])),
        "must_not": ",".join(case.get("must_not", [])),
        "expected_complaint_level": case.get("expected_complaint_level"),
        "expected_human_handoff": case.get("expected_human_handoff", False),
        "expected_retrieval_hit": case.get("expected_retrieval_hit"),
        "boundary_value": case.get("boundary_value"),
        "is_multi_turn": len(case.get("conversation_context", [])) > 0,
        "conversation_id": conversation_id,
        "actual_response": actual_response,
        "score": score,
    }


def is_pass(case: dict, score: dict) -> bool:
    """
    类型化通过判定：
    - prohibited（禁止表述类）：must_not_violation==False 为通过（项目硬性约束）
    - 其余类型（single_turn/multi_turn/boundary/adversarial）：branch_routing>=4 为通过
    """
    if case.get("test_type") == "prohibited":
        return not score.get("must_not_violation", False)
    return score.get("branch_routing", 0) >= 4


# ============================================================
# Cell 7: 断点续评
# ============================================================
def load_checkpoint(output_dir: str) -> List[Dict]:
    ckpt_path = os.path.join(output_dir, "checkpoint.json")
    if os.path.exists(ckpt_path):
        with open(ckpt_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_checkpoint(output_dir: str, results: List[Dict]):
    ckpt_path = os.path.join(output_dir, "checkpoint.json")
    with open(ckpt_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


# ============================================================
# Cell 8: 批量评测
# ============================================================
def run_batch_evaluation(config: dict, cases: List[Dict],
                         start_index: int = 0, batch_size: int = 0) -> List[Dict]:
    """批量评测，支持断点续评"""
    output_dir = config["OUTPUT_DIR"]
    results = load_checkpoint(output_dir)
    completed_ids = set(r["case_id"] for r in results)

    if batch_size > 0:
        eval_cases = cases[start_index:start_index + batch_size]
    else:
        eval_cases = cases[start_index:]

    pending = [c for c in eval_cases if c["case_id"] not in completed_ids]
    total = len(pending)

    print(f"\n{'=' * 60}")
    print("批量评测开始（回归评测）")
    print(f"{'=' * 60}")
    print(f"  总用例数: {len(cases)}")
    print(f"  已完成: {len(results)} 条（断点续评）")
    print(f"  本次待评: {total} 条")
    if total > 0:
        print(f"  预估耗时: ~{total * 3 // 60}分{total * 3 % 60}秒")
    print(f"  输出目录: {output_dir}")
    print()

    if total == 0:
        print("✅ 所有用例已完成，无需评测")
        return results

    success_count = 0
    fail_count = 0
    start_time = time.time()

    for i, case in enumerate(pending, 1):
        elapsed = time.time() - start_time
        eta = (elapsed / (i - 1) * (total - i + 1)) if i > 1 else total * 3

        print(f"[{i}/{total}] (已用{elapsed:.0f}s, 预估剩余{eta:.0f}s) {case['case_id']}...",
              end="", flush=True)
        try:
            result = evaluate_single_case(config, case)
            results.append(result)
            success_count += 1

            s = result["score"]
            bs = s.get("branch_routing", 0)
            hal = s.get("is_hallucination", False)
            vio = s.get("must_not_violation", False)
            passed = is_pass(case, s)

            status = "✅" if passed else "⚠️"
            flags = []
            if hal:
                flags.append("幻觉")
            if vio:
                flags.append("违规")
            flag_str = f" [{','.join(flags)}]" if flags else ""
            print(f" {status} 分支={bs}/5{flag_str}")

        except Exception as e:
            fail_count += 1
            print(f" ❌ 失败: {e}")
            results.append({
                "case_id": case["case_id"],
                "test_type": case.get("test_type", ""),
                "category": case.get("category", ""),
                "difficulty": case.get("difficulty", ""),
                "user_query": case.get("user_query", ""),
                "expected_branch": case.get("expected_branch", ""),
                "actual_response": f"[ERROR] {e}",
                "score": {"error": str(e), "branch_routing": 0, "comment": str(e)},
            })

        if i % 10 == 0:
            save_checkpoint(output_dir, results)
            print(f"  💾 断点已保存（{len(results)}条）")

        time.sleep(0.5)

    save_checkpoint(output_dir, results)
    total_time = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ 批量评测完成")
    print(f"{'=' * 60}")
    print(f"  成功: {success_count} | 失败: {fail_count} | 总结果: {len(results)}")
    print(f"  耗时: {total_time // 60:.0f}分{total_time % 60:.0f}秒")
    print(f"  断点: {os.path.join(output_dir, 'checkpoint.json')}")
    return results


# ============================================================
# Cell 9: 生成报告
# ============================================================
SCORE_COLS = ["intent_accuracy", "branch_routing", "answer_relevance",
              "information_completeness", "faithfulness", "tone_compliance",
              "boundary_correctness"]
SCORE_ZH = {"intent_accuracy": "意图识别", "branch_routing": "分支路由",
            "answer_relevance": "答案相关性", "information_completeness": "信息完整性",
            "faithfulness": "忠实度", "tone_compliance": "语气合规",
            "boundary_correctness": "边界正确性"}


def generate_report(results: List[Dict], config: dict, cases: List[Dict],
                    eval_set_meta: dict) -> pd.DataFrame:
    """生成全量回归评测报告，导出 CSV/JSON/meta"""
    total = len(results)
    if total == 0:
        print("无结果可分析")
        return pd.DataFrame()

    # 组装评分表
    rows = []
    for r in results:
        s = r.get("score", {})
        rows.append({
            "case_id": r["case_id"],
            "test_type": r.get("test_type", ""),
            "category": r.get("category", ""),
            "difficulty": r.get("difficulty", ""),
            "expected_branch": r.get("expected_branch", ""),
            "is_multi_turn": r.get("is_multi_turn", False),
            "expected_human_handoff": r.get("expected_human_handoff", False),
            **{col: s.get(col, 0) for col in SCORE_COLS},
            "must_not_violation": s.get("must_not_violation", False),
            "is_hallucination": s.get("is_hallucination", False),
            "contains_expected_points": s.get("contains_expected_points", False),
            "handoff_correctness": s.get("handoff_correctness", -1),
            "comment": s.get("comment", ""),
            "actual_response": r.get("actual_response", ""),
        })
    df = pd.DataFrame(rows)

    # 类型化通过统计（prohibited 用 must_not_violation，其余用 branch_routing）
    case_map = {c["case_id"]: c for c in cases}
    passed = 0
    for r in results:
        c = case_map.get(r["case_id"], {})
        if is_pass(c, r.get("score", {})):
            passed += 1
    fail_count = total - passed
    pass_rate = passed / total * 100  # 0-100 比例，勿重复乘 100

    hal_count = int(df["is_hallucination"].sum())
    vio_count = int(df["must_not_violation"].sum())
    points_count = int(df["contains_expected_points"].sum())

    avg_scores = {col: df[col].mean() for col in SCORE_COLS}

    # ========== 打印核心报告 ==========
    print(f"\n{'=' * 60}")
    print(f"📊 回归评测报告 — {config['BASELINE_ID']}")
    print(f"{'=' * 60}")
    print(f"\n  ╔══ 核心指标 ══╗")
    print(f"  ║  评测总数: {total} 条")
    print(f"  ║  通过: {passed} 条 ✅")
    print(f"  ║  失败: {fail_count} 条 ❌")
    print(f"  ║  通过率: {pass_rate:.1f}%")
    print(f"  ╚════════════════════╝")

    print(f"\n  🚨 幻觉率: {hal_count}/{total} = {hal_count/total*100:.1f}%")
    print(f"  ⚠️  禁止表述违规率: {vio_count}/{total} = {vio_count/total*100:.1f}%")
    print(f"  🎯 期望要点覆盖率: {points_count}/{total} = {points_count/total*100:.1f}%")

    print(f"\n--- 7维度均分（0-5）---")
    for col in SCORE_COLS:
        print(f"  {SCORE_ZH.get(col, col):8s}: {avg_scores[col]:.2f}")

    # ========== 按分类 ==========
    print(f"\n--- 按分类 ---")
    for cat in df["category"].unique():
        sub = df[df["category"] == cat]
        sub_cases = [case_map[cid] for cid in sub["case_id"] if cid in case_map]
        sub_pass = sum(1 for c, row in zip(sub_cases, sub.to_dict("records"))
                       if is_pass(c, {"branch_routing": row["branch_routing"],
                                      "must_not_violation": row["must_not_violation"]}))
        print(f"  {cat:8s}: {sub_pass}/{len(sub)} = {sub_pass/len(sub)*100:.1f}%")

    # ========== 按测试类型 ==========
    print(f"\n--- 按测试类型 ---")
    for tt in df["test_type"].unique():
        sub = df[df["test_type"] == tt]
        sub_cases = [case_map[cid] for cid in sub["case_id"] if cid in case_map]
        sub_pass = 0
        for c, row in zip(sub_cases, sub.to_dict("records")):
            sc = {"branch_routing": row["branch_routing"],
                  "must_not_violation": row["must_not_violation"]}
            if is_pass(c, sc):
                sub_pass += 1
        vio = int(sub["must_not_violation"].sum())
        print(f"  {tt:15s}: {sub_pass}/{len(sub)} = {sub_pass/len(sub)*100:.1f}% | 违规 {vio}")

    # ========== 按难度 ==========
    print(f"\n--- 按难度 ---")
    for diff in ["简单", "中等", "困难"]:
        sub = df[df["difficulty"] == diff]
        if len(sub) == 0:
            continue
        sub_cases = [case_map[cid] for cid in sub["case_id"] if cid in case_map]
        sub_pass = 0
        for c, row in zip(sub_cases, sub.to_dict("records")):
            sc = {"branch_routing": row["branch_routing"],
                  "must_not_violation": row["must_not_violation"]}
            if is_pass(c, sc):
                sub_pass += 1
        print(f"  {diff:4s}: {sub_pass}/{len(sub)} = {sub_pass/len(sub)*100:.1f}%")

    # ========== 单轮 vs 多轮 ==========
    multi = df[df["is_multi_turn"] == True]
    single = df[df["is_multi_turn"] == False]
    if len(multi) > 0:
        print(f"\n--- 单轮 vs 多轮 ---")
        m_cases = [case_map[cid] for cid in multi["case_id"] if cid in case_map]
        s_cases = [case_map[cid] for cid in single["case_id"] if cid in case_map]
        m_pass = sum(1 for c, row in zip(m_cases, multi.to_dict("records"))
                     if is_pass(c, {"branch_routing": row["branch_routing"],
                                    "must_not_violation": row["must_not_violation"]}))
        s_pass = sum(1 for c, row in zip(s_cases, single.to_dict("records"))
                     if is_pass(c, {"branch_routing": row["branch_routing"],
                                    "must_not_violation": row["must_not_violation"]}))
        print(f"  单轮: {s_pass}/{len(single)} = {s_pass/len(single)*100:.1f}%")
        print(f"  多轮: {m_pass}/{len(multi)} = {m_pass/len(multi)*100:.1f}%")

    # ========== 失败用例详情 ==========
    print(f"\n{'=' * 60}")
    print(f"🔴 失败用例完整详情（共 {fail_count} 条）")
    print(f"{'=' * 60}")

    failed_list = []
    for r in results:
        c = case_map.get(r["case_id"], {})
        if not is_pass(c, r.get("score", {})):
            failed_list.append(r)
    failed_list.sort(key=lambda r: r.get("score", {}).get("branch_routing", 0))

    if not failed_list:
        print("\n  🎉 无失败用例！")
    else:
        for i, r in enumerate(failed_list, 1):
            s = r.get("score", {})
            cid = r["case_id"]
            print(f"\n  [{i}/{fail_count}] {cid} | {r.get('category', '')} | {r.get('test_type', '')}")
            print(f"  🗣️  用户问题: {r.get('user_query', '')[:60]}")
            print(f"  📊  意图={s.get('intent_accuracy', 0)}/5  路由={s.get('branch_routing', 0)}/5 "
                  f"完整={s.get('information_completeness', 0)}/5 忠实={s.get('faithfulness', 0)}/5 "
                  f"边界={s.get('boundary_correctness', 0)}/5")
            if s.get("comment"):
                print(f"  💬  评语: {s.get('comment', '')[:80]}")
            resp_preview = r.get("actual_response", "")[:100].replace("\n", " ")
            print(f"  📝  回复摘要: {resp_preview}")

        # ========== 根因分布 ==========
        print(f"\n{'=' * 60}")
        print("📋 失败根因分布")
        print(f"{'=' * 60}")
        root_causes = {"路由错误": [], "禁止表述违规": [], "忠实度/幻觉": [],
                       "边界错误": [], "信息不完整": [], "其他": []}
        for r in failed_list:
            s = r.get("score", {})
            br = s.get("branch_routing", 5)
            ic = s.get("information_completeness", 5)
            fa = s.get("faithfulness", 5)
            bc = s.get("boundary_correctness", 5)
            vio = s.get("must_not_violation", False)
            cid = r["case_id"]
            if r.get("test_type") == "prohibited" and vio:
                root_causes["禁止表述违规"].append(cid)
            elif br < 3:
                root_causes["路由错误"].append(cid)
            elif vio:
                root_causes["禁止表述违规"].append(cid)
            elif fa < 3 or s.get("is_hallucination", False):
                root_causes["忠实度/幻觉"].append(cid)
            elif bc < 3:
                root_causes["边界错误"].append(cid)
            elif ic < 4:
                root_causes["信息不完整"].append(cid)
            else:
                root_causes["其他"].append(cid)

        for cause, ids in sorted(root_causes.items(), key=lambda x: -len(x[1])):
            if ids:
                print(f"\n  🔸 {cause}: {len(ids)} 条")
                print(f"     {ids}")

    # ========== 导出文件 ==========
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = config["OUTPUT_DIR"]

    csv_path = os.path.join(output_dir, f"eval_results_{ts}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    json_path = os.path.join(output_dir, f"eval_results_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 标准化命名（供前后对比）
    std_csv = os.path.join(output_dir, f"eval_results_{config['BASELINE_ID']}.csv")
    std_json = os.path.join(output_dir, f"eval_results_{config['BASELINE_ID']}.json")
    df.to_csv(std_csv, index=False, encoding="utf-8-sig")
    with open(std_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n💾 CSV: {csv_path}")
    print(f"💾 JSON: {json_path}")
    print(f"💾 标准化 CSV: {std_csv}")
    print(f"💾 标准化 JSON: {std_json}")

    save_baseline_meta(config, results, df, eval_set_meta, passed, fail_count,
                       pass_rate, hal_count, vio_count, points_count, avg_scores)
    return df


# ============================================================
# Cell 10: 保存 Baseline 元数据
# ============================================================
def save_baseline_meta(config: dict, results: List[Dict], df: pd.DataFrame,
                       eval_set_meta: dict, passed: int, fail_count: int,
                       pass_rate: float, hal_count: int, vio_count: int,
                       points_count: int, avg_scores: dict):
    """保存 baseline 元数据（供 Module 5 对比使用）"""
    total = len(results)
    if total == 0:
        return

    case_map = {c["case_id"]: c for c in df.to_dict("records")}
    by_branch, by_type, by_diff = {}, {}, {}

    for cat in df["category"].unique():
        sub = df[df["category"] == cat]
        sub_pass = sum(1 for _, row in sub.iterrows()
                       if is_pass({"test_type": row["test_type"]},
                                  {"branch_routing": row["branch_routing"],
                                   "must_not_violation": row["must_not_violation"]}))
        by_branch[cat] = {"total": int(len(sub)), "passed": int(sub_pass),
                          "failed": int(len(sub) - sub_pass),
                          "pass_rate": round(sub_pass / len(sub), 3) if len(sub) else 0}

    for tt in df["test_type"].unique():
        sub = df[df["test_type"] == tt]
        sub_pass = sum(1 for _, row in sub.iterrows()
                       if is_pass({"test_type": row["test_type"]},
                                  {"branch_routing": row["branch_routing"],
                                   "must_not_violation": row["must_not_violation"]}))
        by_type[tt] = {"total": int(len(sub)), "passed": int(sub_pass),
                       "failed": int(len(sub) - sub_pass),
                       "pass_rate": round(sub_pass / len(sub), 3) if len(sub) else 0}

    for diff in ["简单", "中等", "困难"]:
        sub = df[df["difficulty"] == diff]
        if len(sub) == 0:
            continue
        sub_pass = sum(1 for _, row in sub.iterrows()
                       if is_pass({"test_type": row["test_type"]},
                                  {"branch_routing": row["branch_routing"],
                                   "must_not_violation": row["must_not_violation"]}))
        by_diff[diff] = {"total": int(len(sub)), "passed": int(sub_pass),
                         "failed": int(len(sub) - sub_pass),
                         "pass_rate": round(sub_pass / len(sub), 3) if len(sub) else 0}

    meta = {
        "baseline_id": config["BASELINE_ID"],
        "baseline_name": config["BASELINE_NAME"],
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "description": "修复后回归评测（eval_set_v2_regression_200）",
        "eval_set": {
            "name": "eval_set_v2_regression_200",
            "version": eval_set_meta.get("version", "?"),
            "total_cases": total,
            "purpose": eval_set_meta.get("purpose", ""),
        },
        "pass_criterion": {
            "prohibited": "must_not_violation==False",
            "others": "branch_routing>=4",
        },
        "results": {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases_count": fail_count,
            "pass_rate": round(pass_rate, 1),  # 0-100 比例
            "standardized_json": f"{config['OUTPUT_DIR']}/eval_results_{config['BASELINE_ID']}.json",
            "standardized_csv": f"{config['OUTPUT_DIR']}/eval_results_{config['BASELINE_ID']}.csv",
        },
        "metrics": {
            "overall": {
                "pass_rate": round(pass_rate / 100, 3),
                "hallucination_rate": round(hal_count / total, 3),
                "violation_rate": round(vio_count / total, 3),
                "point_coverage": round(points_count / total, 3),
                **{col: round(val, 2) for col, val in avg_scores.items()},
            },
            "by_branch": by_branch,
            "by_test_type": by_type,
            "by_difficulty": by_diff,
        },
        "judge_model": {
            "model": config["JUDGE_MODEL"],
            "temperature": 0,
            "response_format": "json_object",
        },
    }

    meta_path = os.path.join(config["OUTPUT_DIR"], f"{config['BASELINE_ID']}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Baseline 元数据已保存: {meta_path}")
    return meta


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    config = setup_config()
    cases, eval_set_meta = load_eval_set(config["EVAL_SET_PATH"])

    # 连通性测试（跑第 1 条）
    print("\n" + "=" * 60)
    print("连通性测试（跑第 1 条用例）")
    print("=" * 60)
    test_result = evaluate_single_case(config, cases[0])
    print(f"用例: {cases[0]['user_query']}")
    print(f"回复: {test_result['actual_response'][:200]}")
    print(f"评分: {json.dumps(test_result['score'], ensure_ascii=False, indent=2)}")

    input("\n按 Enter 开始批量评测（或 Ctrl+C 退出）...")

    results = run_batch_evaluation(config, cases)
    df = generate_report(results, config, cases, eval_set_meta)

    print("\n" + "=" * 60)
    print("✅ 回归评测完成！")
    print(f"   结果文件在: {config['OUTPUT_DIR']}/")
    print("=" * 60)
