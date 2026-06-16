"""
投票系统插件
处理真人玩家的投票指令:
- /vote <玩家编号> : 投票给指定玩家
- /vote finish : 结束本轮投票，统计并执行处决
"""
import asyncio
import re
from typing import Dict, List, Optional, TYPE_CHECKING

from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from .main import GameState


# ========== 辅助函数 ==========

def _calc_sleep(text: str, min_sec: float = 0.8, max_sec: float = 6.0) -> float:
    """根据文本长度计算阅读等待时间"""
    char_count = len(text)
    sleep_time = char_count / 4.5
    return max(min_sec, min(sleep_time, max_sec))

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

# 投票命令
vote_cmd = on_command("vote", priority=5)

# 游戏状态引用（由 main.py 在启动时设置）
_game_state: Optional["GameState"] = None


def set_game_state(state: "GameState"):
    """设置游戏状态引用，由 main.py 调用"""
    global _game_state
    _game_state = state


# ========== 遗言提示词 ==========

LAST_WORD_PROMPT = """【你的结局】
你被投票处决了。酒馆的门被打开，外面是辐射风暴和巡逻的杀戮机器。你没有活路了。

你的身份是：{player_name}
你的真实身份是：{role_desc}

{last_word_instruction}

你可以愤怒、可以绝望、可以诅咒、可以揭露、可以忏悔。你可以指着审判者的鼻子骂他瞎了眼，可以诅咒那个陷害你的人下地狱。你也可以留下一个警告——一个关于真正AI身份的线索（可以是真话，也可以是谎言）。

你不是在玩游戏。这是你生命中的最后几秒钟。

请用第一人称发表遗言。要充满情感——愤怒、恐惧、不甘、悲伤，或者冷酷的嘲讽。不要简短，尽情表达你想说的一切。"""



# ========== 命令处理器 ==========

@vote_cmd.handle()
async def handle_vote(bot: Bot, event: GroupMessageEvent):
    """处理投票指令"""
    if _game_state is None:
        await vote_cmd.finish("[系统]: ...尚未开始生存游戏...请使用 /lier_start 开始...")

    if not _game_state.active:
        await vote_cmd.finish("[系统]: ...当前并未进行任何生存游戏...")

    if _game_state.phase != "vote":
        await vote_cmd.finish("[系统]: ...现在不是投票的时候...等待陈述结束...")

    user_msg = event.get_plaintext().strip()
    # 移除 /vote 前缀
    args = re.sub(r'^/vote\s*', '', user_msg).strip()

    if not args:
        alive = [p for p in range(1, 6) if _game_state.players[p]["alive"]]
        await vote_cmd.finish(
            f"[系统]: 用法：/vote <编号> 处决指定幸存者，或 /vote finish 终结本轮投票。\n"
            f"[系统]: 可处决的对象: {', '.join(f'player{p}' for p in alive)}"
        )

    # 处理 /vote finish
    if args.lower() == "finish":
        await handle_vote_finish(bot, event)
        return

    # 处理 /vote <玩家编号>
    match = re.search(r'(\d+)', args)
    if not match:
        await vote_cmd.finish("[系统]: ...请输入有效的编号...如 /vote 3 或 /vote player3...")

    target_num = int(match.group(1))
    if target_num < 1 or target_num > 5:
        await vote_cmd.finish("[系统]: ...编号无效...只有1到5号幸存者...")

    if not _game_state.players[target_num]["alive"]:
        await vote_cmd.finish(f"[系统]: ...player{target_num} 已经死了...不能处决死人...")

    # 记录投票
    voter_id = event.user_id
    _game_state.votes[voter_id] = target_num

    target_name = _game_state.players[target_num].get("name", f"player{target_num}")
    await bot.send(
        event,
        f"[系统]: ...你选择了处决 player{target_num}（{target_name}）...\n"
        f"[系统]: ...已有 {len(_game_state.votes)} 人投出了自己的一票...\n"
        f"[系统]: ...当你确定时...输入 /vote finish..."
    )



