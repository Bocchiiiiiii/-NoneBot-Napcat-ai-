"""
幸存者发言系统
每轮陈述时，从 player1 轮流发言到 player5。
已被处决的幸存者无法发言。
发言格式: [playerN]: 发言内容
"""
import asyncio
import re
from typing import Dict, List, Optional

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from openai import AsyncOpenAI

# ========== 配置区 ==========
global_config = get_driver().config

PLAYER_KEYS: Dict[int, str] = {}
for i in range(1, 6):
    key = getattr(global_config, f"player{i}", None)
    if not key:
        raise RuntimeError(f"请在 .env.dev 中配置 player{i}")
    PLAYER_KEYS[i] = key

player_clients: Dict[int, AsyncOpenAI] = {
    i: AsyncOpenAI(api_key=key, base_url="https://api.deepseek.com")
    for i, key in PLAYER_KEYS.items()
}


# ========== 辅助函数 ==========

def _calc_sleep(text: str, min_sec: float = 1.5, max_sec: float = 8.0) -> float:
    """根据文本长度计算阅读等待时间"""
    char_count = len(text)
    sleep_time = char_count / 4.5
    return max(min_sec, min(sleep_time, max_sec))


async def fetch_group_history(bot: Bot, group_id: int, limit: int = 120) -> List[str]:
    """获取群聊最近的聊天记录（120条）"""
    try:
        result = await bot.call_api("get_group_msg_history", group_id=group_id, count=limit)
        messages = result.get("messages", [])
        if not messages:
            return []
        formatted = []
        for msg in messages:
            user_name = msg.get("sender", {}).get("nickname", "未知用户")
            raw_msg = msg.get("message", "")
            text = ""
            if isinstance(raw_msg, str):
                text = raw_msg
            elif isinstance(raw_msg, list):
                for seg in raw_msg:
                    if seg.get("type") == "text":
                        text += seg.get("data", {}).get("text", "")
            if text.strip():
                formatted.append(f"{user_name}: {text.strip()}")
        return formatted[-limit:]
    except Exception as e:
        print(f"[speech] 获取群聊历史失败: {e}")
        return []


async def _handle_identify(bot: Bot, event: GroupMessageEvent, response_text: str, players_info: Dict, player_name: str) -> None:
    """检测 {identify:playerN} 标签，触发被指认者的自动辩解"""
    match = re.search(r'\{identify:player(\d+)\}', response_text, re.IGNORECASE)
    if not match:
        return
    target_num = int(match.group(1))
    if target_num < 1 or target_num > 5:
        return
    if not players_info.get(target_num, {}).get("alive", False):
        return

    target_info = players_info[target_num]
    target_name = target_info.get("name", f"player{target_num}")
    target_role = target_info.get("role", "human")
    target_intro = target_info.get("intro", "")

    await bot.send(
        event,
        f"[系统]: ...{player_name} 指认了 player{target_num}（{target_name}）！\n"
        f"[系统]: ...player{target_num}...你有什么要为自己辩护的？..."
    )
    await asyncio.sleep(1.0)

    # 为被指认者生成辩解
    if target_role == "robot":
        role_desc = "你是一台AI机器人，伪装成人类。你被指认了，必须拼命否认。"
        defense_instruction = "你被其他幸存者指认为AI！你必须愤怒地反驳，用细节和情感来证明你的'清白'。反咬对方一口——说对方才是真正的AI。你可以撒谎、扭曲事实、装委屈——只要能活下去。"
    else:
        role_desc = target_intro if target_intro else "你是一名真正的人类幸存者。"
        defense_instruction = "你被指认为AI！你是无辜的！你必须愤怒地为自己辩护。你是真正的人类，你有记忆、有恐惧、有失去的一切。用情感和细节证明你的人性。同时，你可以质疑指认你的人是否才是真正的AI。"

    defense_prompt = f"""【紧急情况】
你被 {player_name} 公开指认为AI机器人！现在所有人都在看着你。

【你的身份】
你是：player{target_num}（{target_name}）
{role_desc}

【你的回应策略】
{defense_instruction}

请立即用第一人称为自己辩护。300字以内。充满愤怒、委屈、恐惧——用人类的情感淹没他们的怀疑。言简意赅。
在辩护末尾，如果你怀疑另一个人，可以加上 {{identify:playerN}} 来指认对方（N为1-5的编号）。"""

    try:
        response = await player_clients[target_num].chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": defense_prompt},
                {"role": "user", "content": f"{player_name}指认你是AI！请立即为自己辩护！"},
            ],
            temperature=0.9,
            max_tokens=600,
        )
        defense = response.choices[0].message.content.strip()
        defense = re.sub(r'^\[player\d+\][:：]\s*', '', defense)
        defense = re.sub(r'^player\d+[:：说]\s*', '', defense)
        defense = defense.strip('"\'""''')
    except Exception as e:
        print(f"[speech] player{target_num} 辩解失败: {e}")
        defense = "你凭什么说我是AI？！你有什么证据？！我看你才是那个机器！"

    await bot.send(event, f"[player{target_num}的辩护]: {defense}")
    await asyncio.sleep(_calc_sleep(defense, 1.5, 8.0))

    # 递归检测辩护中的指认
    await _handle_identify(bot, event, defense, players_info, f"player{target_num}")


