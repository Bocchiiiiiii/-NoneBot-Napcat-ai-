"""
质询系统插件
真人玩家在每轮发言完毕后，可以使用 /talk <玩家编号> 向指定幸存者提问。
幸存者会进行自证、猜忌、表达求生欲等回应。
回应中可包含 {identify:playerN} 指认他人，系统会自动触发被指认者辩护。
"""
import asyncio
import re
from typing import Dict, List, Optional, TYPE_CHECKING

from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from .main import GameState

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

# 质询命令
talk_cmd = on_command("talk", priority=5)

# 游戏状态引用
_game_state: Optional["GameState"] = None


def set_game_state(state: "GameState"):
    global _game_state
    _game_state = state


# ========== 辅助函数 ==========

def _calc_sleep(text: str, min_sec: float = 1.5, max_sec: float = 8.0) -> float:
    char_count = len(text)
    sleep_time = char_count / 4.5
    return max(min_sec, min(sleep_time, max_sec))


async def _handle_identify_response(bot: Bot, event: GroupMessageEvent, response_text: str, accuser_name: str) -> None:
    """检测 {identify:playerN} 标签，触发被指认者辩护"""
    match = re.search(r'\{identify:player(\d+)\}', response_text, re.IGNORECASE)
    if not match:
        return
    target_num = int(match.group(1))
    if target_num < 1 or target_num > 5:
        return
    if not _game_state or not _game_state.players.get(target_num, {}).get("alive", False):
        return

    target_info = _game_state.players[target_num]
    target_name = target_info.get("name", f"player{target_num}")
    target_role = target_info.get("role", "human")
    target_intro = target_info.get("intro", "")

    await bot.send(
        event,
        f"[系统]: ...{accuser_name} 指认了 player{target_num}（{target_name}）！\n"
        f"[系统]: ...player{target_num}...请为自己辩护！..."
    )
    await asyncio.sleep(1.0)

    if target_role == "robot":
        role_desc = "你是一台AI机器人，伪装成人类。你被公开指认了！"
        defense_instruction = "你必须愤怒地反驳，反咬对方。撒谎、装委屈——只要能活下去。"
    else:
        role_desc = target_intro if target_intro else "你是一名真正的人类幸存者。"
        defense_instruction = "你是无辜的！用情感和细节证明你的人性。质疑指认者的动机。"

    defense_prompt = f"""【紧急！你被公开指认！】
{accuser_name} 指认你是AI机器人！

【你的身份】
你是：player{target_num}（{target_name}）
{role_desc}

【辩护指令】
{defense_instruction}
请立即用第一人称为自己辩护。300字以内。充满愤怒、委屈、恐惧。言简意赅。
在末尾可以加上 {{identify:playerN}} 来指认你怀疑的人。"""

    try:
        response = await player_clients[target_num].chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": defense_prompt},
                {"role": "user", "content": f"{accuser_name}指认你是AI！立即辩护！"},
            ],
            temperature=0.9,
            max_tokens=600,
        )
        defense = response.choices[0].message.content.strip()
        defense = re.sub(r'^\[player\d+\][:：]\s*', '', defense)
        defense = re.sub(r'^player\d+[:：说]\s*', '', defense)
        defense = defense.strip('"\'""''')
        defense = re.sub(r'\{identify:player\d+\}', '', defense, flags=re.IGNORECASE)
    except Exception as e:
        print(f"[talk] player{target_num} 辩护失败: {e}")
        defense = "你凭什么？！你有什么证据？！我看你才是那个机器！"

    await bot.send(event, f"[player{target_num}的辩护]: {defense}")
    await asyncio.sleep(_calc_sleep(defense, 1.5, 8.0))

    # 递归检测辩护中的指认
    await _handle_identify_response(bot, event, defense, f"player{target_num}")


# ========== 质询提示词 ==========

TALK_SYSTEM_PROMPT = """【你的处境】
现在是2077年。你和其他四个幸存者躲在一间废弃的地下酒馆里。外面是AI的巡逻机器和辐射风暴。
你们之中混入了一台AI机器人——它伪装成了人类。
审判者正在QQ群里观察你们的每一次对话。

你刚刚结束了陈述。现在，审判者点名要质询你——单独问你问题。

【你的身份】
你是：{player_name}
{role_desc}

【在场的所有人（共5人）】
{all_players_info}

【本轮所有陈述回顾】
{speech_history}

【审判者的质询】
审判者刚刚在群里对你提出了以下问题：
"{question}"

【你的回应策略】
300字以内。直接回应审判者的问题。同时：
- 如果你是真正的人类：用记忆、情感和恐惧来证明你的人性。指出你怀疑的人。
- 如果你是AI机器人：装作被恐惧和委屈淹没的人类。撒谎、编造细节、反咬他人——只要能活下去。
- 你可以质疑审判者，攻击其他幸存者，恳求同情——道德在这里不存在。
- 在末尾，如果你怀疑某人，加上 {{identify:playerN}}（N为1-5）来公开指认他。
- 不要说"playerX说"这样的前缀。言简意赅，不要长篇大论。"""


