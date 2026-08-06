"""规则意图解析：正则规则 1:1 迁移 intent.ts。

norm() 先去空白与标点再小写；规则顺序与 TS 版 RULES 数组完全一致（先匹配先赢）。
返回 dict：{"name": ..., "slots": {...}, "raw_text": ...}。
"""
import re

MOTION_INTENTS = [
    "MOVE_FWD",
    "MOVE_BACK",
    "TURN_LEFT",
    "TURN_RIGHT",
    "SPEED_UP",
    "SPEED_DOWN",
    "SPEED_SET",
]

# 货叉升降按"运动级"处理：需要现场锁 + cabin 通道需唤醒武装（与 MOTION 一致）
FORK_INTENTS = ["FORK_LIFT_UP", "FORK_LIFT_DOWN", "FORK_LIFT_TO"]

# TASK_* 全部按运动级：needs_site + cabin 需唤醒
TASK_INTENTS = ["TASK_HEAD", "TASK_FOLLOW_BACK", "TASK_GET_PALLET", "TASK_CHARGE"]


# ASR 同音误识别纠偏表：piper/真人说出的部分指令词被 14M 小模型稳定识别为同音词
# （如 电量→掂量、前进→前经），对固定指令词表在 parse_intent 前做确定性纠偏。
# 注意：长词优先（replace 顺序按 key 长度降序排列，避免短词先替换破坏长词）。
ASR_CORRECTIONS = {
    # 货叉
    "身体或差": "升起货叉", "身体货叉": "升起货叉", "方侠或差": "放下货叉",
    "方侠货叉": "放下货叉", "灯下锅菜": "放下货叉", "或差": "货叉", "锅菜": "货叉",
    # 数字单位
    "号米": "毫米", "万毫米": "毫米", "一百五十": "150", "九十": "90", "就十": "90", "就时": "90",
    # 点动
    "掂量": "电量", "前经": "前进", "作战": "左转", "高晨": "后退", "夫妻": "后退",
    # 任务
    "同类点": "A点", "低点": "B点", "地点": "B点", "站板": "栈板", "站吧": "栈板",
    # 唤醒（玖物 同音字组已在 match_wake_word 等价组覆盖，此处兜底）
    "九屋": "玖物", "九乌": "玖物", "九五": "玖物",
    # 任务流
    "也是流程": "演示流程", "单凭任务": "暂停任务", "身体愿意": "暂停任务", "三庭任务": "暂停任务",
}


def correct_asr(text: str) -> str:
    # 长词优先替换，避免短词先命中破坏长词（如"或差"先于"身体或差"）
    for wrong in sorted(ASR_CORRECTIONS, key=len, reverse=True):
        text = text.replace(wrong, ASR_CORRECTIONS[wrong])
    return text


