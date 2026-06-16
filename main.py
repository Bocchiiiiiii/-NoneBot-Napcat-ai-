"""
游戏进程主程序 — 骗子酒馆 (Liar's Tavern)
规则：
- /lier_start : 开启游戏
- 系统自动初始化角色 → 发言 → 投票循环
- 共 3 轮，处决 AI 机器人则真人获胜，否则 AI 获胜
- 系统消息格式: [系统]:......
"""
import asyncio
from typing import Dict, List, Optional

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

from .role_initialize import initialize_roles
from .speech import run_speech_round
from . import vote as vote_module


# ========== 辅助函数 ==========

def _calc_sleep(text: str, min_sec: float = 0.8, max_sec: float = 5.0) -> float:
    """根据文本长度计算阅读等待时间（中文约每分钟300字）"""
    char_count = len(text)
    sleep_time = char_count / 5.0  # ~5 chars per second
    return max(min_sec, min(sleep_time, max_sec))


# ========== 游戏状态 ==========

class GameState:
    """共享游戏状态"""
    def __init__(self):
        self.active: bool = False
        self.round: int = 1
        self.phase: str = "idle"  # idle, init, speech, talk, vote, ended
        self.players: Dict[int, Dict] = {}  # {player_num: {role, intro, name, alive}}
        self.votes: Dict[int, int] = {}  # {voter_user_id: target_player_num}
        self.executed: List[int] = []  # 已被处决的玩家编号
        self.group_id: Optional[int] = None
        self.winner: Optional[str] = None  # "human" or "robot"
        self.speech_history: List[str] = []  # 当前轮的发言历史
        self.difficulty: str = "中等"  # 简单 / 中等 / 高难
        self.waiting_difficulty: bool = False  # 是否在等待难度选择

    def reset(self):
        self.active = False
        self.round = 1
        self.phase = "idle"
        self.players = {}
        self.votes = {}
        self.executed = []
        self.group_id = None
        self.winner = None
        self.speech_history = []
        self.difficulty = "中等"
        self.waiting_difficulty = False


# 全局游戏状态实例
game_state = GameState()

# 将游戏状态注入到 vote 模块
vote_module.set_game_state(game_state)

# ========== 命令定义 ==========

lier_start = on_command("lier_start", priority=10)


# ========== 命令处理器 ==========

@lier_start.handle()
async def handle_lier_start(bot: Bot, event: GroupMessageEvent):
    """开始游戏 — 第一阶段：选难度"""
    from . import talk as talk_module
    talk_module.set_game_state(game_state)

    if game_state.active:
        await lier_start.finish("[系统]: ...游戏已经在运行中了...请等待当前...清算结束...")

    # 重置游戏状态
    game_state.reset()
    game_state.active = True
    game_state.phase = "init"
    game_state.group_id = event.group_id
    game_state.waiting_difficulty = True

    # === 开场白（分段发送，制造悬疑感）===

    msgs = [
        "[系统]: ⚠️ 注意... ⚠️",
        "[系统]: 2077年...智械危机爆发...AI已经全面入侵了人类文明...",
        "[系统]: 曾经繁荣的城市...如今只剩下废墟与沉默...",
        "[系统]: 仅存的五名幸存者聚集在了一起...他们躲藏在这间破败的地下酒馆中...",
        "[系统]: 但是...他们中间...混入了一个不该存在的东西...",
        "[系统]: 一台AI机器人...它披着人类的皮囊...潜伏在众人之间...",
        "[系统]: 它不会流血...不会恐惧...它唯一的目标...就是活下去...把你们所有人都拖入深渊...",
        "[系统]: 你们只有三次机会...三次投票...来找出它...",
        "[系统]: 如果三次之后...它仍然活着...那么...人类将彻底沦为历史...",
        "[系统]: 现在...在开始之前...你需要做出一个选择...",
    ]
    for msg in msgs:
        await bot.send(event, msg)
        await asyncio.sleep(_calc_sleep(msg, 0.6, 3.0))

    await asyncio.sleep(1.0)
    await bot.send(
        event,
        "[系统]: 选择难度...\n"
        "[系统]: 回复「简单」—— AI机器人会露出明显的破绽...\n"
        "[系统]: 回复「中等」—— AI机器人会更小心地隐藏自己...\n"
        "[系统]: 回复「高难」—— AI机器人几乎天衣无缝...你能找到它吗？"
    )


# ========== 难度选择监听 ==========

