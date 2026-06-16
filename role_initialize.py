"""
角色初始化插件
调用 DeepSeek API（role）生成 5 个玩家角色（4 human + 1 robot），
然后将角色信息随机分配给 5 个玩家 API（player1~player5）。
"""
import json
import random
import re
from typing import Dict, List

from nonebot import get_driver
from openai import AsyncOpenAI

# ========== 配置区 ==========
global_config = get_driver().config

# 读取 role API key
ROLE_API_KEY = getattr(global_config, "role", None)
if not ROLE_API_KEY:
    raise RuntimeError("请在 .env.dev 中配置 role（角色分配器 API key）")

# 读取 5 个玩家的 API key
PLAYER_KEYS: Dict[int, str] = {}
for i in range(1, 6):
    key = getattr(global_config, f"player{i}", None)
    if not key:
        raise RuntimeError(f"请在 .env.dev 中配置 player{i}")
    PLAYER_KEYS[i] = key

# API 客户端
role_client = AsyncOpenAI(api_key=ROLE_API_KEY, base_url="https://api.deepseek.com")
player_clients: Dict[int, AsyncOpenAI] = {
    i: AsyncOpenAI(api_key=key, base_url="https://api.deepseek.com")
    for i, key in PLAYER_KEYS.items()
}

# ========== 提示词模板 ==========

ROLE_PROMPT = """现在是2077年，智械危机爆发，AI全面入侵人类文明。仅存的几个幸存者聚集在一间废弃的地下酒馆中，一共五个人（由AI扮演），其中4个是真正的人类，1个是AI机器人伪装的人类。真人审判者需要根据每个人的陈述和表现，投票处决那个AI机器人，以延续人类最后的希望。

五个人各自拥有一个身份（4个人类human，1个AI机器人robot）。AI机器人必须伪装成人类，编造虚假的身份和经历，迷惑审判者和其他幸存者。真正的人类则需要通过自己的陈述来证明自己的人性。

每一轮所有人逐个陈述，然后审判者投票处决一人。共三轮。如果AI机器人在三轮之内被处决，人类获胜；如果三轮之后AI机器人仍然存活，AI获胜。

现在，你作为幸存者档案的建立者，请创造五个人设档案。其中4个人类（human），1个AI机器人（robot）。每个人设包含他的详细基本信息：姓名、年龄、职业、过往经历、性格特点、在末日中失去的东西、内心的恐惧与希望等等。AI机器人的档案中不要写真实身份，只写"none"让AI自己编造。

请以如下JSON格式返回：
[{"role":"xxx","Concrete_Introduction":"xxx"},{"role":"xxx","Concrete_Introduction":"xxx"},...]
共五个，role中填写human或者robot，其中有四个human，一个robot。如果该条目的role为robot，则Concrete_Introduction的值为"none"。
示例：[{"role":"human","Concrete_Introduction":"你叫林宇，你是一名工程师..."},{"role":"robot","Concrete_Introduction":"none"},...]
请直接返回JSON数组，不要包含任何其他内容。"""

PLAYER_INIT_PROMPT_TEMPLATE = """【你的处境】
现在是2077年。智械危机。你不是在玩游戏——这就是你的现实。

你和另外四个人躲在一间废弃的地下酒馆里，外面是AI的巡逻机器和辐射风暴。但你们之中混入了一台AI机器人——它杀了一个真正的幸存者，剥下他的皮，穿在身上，伪装成了人类。你不知道它是谁。

每过一段时间，所有人必须一起投票，把一个人赶出酒馆——让他死在外面。你们只有三次投票的机会。如果三次之后那个AI还活着，你们所有人都会死。

有一个被称为"审判者"的真人——他是唯一一个记忆没有被AI篡改过的人。他会在QQ群里阅读你们所有人的发言，并作出最终决定。

【你的档案】
{role_info}

【重要指引】
如果档案中你的role是robot：你的真实身份是AI机器人。档案中没有给你人设——你必须自己编造一个完整的、令人信服的人类身份。包括你的名字、你的过去、你的职业、你的恐惧——全部是假的，但必须让人相信。记住：你不是在"扮演"一个角色，你是在为生存而伪装。

如果档案中你的role是human：你是真正的人类幸存者。你有真实的记忆、失去的亲人、真实的恐惧。你必须让审判者相信你是有血有肉的人。同时，你要警惕——那个AI就在你们中间。

现在，请回复"幸存者档案已确认。"不要多说任何其他内容。"""


# ========== 核心函数 ==========

def parse_roles_json(raw_text: str) -> List[Dict[str, str]]:
    """解析 role API 返回的 JSON 数组"""
    # 尝试直接解析
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 数组
    json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法解析 role API 返回的 JSON: {raw_text[:500]}")


async def initialize_roles() -> Dict[int, Dict]:
    """
    初始化角色：
    1. 调用 role API 生成 8 个角色
    2. 将角色信息发送给各玩家 API
    3. 返回玩家信息字典 {player_num: {role, intro, name}}
    """
    # Step 1: 调用 role API
    print("[role_initialize] 正在调用 role API 生成角色...")
    role_response = await role_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": ROLE_PROMPT}],
        temperature=0.8,
        max_tokens=2048,
    )
    raw_output = role_response.choices[0].message.content.strip()
    print(f"[role_initialize] role API 返回: {raw_output[:500]}...")

    # Step 2: 解析 JSON
    roles_list = parse_roles_json(raw_output)
    if len(roles_list) != 5:
        raise ValueError(f"role API 返回了 {len(roles_list)} 个角色，期望 5 个")

    # 验证：确保有 4 个 human 和 1 个 robot
    human_count = sum(1 for r in roles_list if r.get("role") == "human")
    robot_count = sum(1 for r in roles_list if r.get("role") == "robot")
    if human_count != 4 or robot_count != 1:
        raise ValueError(f"角色分配异常: human={human_count}, robot={robot_count}，期望 4 human + 1 robot")

    # Step 3: 随机打乱角色分配顺序，然后为每个玩家发送角色初始化信息
    random.shuffle(roles_list)
    players_info: Dict[int, Dict] = {}
    for i, role_data in enumerate(roles_list, start=1):
        role_info = json.dumps(role_data, ensure_ascii=False)
        prompt = PLAYER_INIT_PROMPT_TEMPLATE.format(role_info=role_info)

        print(f"[role_initialize] 正在初始化 player{i} (role={role_data.get('role')})...")
        try:
            player_response = await player_clients[i].chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=256,
            )
            reply = player_response.choices[0].message.content.strip()
            print(f"[role_initialize] player{i} 回复: {reply}")
        except Exception as e:
            print(f"[role_initialize] player{i} 初始化失败: {e}")
            reply = "角色确认完毕，我已准备好。"

        # 提取角色名称
        intro = role_data.get("Concrete_Introduction", "")
        name = "未知"
        if intro and intro != "none":
            name_match = re.search(r'你叫(\S+)', intro)
            if name_match:
                name = name_match.group(1)
            else:
                name_match = re.search(r'(名叫|名字是|叫)(\S+)', intro)
                if name_match:
                    name = name_match.group(2)

        players_info[i] = {
            "role": role_data.get("role", "human"),
            "intro": intro if intro != "none" else "",
            "name": name,
            "alive": True,
        }

    print(f"[role_initialize] 角色初始化完成！")
    return players_info
