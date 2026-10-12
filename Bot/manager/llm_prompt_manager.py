from copy import deepcopy
from threading import RLock
from typing import Callable, Optional

from condata.pipeline.llm import Conversation, RoleEnum

from character.config import ChatConfig


class LLMPromptManager:
    """对话历史持有者。

    历史会被两条线程改写：前台每轮追加消息，记忆压缩线程把它替换为摘要。
    所有"读-改-写"都必须在 `_lock` 内完成，否则后者会把前者的结果覆盖掉。
    锁只保护内存里的列表操作（微秒级），不包含模型调用与磁盘写入。
    """

    def __init__(self, config: ChatConfig, bot_name: str | None = None):
        self.bot_name: str | None = bot_name
        self._lock = RLock()
        self.system_prompt: str = self._render_template(config.system_prompt)
        injected_history = [self._render_template(content) for content in config.injected_history]
        self.injected_history: list[Conversation] = self._parse_history_list(injected_history,
                                                                             self.system_prompt)
        self.current_history: list[Conversation] = deepcopy(self.injected_history)
        self.max_history = config.max_history

    @property
    def injected_len(self) -> int:
        """注入块条数（1 条 system 提示 + injected_history），压缩不得越过这个边界。"""
        return len(self.injected_history) + 1

    def history_snapshot(self) -> list[Conversation]:
        """取当前历史的浅拷贝快照；调用方后续只读，不再直接改原件。"""
        with self._lock:
            return list(self.current_history)

    def commit_history_update(self, transform: Callable[[list[Conversation]], Optional[list[Conversation]]]) -> bool:
        """在锁内基于"当前"历史执行一次读-改-写。

        transform 收到的是提交时刻的真实历史（已拷贝），返回新列表表示提交，
        返回 None 表示放弃（例如压缩游标已失效）。返回是否提交成功。
        """
        with self._lock:
            updated = transform(list(self.current_history))
            if updated is None:
                return False
            self.update_history(updated)
            return True

    def render_bot_name(self, content: str) -> str:
        """把 `{bot_name}` 渲染成实际角色名。

        工具 acknowledgement 这类"模型之外要说出口的文本"也走这里，
        否则配置里写 `{bot_name}` 会被原样念出来。
        """
        return self._render_template(content)

    def _render_template(self, content: str) -> str:
        if self.bot_name is None:
            return content
        try:
            return content.format(bot_name=self.bot_name)
        except (KeyError, IndexError, ValueError):
            # 出现非 bot_name 的占位符或落单大括号时原样返回，别让配置文本打死流程
            return content
        
    def replace_system_prompt(self, new_prompt: str) -> None:
        """
        替换 injected_history 和 current_history 中的 system 角色消息。
        如果不存在 system 消息，则在最前面插入。
        用于注入长期记忆
        """
        with self._lock:
            # 更新 system_prompt 属性
            self.system_prompt = new_prompt

            # 替换 injected_history
            found = False
            for conv in self.injected_history:
                if conv.role == RoleEnum.system:
                    conv.content = new_prompt
                    found = True
                    break
            if not found:
                # 如果没有 system 消息（通常不会），插入到最前面
                self.injected_history.insert(0, Conversation(role=RoleEnum.system, content=new_prompt))

            # 替换 current_history（同样逻辑）
            found = False
            for conv in self.current_history:
                if conv.role == RoleEnum.system:
                    conv.content = new_prompt
                    found = True
                    break
            if not found:
                # 如果没有，插入到最前面
                self.current_history.insert(0, Conversation(role=RoleEnum.system, content=new_prompt))
        
    def update_history(self, new_history: list[Conversation]) -> None:
        """
        用新的历史列表更新 current_history，自动截断到 max_history。
        保证 injected_history 始终位于最前面。
        """
        if new_history is None:
            return
        with self._lock:
            # 确保 injected_history 的内容在最前面（防止顺序错乱）
            # 但通常 new_history 已经包含了 injected_history（因为 predict 时会传入 current_history，
            # 而 current_history 的开头就是 injected_history），所以只需直接截断。
            max_len = self.max_history
            if len(new_history) <= max_len:
                self.current_history = new_history
            else:
                # 保留 injected_history 的长度，截断后面的部分
                inject_len = len(self.injected_history)
                # 如果注入长度可能大于 max_len，则只保留 max_len 条（但一般不会）
                if inject_len >= max_len:
                    self.current_history = new_history[:max_len]
                else:
                    # 保持前 inject_len 条是注入历史，然后取最新的 (max_len - inject_len) 条
                    tail_start = max(inject_len, len(new_history) - (max_len - inject_len))
                    self.current_history = new_history[:inject_len] + new_history[tail_start:]

    def reset_history(self, history: list[Conversation]) -> None:
        """
        Resets `current_history` with deepcopy.
        If the length of `current_history` is greater than the `max_history`,
        resets it to `injected_history` from the config file.
        :param history: List of instances of class Conversation
        :return: None
        """
        if history is None:
            self.current_history = deepcopy(self.injected_history)
        else:
            if len(history) <= self.max_history:
                self.current_history = deepcopy(history)
            else:
                self.current_history = deepcopy(self.injected_history)

    @staticmethod
    def _parse_history_list(history: list[str], system_prompt: str | None = None) -> list[Conversation]:
        result = []

        if system_prompt is not None:
            result.append(Conversation(role=RoleEnum.system, content=system_prompt))

        for idx, content in enumerate(history):
            role = RoleEnum.user if idx % 2 == 0 else RoleEnum.assistant
            conversation = Conversation(role=role, content=content)
            result.append(conversation)

        return result