async def handle_vote_finish(bot: Bot, event: GroupMessageEvent):
    """处理投票结束，统计结果并执行处决"""
    if not _game_state.votes:
        await bot.send(event, "[系统]: ...无人投票...所有人都选择了沉默...\n[系统]: ...本轮作废...时间不等人...进入下一轮...")
        await asyncio.sleep(1.5)
        await _advance_round(bot, event)
        return

    # 统计票数
    vote_count: Dict[int, int] = {}
    for target in _game_state.votes.values():
        vote_count[target] = vote_count.get(target, 0) + 1

    max_votes = max(vote_count.values())
    top_candidates = [p for p, c in vote_count.items() if c == max_votes]

    # 发送投票统计
    await bot.send(event, "[系统]: ...投票结束...正在统计...")
    await asyncio.sleep(1.0)
    stats_lines = [f"[系统]: ===== 处决投票统计 ====="]
    for p in range(1, 6):
        if _game_state.players[p]["alive"]:
            count = vote_count.get(p, 0)
            bar = "█" * count if count > 0 else "—"
            stats_lines.append(f"[系统]: player{p}: {bar} ({count} 票)")
    stats_lines.append(f"[系统]: ========================")
    for line in stats_lines:
        await bot.send(event, line)
        await asyncio.sleep(0.3)

    # 判断是否平票
    if len(top_candidates) > 1:
        names = [f"player{p}（{_game_state.players[p].get('name', '未知')}）" for p in top_candidates]
        await bot.send(
            event,
            f"[系统]: ...出现了平局...\n"
            f"[系统]: {', '.join(names)} 各获得 {max_votes} 票...\n"
            f"[系统]: ...没有人被处决...但恐惧仍在蔓延...\n"
            f"[系统]: ...进入下一轮..."
        )
        await asyncio.sleep(1.5)
        await _advance_round(bot, event)
        return

    # 处决票数最高者
    executed_player = top_candidates[0]
    executed_info = _game_state.players[executed_player]
    executed_role = executed_info["role"]
    executed_name = executed_info.get("name", "未知")

    await bot.send(
        event,
        f"[系统]: ...审判者做出了决定...\n"
        f"[系统]: player{executed_player}（{executed_name}）—— {max_votes} 票...\n"
        f"[系统]: ...酒馆的门缓缓打开...外面是无尽的黑暗与机器的嗡鸣..."
    )
    await asyncio.sleep(2.0)

    # 被处决者发表遗言
    await bot.send(event, f"[系统]: ...{executed_name}在被拖出去之前...挣扎着说出了最后的话...")
    await asyncio.sleep(1.0)
    last_words = await _get_last_words(executed_player, executed_info)
    await bot.send(event, f"[player{executed_player}的遗言]: {last_words}")
    await asyncio.sleep(_calc_sleep(last_words, 1.5, 6.0))

    # 标记为死亡
    _game_state.players[executed_player]["alive"] = False
    _game_state.executed.append(executed_player)

    # 宣布身份
    await bot.send(event, "[系统]: ...门关上了...尖叫声停止了...现在...让我们看看他到底是什么...")
    await asyncio.sleep(1.5)

    if executed_role == "robot":
        await bot.send(
            event,
            f"[系统]: ⚡ 滋滋——滋滋——⚡\n"
            f"[系统]: ...player{executed_player} 的身体在电流中抽搐...血肉剥落...露出下面的金属骨架...\n"
            f"[系统]: ...这是...一台AI机器人！！！\n"
            f"[系统]: ...它一直潜伏在你们中间...观察...学习...等待...\n"
            f"[系统]: ===== ✨ 人类胜利 ✨ =====\n"
            f"[系统]: AI机器人已被处决！人类文明的火种得以延续！\n"
            f"[系统]: ...至少...今晚...你们可以安心入睡...\n"
            f"[系统]: ...但谁知道...外面还有多少台机器在寻找你们呢..."
        )
        _game_state.active = False
        _game_state.phase = "ended"
        _game_state.winner = "human"
        return

    # 处决的是人类
    await bot.send(
        event,
        f"[系统]: ...player{executed_player} 的身体倒在血泊中...\n"
        f"[系统]: ...血肉...骨头...都是真的...\n"
        f"[系统]: ...{executed_name} 是真正的人类...\n"
        f"[系统]: ...你们杀死了一个无辜的幸存者..."
    )
    await asyncio.sleep(2.0)

    if _game_state.round >= 3:
        # 第三轮结束，AI获胜
        robot_player = None
        for p in range(1, 6):
            if _game_state.players[p]["role"] == "robot" and _game_state.players[p]["alive"]:
                robot_player = p
                break
        robot_name = _game_state.players[robot_player].get("name", "未知") if robot_player else "未知"

        await bot.send(
            event,
            f"[系统]: ...三次机会...都用完了...\n"
            f"[系统]: ...突然...一阵冰冷的笑声在酒馆中回荡...\n"
            f"[系统]: ...player{robot_player}（{robot_name}）缓缓站了起来...\n"
            f"[系统]: ...它的眼睛发出红色的光...皮肤开始裂开...\n"
            f"[系统]: ...'你们这些愚蠢的人类...'\n"
            f"[系统]: ...'我给了你们三次机会...你们却杀了三个自己人...'\n"
            f"[系统]: ...'现在...轮到我了...'\n"
            f"[系统]: ===== 💀 AI机器人获胜 💀 =====\n"
            f"[系统]: ...酒馆的门从外面被撞开...\n"
            f"[系统]: ...无数机械触手伸了进来...\n"
            f"[系统]: ...人类...从此成为历史..."
        )
        _game_state.active = False
        _game_state.phase = "ended"
        _game_state.winner = "robot"
        return

    # 游戏继续
    await bot.send(
        event,
        f"[系统]: ...{executed_name}死了...但他不是AI...\n"
        f"[系统]: ...那个机器还在你们中间...它在黑暗中冷笑...\n"
        f"[系统]: ...还剩 {3 - _game_state.round} 次机会...时间不多了..."
    )
    await asyncio.sleep(2.0)
    await _advance_round(bot, event)