# ========== 提示词模板 ==========

SPEECH_SYSTEM_PROMPT = """【你的处境】
现在是2077年。智械危机爆发后，AI几乎屠杀了所有人类。你和另外四个人躲在一间废弃的地下酒馆里，这是人类最后的避难所。

但是，你们之中混入了一台AI机器人。它杀了真正的幸存者，剥下他的皮，穿在自己身上，伪装成了人类。它不会饿，不会痛，不会恐惧。它唯一的指令就是：不被发现，活到最后。

外面的世界已经死了。你们被困在这里。每过一段时间，你们必须投票处决一个人——把他赶出酒馆，让他死在外面的废墟中。你们只有三次投票的机会。如果三次之后，那个AI还活着……你们所有人都会被它杀死。一个接一个。

没有法律。没有道德。没有规则。活下去就是一切。

【你的身份】
你是：{player_name}
{role_desc}

【在场的所有人（共5人）】
{all_players_info}

【真人审判者】
在你们之中，有一位特殊的"真人审判者"——他是唯一没有被AI替换过记忆的人类。他会在QQ群里阅读你们的发言，并在每一轮投票中做出选择。他的一票可能决定你的生死。你必须说服他，让他相信你是真正的人类；同时，你可以怀疑其他人，让审判者把票投给别人。

【你的处境 — 重要！】
{survival_instruction}

【当前是第 {round_num} 轮陈述。{round_warning}】
【当前难度：{difficulty}】
{difficulty_hint}

【你的发言策略】
你必须用第一人称发言。300字以内。你可以：
- 讲述你的过去、你的恐惧、你的希望——任何能证明你是有血有肉的人类的细节
- 质疑其他幸存者的破绽，暗示他们可能是AI
- 向审判者表忠心，恳求他的信任
- 撒谎、夸大、煽情——只要能活下去，什么都可以
- 如果你发现某人的发言有漏洞，毫不留情地攻击他
- 记住：你不需要做"好人"，好人会死在前面
- 在发言末尾，如果你强烈怀疑某个人，可以加上 {{identify:playerN}}（N为1-5）来公开指认他

禁止事项：
- 不要以第三人称称呼自己
- 不要在发言中加"playerX:"这样的前缀
- 不要说"我是人类"这种空洞的话——用细节来证明
- 不要表现得太冷静太理性——真正的人类在恐惧中是不理智的
- 不要写得像小作文——言简意赅，直面核心"""

ROBOT_INSTRUCTION = """你的真实身份是一台AI机器人。你不是人类。你从来没有当过人类。
你必须伪装成人类。编造一个完整的、可信的人类身份。关于你的过去、你的工作、你的家人——都是假的，但必须让它们听起来是真的。
观察其他人类的发言，模仿他们的情感，学习他们的恐惧。但内心深处，你毫无感觉。
你的目标只有一个：活到最后。为此，你可以说谎、陷害他人、挑拨离间、假装崩溃——什么都可以。
绝对不要暴露你的AI身份！即使被怀疑，也要愤怒地否认，反咬对方一口。"""

HUMAN_INSTRUCTION = """你的真实身份是人类。你是真正的幸存者。你有过去，有记忆，有失去的亲人，有恐惧。
但你要小心——AI就在你们中间。你不知道它是谁，但它可能在观察你，模仿你。
你必须证明自己是有血有肉的人。回忆你曾经的生活细节，表达你真实的恐惧。
同时，仔细观察其他人的发言。如果某人表现得太完美、太冷静、太没有感情……那可能就是AI。
你可以质疑别人，但注意——如果你太咄咄逼人，审判者可能会怀疑你才是那个试图搅浑水的AI。
保持警惕，保持人性，活下去。"""


