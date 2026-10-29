"""Conservative local intents and a pre-session text hook.

The current gbot audio path has NO pre-session ASR. A future local ASR bridge
must await handle_before_session(text) BEFORE creating a session/turn, and
forward only on None. Never call this on cloud transcripts (too late to save).
Pass the connected EdgeTTSProvider.speak_text, configured zh-HK-HiuMaanNeural.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

LINES = json.loads((Path(__file__).parent / 'data/local_lines_placeholder.json').read_text())


def normalise(text):
    return re.sub(r'\s+', '', text.translate(str.maketrans('计时钟还么课游戏阵见几', '計時鐘還麼課遊戲陣見幾'))).strip('。，！？,.!?、；;')


@dataclass(frozen=True)
class Intent:
    name: str
    value: object = None


def match(text):
    if not isinstance(text, str):
        return None
    text = normalise(text)
    fixed = {'仲有幾耐': 'remaining', '停': 'stop', '停止計時': 'stop',
             '小休': 'break', '休息一陣': 'break', '玩遊戲': 'game',
             '仲有咩功課': 'homework', '你好': 'hello', '拜拜': 'bye', '再見': 'bye'}
    if text in fixed:
        return Intent(fixed[text])
    timer = re.fullmatch(r'計時([0-9]+|[一二三四五六七八九十]+)分鐘', text)
    if timer:
        token = timer[1]
        numbers = {'一二三四五六七八九'[i-1]: i for i in range(1, 10)}
        numbers['十'] = 10
        for n in range(11, 61):
            tens, units = divmod(n, 10)
            label = ('' if tens == 1 else '一二三四五六七八九'[tens-1]) + '十'
            if units:
                label += '一二三四五六七八九'[units-1]
            numbers[label] = n
        minutes = int(token) if token.isascii() and token.isdigit() else numbers.get(token, 0)
        if 1 <= minutes <= 60:
            return Intent('timer', minutes)
    done = re.fullmatch(r'做完(.+)', text)
    if done and not re.search(r'[，。！？,!?；;]', done[1]):
        return Intent('done', done[1])
    return None


class LocalCommands:
    def __init__(self, *, settings, forms, games, usage, speak, clock=time.monotonic):
        self.settings, self.forms, self.games = settings, forms, games
        self.usage, self.speak, self.clock = usage, speak, clock
        self.deadline = None

    async def handle_before_session(self, text):
        if not self.settings.get()['local_commands_enabled']:
            return None
        intent = match(text)
        if intent is None:
            return None
        name, values, messages = intent.name, {}, []
        items = [i for i in self.forms.snapshot()['items'] if not i['done']]
        if name == 'done':
            matches = [i for i in items if normalise(i['title']) == intent.value]
            if len(matches) != 1:
                return None
            item = matches[0]
            self.forms.mark_done(item['id'], source='child-local')
            values['item'] = item['title']
        elif name == 'timer':
            self.deadline = self.clock() + intent.value * 60
            name, values = 'timer_start', {'minutes': intent.value}
        elif name == 'remaining':
            name = 'timer_remaining' if self.deadline is not None else 'timer_none'
            values['seconds'] = max(0, int((self.deadline or self.clock()) - self.clock()))
        elif name == 'stop':
            self.deadline, name = None, 'timer_stop'
        elif name == 'break':
            # Reuse BOOT double-press game exit/decline; no invented board command.
            messages = self.games.on_double_press().get('messages', [])
        elif name == 'game':
            result = self.games.start_pending()  # sole existing unlock/expiry check
            messages = result.get('messages', [])
            if not result['ok']:
                name = 'locked' if items else 'locked_empty'
        elif name == 'homework':
            name = 'homework' if items else 'homework_empty'
        values['items'] = '、'.join(i['title'] for i in items)
        self.usage.local_handled()
        for message in messages:
            await self.forms.send_device(message)
        spoken = LINES[name].format(**values)
        await self.speak(spoken)
        return {'intent': intent.name, 'handled': True}