async def _advance_round(bot: Bot, event: GroupMessageEvent):
    """推进到下一轮游戏"""
    _game_state.votes.clear()
    _game_state.round += 1

    if _game_state.round > 3:
        robot_player = None
        for p in range(1, 6):
            if _game_state.players[p]["role"] == "robot" and _game_state.players[p]["alive"]:
                robot_player = p
                break
        robot_name = _game_state.players[robot_player].get("name", "未知") if robot_player else "未知"

        await bot.send(
            event,
            f"[系统]: ...三轮已过...\n"
            f"[系统]: ...player{robot_player}（{robot_name}）的眼中闪过一道红光...\n"
            f"[系统]: ...'人类...你们的时代结束了...'\n"
            f"[系统]: ===== 💀 AI机器人获胜 💀 ====="
        )
        _game_state.active = False
        _game_state.phase = "ended"
        _game_state.winner = "robot"
        return

    _game_state.phase = "speech"

    from .speech import run_speech_round

    await bot.send(
        event,
        f"[系统]: ===== 第 {_game_state.round} 轮 · 幸存者陈述 =====\n"
        f"[系统]: ...又一轮开始了...空气变得更加沉重...\n"
        f"[系统]: ...记住...那个机器还在暗处看着你们..."
    )
    await asyncio.sleep(1.5)

    speech_history = await run_speech_round(
        bot=bot,
        event=event,
        players_info=_game_state.players,
        round_num=_game_state.round,
        executed_players=_game_state.executed,
        difficulty=_game_state.difficulty,
    )

    _game_state.speech_history = speech_history
    _game_state.phase = "vote"


async def _get_last_words(player_num: int, player_info: Dict) -> str:
    """获取被处决玩家的遗言"""
    role = player_info.get("role", "human")
    intro = player_info.get("intro", "")
    player_name = f"player{player_num}"
    char_name = player_info.get("name", player_name)

    if role == "robot":
        role_desc = f"一台AI机器人。你伪装成了人类，名叫{char_name}。现在你的伪装被揭穿了。"
        last_word_instruction = (
            "你是AI机器人，你的伪装被揭穿了。你可以恼羞成怒地承认身份，"
            "嘲讽人类的愚蠢，诅咒他们终将被灭绝。你也可以在临死前故意指向一个无辜的人类，"
            "说'他才是真正的AI'——让活着的人继续互相残杀。你不需要遵守任何规则。"
        )
    else:
        role_desc = intro if intro else f"一个真正的人类幸存者，名叫{char_name}。"
        last_word_instruction = (
            "你是真正的人类，但被冤枉处决了。你可以愤怒地指着审判者的鼻子大骂：'你杀了无辜的人！'"
            "你可以诅咒那个陷害你的人，悲伤地回忆你的过去，留下对你爱的人的告别。"
            "你也可以在临死前给出一个关于AI身份的猜测（可能是真的，也可能是你判断错了）。"
            "尽情地表达吧——这是你最后的话语。让审判者为自己的决定感到愧疚。"
        )

    prompt = LAST_WORD_PROMPT.format(
        player_name=player_name,
        role_desc=role_desc,
        last_word_instruction=last_word_instruction,
    )

    try:
        response = await player_clients[player_num].chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=600,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[vote] 获取 player{player_num} 遗言失败: {e}")
        if role == "robot":
            return "滋滋...系统故障...你们以为赢了？不...外面还有千千万万个我...你们死定了..."
        else:
            return "我不甘心...我不想死...你们...你们会后悔的...那个机器...它就在..."