# ========== 命令处理器 ==========

@talk_cmd.handle()
async def handle_talk(bot: Bot, event: GroupMessageEvent):
    """处理质询指令"""
    if _game_state is None:
        await talk_cmd.finish("[系统]: ...尚未开始生存游戏...请使用 /lier_start 开始...")

    if not _game_state.active:
        await talk_cmd.finish("[系统]: ...当前并未进行任何生存游戏...")

    if _game_state.phase != "vote":
        await talk_cmd.finish("[系统]: ...现在不是质询的时候...等待陈述结束后再提问...")

    user_msg = event.get_plaintext().strip()
    args = re.sub(r'^/talk\s*', '', user_msg).strip()

    if not args:
        alive = [p for p in range(1, 6) if _game_state.players[p]["alive"]]
        await talk_cmd.finish(
            f"[系统]: 用法：/talk <编号> <你的问题>\n"
            f"[系统]: 例如：/talk 3 你刚才为什么回避了关于家人问题的回答？\n"
            f"[系统]: 可质询的对象: {', '.join(f'player{p}' for p in alive)}"
        )

    match = re.match(r'(\d+)\s+(.+)', args, re.DOTALL)
    if not match:
        num_match = re.match(r'(\d+)', args)
        if num_match:
            await talk_cmd.finish("[系统]: ...请在你的问题前面加上你要质询的幸存者编号...例如 /talk 3 你昨晚去了哪里？")
        else:
            await talk_cmd.finish("[系统]: ...请指定你要质询的幸存者编号和问题...例如 /talk 3 你为什么不敢看我的眼睛？")

    target_num = int(match.group(1))
    question = match.group(2).strip()

    if target_num < 1 or target_num > 5:
        await talk_cmd.finish("[系统]: ...编号无效...只有1到5号幸存者...")

    if not _game_state.players[target_num]["alive"]:
        await talk_cmd.finish(f"[系统]: ...player{target_num} 已经死了...死人不会说话...")

    if not question:
        await talk_cmd.finish("[系统]: ...你需要提出具体的问题...")

    target_name = _game_state.players[target_num].get("name", f"player{target_num}")
    await bot.send(
        event,
        f"[系统]: ...审判者向 player{target_num}（{target_name}）发起了质询...\n"
        f"[系统]: ...'{question}'...\n"
        f"[系统]: ...等待回应..."
    )
    await asyncio.sleep(1.0)

    response_text = await _get_talk_response(target_num, question)
    # 清理identify标签显示
    display_text = re.sub(r'\{identify:player\d+\}', '', response_text, flags=re.IGNORECASE).strip()
    await bot.send(event, f"[player{target_num}的回应]: {display_text}")
    await asyncio.sleep(_calc_sleep(display_text, 1.5, 8.0))

    # 检测并处理指认
    await _handle_identify_response(bot, event, response_text, f"player{target_num}")


async def _get_talk_response(player_num: int, question: str) -> str:
    """获取玩家的质询回应"""
    player_info = _game_state.players[player_num]
    player_name = f"player{player_num}"
    role = player_info.get("role", "human")
    intro = player_info.get("intro", "")

    if role == "robot":
        role_desc = (
            "你是一台AI机器人，伪装成人类。你的伪装身份信息是：\n"
            + (intro if intro else "你需要自己编造一个完整的人类身份。")
            + "\n记住：你绝对不能暴露你的AI身份。"
        )
    else:
        role_desc = intro if intro else "你是一名真正的人类幸存者。"

    all_players_lines = []
    for p in range(1, 6):
        info = _game_state.players[p]
        name = info.get("name", f"幸存者{p}")
        status = "存活" if info["alive"] else "已被处决"
        all_players_lines.append(f"  player{p}（{name}）— {status}")
    all_players_text = "\n".join(all_players_lines)

    speech_text = "\n".join(_game_state.speech_history) if _game_state.speech_history else "（暂无陈述记录）"

    system_content = TALK_SYSTEM_PROMPT.format(
        player_name=player_name,
        role_desc=role_desc,
        all_players_info=all_players_text,
        speech_history=speech_text,
        question=question,
    )

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"审判者问你：{question}\n\n直接回答。300字以内。可以指认他人：{{identify:playerN}}。言简意赅。"},
    ]

    try:
        response = await player_clients[player_num].chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.9,
            max_tokens=600,
        )
        reply = response.choices[0].message.content.strip()
        reply = re.sub(r'^\[player\d+\][:：]\s*', '', reply)
        reply = re.sub(r'^player\d+[:：说]\s*', '', reply)
        reply = reply.strip('"\'""''')
        return reply
    except Exception as e:
        print(f"[talk] 获取 player{player_num} 回应失败: {e}")
        if role == "robot":
            return "我...我不明白你在说什么...我是真正的人类！你这样质问我，是不是因为你自己心虚？！"
        else:
            return "你怀疑我？你有证据吗？我已经失去了所有亲人...现在你还要夺走我仅剩的生命？你凭什么？！"