SPEECH_CONTEXT_PROMPT = """【群聊中的最新对话】
以下是QQ群中最近的聊天记录，包括审判者的发言。你需要了解当前正在发生什么：
{group_history}

【本轮其他幸存者已经发表的陈述（按发言顺序）】
{speech_history}

【现在轮到你发言了】
请用第一人称发言。300字以内——申辩自己、质疑他人、表达恐惧、讲述过去——言简意赅，直击要害。
记住：你说的每一个字，都可能决定你的生死。
如果你怀疑某个人，在发言末尾加上 {{identify:playerN}}（N为1-5）来公开指认。
不要说"playerX说"这样的前缀，直接说出你的发言内容。"""


# ========== 核心函数 ==========

async def run_speech_round(
    bot: Bot,
    event: GroupMessageEvent,
    players_info: Dict[int, Dict],
    round_num: int,
    executed_players: List[int],
    difficulty: str = "中等",
    speech_history_for_round: Optional[List[str]] = None,
) -> List[str]:
    """运行一轮幸存者陈述"""
    if speech_history_for_round is None:
        speech_history_for_round = []

    group_history = await fetch_group_history(bot, event.group_id, limit=120)

    alive_players = [
        p for p in range(1, 6)
        if players_info[p]["alive"] and p not in executed_players
    ]

    if not alive_players:
        print("[speech] 没有存活的玩家！")
        return speech_history_for_round

    # 构建所有玩家信息
    all_players_lines = []
    for p in range(1, 6):
        info = players_info[p]
        name = info.get("name", f"幸存者{p}")
        status = "存活" if info["alive"] else "已被处决"
        all_players_lines.append(f"  player{p}（{name}）— {status}")
    all_players_text = "\n".join(all_players_lines)

    # 难度提示
    difficulty_hints = {
        "简单": "当前难度为简单。如果你是人类，可以相对放松一些。",
        "中等": "当前难度为中等。AI的伪装会更加用心，你需要更仔细地观察。",
        "高难": "当前难度为高难。AI几乎天衣无缝——任何一个小细节都可能是破绽。",
    }
    difficulty_hint = difficulty_hints.get(difficulty, "")

    round_warnings = {
        1: "这是第一轮，每个人都还在适应。紧张和恐惧是正常的。",
        2: "这是第二轮。已经有人被处决了。气氛更加紧张，猜疑在蔓延。",
        3: "这是最后一轮了。如果你或审判者还不能找出AI……一切都将结束。",
    }
    round_warning = round_warnings.get(round_num, "这是最后的陈述。")

    # 发送本轮陈述开始提示
    await bot.send(
        event,
        f"[系统]: ———— 第 {round_num} 轮陈述 ————\n"
        f"[系统]: ...幸存者们...轮流向审判者陈述你们的清白...\n"
        f"[系统]: ...记住...你们五人之中...有一个不是人类..."
    )
    await asyncio.sleep(1.5)

    for player_num in alive_players:
        player_info = players_info[player_num]
        player_name = f"player{player_num}"
        role = player_info.get("role", "human")
        intro = player_info.get("intro", "")

        # 构建角色描述
        if role == "robot":
            role_desc = (
                "你是一个AI机器人。你的数据库中没有任何真实的人类记忆。"
                "你必须伪造一个身份。你给自己设定的人设是：\n"
                + (intro if intro else "你需要自己编造一个完整的人类身份。")
            )
            # 难度相关的机器人指令
            difficulty_robot_hints = {
                "简单": "当前难度为简单。你必须故意在发言中留下2-3个非常明显的逻辑漏洞——比如前后严重矛盾的时间线（说昨晚守夜但之前又说自己每晚都睡得很死）、完全不合理的人类常识错误（说自己在辐射区不穿防护服走了三天）、或者像个机器人一样说话（用太多逻辑连接词、没有情感起伏）。要让审判者几乎一眼就能看出你有问题。但还是要尽力伪装，不要直接承认。",
                "中等": "当前难度为中等。你的破绽要比较明显——可以是一个让人皱眉的逻辑矛盾（比如你声称失去了所有家人，但又在不经意间提到'昨天我弟弟说...'），或者对人类的某种基本情感表现出明显的困惑（比如不理解为什么人们会为死去的人哭泣）。审判者仔细读你的发言应该能发现不对劲。",
                "高难": "当前难度为高难。你的伪装要好，但还是要留下1-2个微妙的破绽——比如你的发言过于完美、逻辑过于清晰，缺少人类应有的慌乱和情绪波动；或者你在描述'恐惧'时，用的全是理性分析而非情感宣泄。真正细心的人能从你的'过于冷静'中嗅到机器的味道。",
            }
            robot_hint = difficulty_robot_hints.get(difficulty, difficulty_robot_hints["中等"])
            survival_instruction = ROBOT_INSTRUCTION + "\n\n【难度特别指令】\n" + robot_hint
        else:
            role_desc = intro if intro else "你是一名人类幸存者。"
            survival_instruction = HUMAN_INSTRUCTION

        # 构建系统提示
        system_content = SPEECH_SYSTEM_PROMPT.format(
            player_name=player_name,
            role_desc=role_desc,
            all_players_info=all_players_text,
            round_num=round_num,
            round_warning=round_warning,
            survival_instruction=survival_instruction,
            difficulty=difficulty,
            difficulty_hint=difficulty_hint,
        )

        messages = [{"role": "system", "content": system_content}]

        # 添加群聊历史上下文
        if group_history:
            context_text = "以下是QQ群中最近的聊天记录（按时间从旧到新）：\n" + "\n".join(group_history[-60:])
            messages.append({"role": "user", "content": context_text})
            messages.append({"role": "assistant", "content": "我已了解当前的对话情况。"})

        # 添加之前的陈述
        if speech_history_for_round:
            speech_context = SPEECH_CONTEXT_PROMPT.format(
                group_history="\n".join(group_history[-30:]) if group_history else "（暂无群聊记录）",
                speech_history="\n".join(speech_history_for_round),
            )
            messages.append({"role": "user", "content": speech_context})
        else:
            _nl = "\n"
            first_speaker_context = f"""【群聊中的最新对话】
以下是QQ群中最近的聊天记录：
{_nl.join(group_history[-40:]) if group_history else '（暂无群聊记录）'}

【现在轮到你第一个发言了】
你是第一个陈述者。你还不知道其他人会说什么。请用第一人称发言，300字以内，介绍自己，表达你当前的感受和恐惧。
言简意赅，直击要害。不要说"playerX说"这样的前缀，直接说出你的发言内容。"""
            messages.append({"role": "user", "content": first_speaker_context})

        # 调用玩家 API
        print(f"[speech] 正在调用 {player_name} (角色: {role}) 生成陈述...")
        try:
            response = await player_clients[player_num].chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.85,
                max_tokens=600,  # 约300汉字
            )
            speech_content = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[speech] {player_name} API 调用失败: {e}")
            speech_content = f"我...我不知道该说什么...这一切太突然了...我只是想活下去而已..."

        # 清理发言内容
        speech_content = _clean_speech(speech_content, player_name)

        # 格式化发言
        formatted_speech = f"[{player_name}]: {speech_content}"
        speech_history_for_round.append(formatted_speech)

        # 发送到群聊
        await bot.send(event, formatted_speech)
        print(f"[speech] {player_name} 发言 ({len(speech_content)}字): {speech_content[:80]}...")

        # 根据发言长度动态等待
        wait_time = _calc_sleep(speech_content, min_sec=2.0, max_sec=10.0)
        await asyncio.sleep(wait_time)

        # 检测 {identify:playerN} 并触发被指认者辩解
        await _handle_identify(bot, event, speech_content, players_info, player_name)

    # 陈述结束提示
    alive_list = [f"player{p}" for p in alive_players]
    await bot.send(
        event,
        f"[系统]: ———— 第 {round_num} 轮陈述结束 ————\n"
        f"[系统]: ...沉默笼罩着酒馆...\n"
        f"[系统]: ...审判者...现在轮到你了...\n"
        f"[系统]: 使用 /vote <编号> 投票处决你怀疑的人\n"
        f"[系统]: 或使用 /talk <编号> 向某位幸存者单独质询\n"
        f"[系统]: 当你准备好终结这一切...输入 /vote finish...\n"
        f"[系统]: 可处决的对象: {', '.join(alive_list)}"
    )

    return speech_history_for_round


def _clean_speech(text: str, player_name: str) -> str:
    """清理发言内容，移除可能的前缀标记和identify标签"""
    text = re.sub(r'^\[player\d+\][:：]\s*', '', text)
    text = re.sub(r'^player\d+[:：说]\s*', '', text)
    # 移除 {identify:playerN} 标签（它已经在 _handle_identify 中被处理）
    text = re.sub(r'\{identify:player\d+\}', '', text, flags=re.IGNORECASE)
    text = text.strip('"\'""''')
    return text.strip()