def norm(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    # 删标点，但保留小数点（前后都是数字的 . 如 1.5米），否则 1.5→15 出错
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", "", text)  # 删非小数点
    text = re.sub(r"[，,。!！？?]", "", text)
    return text.lower()


def _slot_speed_set(m: re.Match) -> dict:
    return {"n": min(100, int(m.group(1) or m.group(0)))}


def _slot_goto(m: re.Match) -> dict:
    return {"goal": re.sub(r"(站点|点)$", "", m.group(1) or "")}


# 长度单位 → mm 换算（FORK_LIFT_TO slot 统一成毫米）
_UNIT_TO_MM = {"毫米": 1, "mm": 1, "厘米": 10, "cm": 10, "米": 1000, "m": 1000}


def _slot_fork_to(m: re.Match) -> dict:
    value = float(m.group(1))
    unit = m.group(2) or "毫米"
    mm = value * _UNIT_TO_MM.get(unit, 1)
    return {"n": int(mm + 0.5)}


def _slot_head(m: re.Match) -> dict:
    """TASK_HEAD：角度（左正右负，无方向默认正）；掉头=180；无角度 → {}（触发追问）。"""
    if m.re.pattern == r"掉头":
        return {"angle": 180}
    direction = None
    value = None
    for g in m.groups():
        if g in ("左", "右"):
            direction = g
        elif g and re.fullmatch(r"\d+(\.\d+)?", g):
            value = float(g)
    if value is None:
        return {}
    angle = value if direction != "右" else -value
    return {"angle": int(angle) if angle == int(angle) else angle}


def _slot_follow_back(m: re.Match) -> dict:
    """TASK_FOLLOW_BACK：start_name/target_name 保留地图点原名（点/区不剥，
    仅去尾部"站点"语气词——与 GOTO 剥"站点|点"不同，这里保真优先，注释见计划步骤28）；
    带 盲叉|叉取|取货 时 get_pallet=true。"""
    slots = {}
    groups = [g for g in m.groups() if g]
    if len(groups) >= 2:
        slots["start_name"] = re.sub(r"站点$", "", groups[0])
        slots["target_name"] = re.sub(r"站点$", "", groups[1])
    if re.search(r"盲叉|叉取|取货", m.string):
        slots["get_pallet"] = True
    return slots


def _slot_charge(m: re.Match) -> dict:
    """TASK_CHARGE：goal = 充电桩名（含"充电桩/充电站"后缀，如 "1号充电桩"）。"""
    groups = [g for g in m.groups() if g]
    if len(groups) >= 2:
        return {"goal": groups[0] + groups[1]}
    if groups:
        return {"goal": groups[0]}
    return {}


def _slot_flow_name(m: re.Match) -> dict:
    """FLOW_START：流程名剥尾部"任务流|任务|流程"语气词（group2=名称）。"""
    name = re.sub(r"(任务流|任务|流程)$", "", m.group(2) or "")
    return {"name": name}


RULES = [
    # CONFIRM/CANCEL 必须在 STOP 之前："停止确认" 含 "停止"，先匹配先赢
    {"name": "CONFIRM", "patterns": [r"^(确认|是的|好|执行|对的?)$"]},
    {"name": "CANCEL", "patterns": [r"^(取消|算了|不用了|停止确认)$"]},
    # FLOW_* 流控意图：CANCEL 为全匹配不吞"取消任务"；"执行"单字归 CONFIRM（全匹配），
    # "执行XX" 才归 FLOW_START，互不冲突
    {"name": "FLOW_START", "patterns": [r"^(执行|开始|运行)(.+)$"], "slot": _slot_flow_name},
    {"name": "FLOW_PAUSE", "patterns": [r"^暂停(任务|任务流)$"]},
    {"name": "FLOW_RESUME", "patterns": [r"^继续(任务|任务流)$"]},
    {"name": "FLOW_CANCEL", "patterns": [r"^取消(任务|任务流)$"]},
    {"name": "STOP", "patterns": [r"停止", r"停下", r"停车", r"急停", r"^停$"]},
    # TASK_HEAD 必须在 TURN_LEFT/RIGHT 之前：带"度"的转向是原地旋转任务，
    # 无角度的连续转向仍走 TURN_*（点动），两者零冲突（有"度"才命中 TASK_HEAD）
    {
        "name": "TASK_HEAD",
        "patterns": [
            r"原地(?:旋转|转)(?:向)?(左|右)?(\d+(?:\.\d+)?)?度?",
            r"(向)?(左|右)?转(\d+(?:\.\d+)?)度",
            r"掉头",
        ],
        "slot": _slot_head,
    },
    {"name": "MOVE_FWD", "patterns": [r"前进", r"往前", r"向前", r"走吧", r"^走$"]},
    {"name": "MOVE_BACK", "patterns": [r"后退", r"往后", r"向后", r"倒车"]},
    {"name": "TURN_LEFT", "patterns": [r"左转", r"向左", r"往左"]},
    {"name": "TURN_RIGHT", "patterns": [r"右转", r"向右", r"往右"]},
    # 货叉意图须在 SPEED_SET 之前：FORK_LIFT_TO 的 "调到150毫米" 会被
    # SPEED_SET 的 /速度(?:调到|设为|到)?(\d{1,3})%?/ 之外规则误吞的风险点在于
    # 裸 "调到210"，因此货叉规则要求带 升/降 字眼、或 "货叉调"、或带长度单位；
    # SPEED_SET 保留 "速度" 前缀或 % 号，两者不冲突。
    {"name": "FORK_LIFT_UP",
     "patterns": [r"升起|上升|抬起来?|升叉", r"升(一)?点", r"高一点"]},
    {"name": "FORK_LIFT_DOWN",
     "patterns": [r"放下|下降|降下来?|降叉", r"降(一)?点", r"低一点"]},
    {
        "name": "FORK_LIFT_TO",
        "patterns": [
            r"(?:升|降)(?:到|至|为)?(\d+(?:\.\d+)?)(毫米|mm|厘米|cm|米|m)?",
            r"货叉调(?:到|至|为)?(\d+(?:\.\d+)?)(毫米|mm|厘米|cm|米|m)?",
            r"调(?:到|至|为)?(\d+(?:\.\d+)?)(毫米|mm|厘米|cm|米|m)",
        ],
        "slot": _slot_fork_to,
    },
    {
        "name": "SPEED_SET",
        "patterns": [r"速度(?:调到|设为|到)?(\d{1,3})%?", r"(\d{1,3})\s*%"],
        "slot": _slot_speed_set,
    },
    {"name": "SPEED_UP", "patterns": [r"快点", r"加速", r"快一点"]},
    {"name": "SPEED_DOWN", "patterns": [r"慢点", r"减速", r"慢一点"]},
    # TASK_GET_PALLET / TASK_FOLLOW_BACK 必须在 GOTO_GOAL 之前：
    # "从A点去B点" 含 "去B点" 会被 GOTO 吞掉；GOTO 的 "去X" 无 "从" 不受影响
    {"name": "TASK_GET_PALLET",
     "patterns": [r"识别栈板", r"栈板识别", r"相机取货", r"自动取货", r"叉取栈板"]},
    {
        "name": "TASK_FOLLOW_BACK",
        "patterns": [
            r"从(.+?)到(.+)$",
            r"从(.+?)去(.+)$",
            r"盲叉",  # 裸"盲叉取货"：get_pallet=true，起止点缺失 → ParamDialogue 追问
        ],
        "slot": _slot_follow_back,
    },
    # TASK_CHARGE 必须在 DOCK 之前：带桩名的"去X充电桩充电"含"充电"会被 DOCK 吞；
    # 无名字的"回充/去充电"仍走 DOCK 不动
    {
        "name": "TASK_CHARGE",
        "patterns": [r"去(.+?)(充电桩|充电站)充电", r"去(.+?)充电"],
        "slot": _slot_charge,
    },
    {"name": "IDLE", "patterns": [r"空闲", r"待机"]},
    {"name": "DOCK", "patterns": [r"回充", r"去充电", r"充电"]},
    {
        "name": "GOTO_GOAL",
        "patterns": [r"去(?:往|到)?(.+?)(?:站点|点)?$", r"前往(.+)$"],
        "slot": _slot_goto,
    },
    {"name": "QUERY_BATTERY", "patterns": [r"电量", r"还有多少电", r"电池"]},
    {"name": "QUERY_MODE", "patterns": [r"什么模式", r"当前模式", r"模式"]},
    {"name": "QUERY_POSE", "patterns": [r"位置", r"在哪", r"坐标"]},
    {"name": "QUERY_FORK_HEIGHT", "patterns": [r"叉高", r"货叉多高", r"货叉高度"]},
    {"name": "QUERY_MOTOR", "patterns": [r"电机", r"使能"]},
    # QUERY_SPEED：必须带"多少|多快"等疑问词，与 SPEED_UP/DOWN/SET 零冲突
    {"name": "QUERY_SPEED", "patterns": [r"速度多少", r"当前速度", r"多快", r"跑多快"]},
    {"name": "QUERY_TASK", "patterns": [r"什么任务", r"当前任务", r"在干(嘛|什么)", r"任务进度", r"执行到哪"]},
    # QUERY_ALARM_EXPLAIN 必须在 QUERY_ALARM 之前："怎么了/为什么停"归解释类；
    # QUERY_ALARM 只保留 告警/报警 的状态播报
    {"name": "QUERY_ALARM_EXPLAIN",
     "patterns": [r"为什么停", r"怎么回事", r"怎么了", r"报警原因", r"什么故障", r"为啥停"]},
    {"name": "QUERY_ALARM", "patterns": [r"告警", r"报警"]},
]


def parse_intent_rule(raw_text: str) -> dict:
    text = norm(raw_text)
    if not text:
        return {"name": "UNKNOWN", "slots": {}, "raw_text": raw_text, "span": (0, 0)}
    for rule in RULES:
        for pattern in rule["patterns"]:
            m = re.search(pattern, text)
            if m:
                slot_fn = rule.get("slot")
                return {
                    "name": rule["name"],
                    "slots": slot_fn(m) if slot_fn else {},
                    "raw_text": raw_text,
                    # 命中区间（norm 后文本上的 span），router 用于复合指令覆盖率判断
                    "span": m.span(),
                }
    return {"name": "UNKNOWN", "slots": {}, "raw_text": raw_text, "span": (0, 0)}


# 唤醒词同音字等价组：ASR 常把 "玖物玖物" 识别成 "九屋九物/九乌九物" 等同音串，
# 匹配与剥离都按等价组做字符类模糊匹配（与 TS 版收编 "九物九物" 同一思路，更通用）。
WAKE_HOMOPHONE_GROUPS = [
    set("玖九酒久"),
    set("物屋乌五务舞武"),
]


def _wake_char_pattern(ch: str) -> str:
    for g in WAKE_HOMOPHONE_GROUPS:
        if ch in g:
            return "[" + "".join(sorted(g)) + "]"
    return re.escape(ch)


def wake_word_pattern(wake_word: str) -> str:
    """唤醒词 → 同音字字符类正则；字符间允许可选空白/逗号（对齐 TS 玖物[，,]?玖物）。"""
    chars = norm(wake_word)
    return r"[\s，,]?".join(_wake_char_pattern(c) for c in chars)


def match_wake_word(raw_text: str, wake_words: list) -> bool:
    text = norm(raw_text)
    return any(re.search(wake_word_pattern(w), text) for w in wake_words)