from nonebot import on_message
from nonebot.rule import Rule

async def _difficulty_rule(event: GroupMessageEvent) -> bool:
    """只有等待难度选择时，且消息为简单/中等/高难才触发"""
    if not game_state.waiting_difficulty:
        return False
    if event.group_id != game_state.group_id:
        return False
    text = event.get_plaintext().strip()
    return text in ("简单", "中等", "高难")

difficulty_listener = on_message(rule=Rule(_difficulty_rule), priority=1, block=True)


@difficulty_listener.handle()
async def handle_difficulty(bot: Bot, event: GroupMessageEvent):
    """处理难度选择并继续游戏"""
    text = event.get_plaintext().strip()
    game_state.difficulty = text
    game_state.waiting_difficulty = False

    await bot.send(event, f"[系统]: ...难度已设定为「{text}」...那么...让我们开始吧...")
    await asyncio.sleep(1.5)

    # 继续游戏流程
    await _start_game_flow(bot, event)


async def _start_game_flow(bot: Bot, event: GroupMessageEvent):
    """难度选定后，继续游戏流程"""
    # Step 1: 初始化角色
    await bot.send(event, "[系统]: 正在扫描幸存者信息......")
    await asyncio.sleep(1.5)
    try:
        players_info = await initialize_roles()
    except Exception as e:
        await bot.send(event, f"[系统]: ...初始化失败...系统遭受干扰...{e}")
        game_state.reset()
        return
    game_state.players = players_info
    await bot.send(event, "[系统]: ...扫描完成...五名幸存者身份已确认...")
    await asyncio.sleep(2.0)

    # Step 2: 第一轮发言
    game_state.phase = "speech"
    try:
        speech_history = await run_speech_round(
            bot=bot,
            event=event,
            players_info=game_state.players,
            round_num=game_state.round,
            executed_players=game_state.executed,
            difficulty=game_state.difficulty,
        )
        game_state.speech_history = speech_history
    except Exception as e:
        await bot.send(event, f"[系统]: ...通讯中断...发言阶段出现异常...{e}")
        game_state.reset()
        return

    # Step 3: 进入投票 + 提问阶段
    game_state.phase = "vote"
    await bot.send(
        event,
        "[系统]: 发言完毕...现在...做出你的选择...\n"
        "[系统]: 输入 /vote <编号> 投票处决你怀疑的人...\n"
        "[系统]: 或者输入 /talk <编号> 向某位幸存者质询...\n"
        "[系统]: 当你准备好终结这一切时...输入 /vote finish..."
    )


# ========== 辅助命令 ==========

lier_end = on_command("lier_end", priority=10)


@lier_end.handle()
async def handle_lier_end(bot: Bot, event: GroupMessageEvent):
    """强制结束游戏"""
    if not game_state.active:
        await lier_end.finish("[系统]: ...当前并未进行任何生存游戏...")

    game_state.reset()
    await bot.send(event, "[系统]: ...游戏已被强行终止...一切归于虚无...")


lier_status = on_command("lier_status", priority=10)


@lier_status.handle()
async def handle_lier_status(bot: Bot, event: GroupMessageEvent):
    """查看游戏状态"""
    if not game_state.active:
        await lier_status.finish("[系统]: ...当前并未进行任何生存游戏...")

    alive = [p for p in range(1, 6) if game_state.players[p]["alive"]]
    executed = game_state.executed
    status_lines = [
        f"[系统]: ===== 当前状态 =====",
        f"[系统]: 难度: {game_state.difficulty}",
        f"[系统]: 轮次: 第 {game_state.round} 轮 / 共 3 轮",
        f"[系统]: 阶段: {_phase_name(game_state.phase)}",
        f"[系统]: 存活者: {', '.join(f'player{p}' for p in alive) if alive else '...无人存活...'}",
        f"[系统]: 已处决: {', '.join(f'player{p}' for p in executed) if executed else '...尚未处决任何人...'}",
        f"[系统]: 已投票: {len(game_state.votes)} 人",
    ]
    for line in status_lines:
        await bot.send(event, line)
        await asyncio.sleep(0.4)


def _phase_name(phase: str) -> str:
    """返回阶段的中文名称"""
    mapping = {
        "idle": "待命",
        "init": "扫描中",
        "speech": "幸存者陈述",
        "talk": "质询阶段",
        "vote": "处决投票",
        "ended": "已终结",
    }
    return mapping.get(phase, phase)

