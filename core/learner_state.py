"""
学习者状态管理模块

管理用户对各知识点的掌握程度和学习历史
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol


# NOTE: 每个知识点只保留最近若干条历史。整份状态是一个 JSON blob，
# 每次评分都会整体重写，历史无上限时这个 blob 会无限膨胀。
MAX_HISTORY_ENTRIES = 200

logger = logging.getLogger(__name__)


@dataclass
class KnowledgePoint:
    """知识点数据类"""
    
    # 知识点名称
    name: str
    
    # A权重：用户实际的学习程度（0.0-1.0）
    actual_mastery: float = 0.0
    
    # B权重：用户期望的掌握程度（0.0-1.0）
    target_mastery: float = 0.8
    
    # 用户备注
    note: str = ""
    
    # 学习历史记录
    history: list = field(default_factory=list)
    
    # 创建时间
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    
    # 最后更新时间
    updated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    # NOTE: 教学计划相关字段 —— 支持按计划逐步教学
    # 每个元素为 {"name": str, "content": str, "verification": str}
    teaching_plan: list = field(default_factory=list)

    # 当前正在进行的步骤索引（从 0 开始）
    current_step: int = 0

    # 教学计划生成时间（用于判断是否需要重新生成）
    plan_generated_at: Optional[str] = None

    # NOTE: 双层评分 —— 每步测验分独立记录，支持重考覆盖
    # step_scores[i] 对应 teaching_plan[i] 的最新测验分 (0.0-1.0)
    step_scores: list = field(default_factory=list)

    # The following fields bind a quiz to the exact lesson step that produced it.
    # They are persisted with learner state so a page refresh cannot silently
    # switch the quiz to a different step.
    plan_version: str = ""
    last_teaching_content: str = ""
    last_taught_step_index: Optional[int] = None
    last_teaching_completed_at: Optional[str] = None
    active_question_id: Optional[str] = None
    active_question_text: str = ""
    active_question_step_index: Optional[int] = None
    active_question_plan_version: str = ""

    def clear_quiz_context(self) -> None:
        """Invalidate any quiz created for a previous lesson or plan."""
        self.active_question_id = None
        self.active_question_text = ""
        self.active_question_step_index = None
        self.active_question_plan_version = ""

    def record_completed_teaching(self, content: str) -> None:
        """Remember only a fully completed lesson as the quiz source."""
        self.last_teaching_content = content.strip()
        self.last_taught_step_index = self.current_step
        self.last_teaching_completed_at = datetime.now().isoformat()
        self.clear_quiz_context()
        self.updated_at = datetime.now().isoformat()

    def record_step_score(self, step_index: int, score: float) -> None:
        """
        记录某步骤的测验分数（重考时覆盖旧分）

        Args:
            step_index: 步骤索引
            score: AI 评分 (0.0-1.0)
        """
        # 自动扩展列表长度
        while len(self.step_scores) <= step_index:
            self.step_scores.append(0.0)
        self.step_scores[step_index] = score

    def calculate_weighted_mastery(self) -> float:
        """
        根据所有已完成步骤的分数，计算加权全局掌握度

        公式：weighted_avg × completion_factor × target_mastery
        - 权重递增：weights[i] = 1.0 + i * 0.5（后面步骤更重要）
        - 完成度系数 = completed_steps / total_steps

        Returns:
            加权后的全局掌握度 (0.0 - target_mastery)
        """
        if not self.step_scores or not self.teaching_plan:
            return self.actual_mastery

        completed = len(self.step_scores)
        total = len(self.teaching_plan)

        # NOTE: 步骤权重递增：[1.0, 1.5, 2.0, 2.5, ...]
        weights = [1.0 + i * 0.5 for i in range(completed)]
        weighted_sum = sum(s * w for s, w in zip(self.step_scores, weights))
        weight_total = sum(weights)

        weighted_avg = weighted_sum / weight_total if weight_total > 0 else 0.0

        # NOTE: 完成度系数——未完成全部步骤会压低掌握度
        completion_factor = completed / total

        return min(
            weighted_avg * completion_factor * self.target_mastery,
            self.target_mastery
        )

    def get_current_plan_step(self) -> Optional[dict]:
        """
        获取当前教学步骤

        Returns:
            当前步骤的 dict，如果所有步骤已完成则返回 None
        """
        if not self.teaching_plan or self.current_step >= len(self.teaching_plan):
            return None
        return self.teaching_plan[self.current_step]

    def advance_step(self) -> None:
        """推进到下一个教学步骤"""
        self.current_step += 1
        self.clear_quiz_context()
        self.updated_at = datetime.now().isoformat()

    def is_plan_completed(self) -> bool:
        """判断是否已完成教学计划的所有步骤"""
        if not self.teaching_plan:
            return True
        return self.current_step >= len(self.teaching_plan)

    def update_mastery(self, new_mastery: float, score: float, feedback: str) -> None:
        """
        更新掌握度并记录历史
        
        Args:
            new_mastery: 新的掌握度值
            score: 本次评分
            feedback: 反馈内容
        """
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "old_mastery": self.actual_mastery,
            "new_mastery": new_mastery,
            "score": score,
            "feedback": feedback
        })
        if len(self.history) > MAX_HISTORY_ENTRIES:
            # 丢掉最旧的记录，只保留最近的窗口
            del self.history[:-MAX_HISTORY_ENTRIES]
        self.actual_mastery = new_mastery
        self.updated_at = datetime.now().isoformat()
    
    def is_mastered(self) -> bool:
        """检查是否已达到期望掌握度"""
        return self.actual_mastery >= self.target_mastery
    
    def get_teaching_stage(self) -> int:
        """
        根据当前掌握度获取教学阶段（5 档）

        Returns:
            0: 启蒙阶段 (0.0 <= A < 0.2)
            1: 基础阶段 (0.2 <= A < 0.45)
            2: 进阶阶段 (0.45 <= A < 0.7)
            3: 熟练阶段 (0.7 <= A < 0.9)
            4: 专家阶段 (0.9 <= A <= 1.0)
        """
        a = self.actual_mastery
        if a < 0.2:
            return 0
        elif a < 0.45:
            return 1
        elif a < 0.7:
            return 2
        elif a < 0.9:
            return 3
        else:
            return 4
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)
    
    def get_last_practice_time(self) -> Optional[datetime]:
        """
        获取上次练习时间
        
        用于计算时间遗忘因子
        
        Returns:
            上次练习的datetime对象，无练习记录返回None
        """
        if not self.history:
            return None
        
        last_entry = self.history[-1]
        try:
            return datetime.fromisoformat(last_entry["timestamp"])
        except (KeyError, ValueError):
            return None
    
    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgePoint":
        """从字典创建实例"""
        return cls(**data)


class LearnerStateStore(Protocol):
    """状态后端契约：只需要读出与写入整份状态字典。

    有了它，``LearnerState`` 就不再绑死本地 JSON 文件；服务端换成按账号隔离的
    SQLite 后端时，那些散布在业务代码里的 ``_auto_save()`` 调用点无需改动。
    """

    def read(self) -> dict[str, Any]:
        """返回已保存的状态；从未保存过时返回空字典。"""

    def write(self, data: dict[str, Any]) -> None:
        """整体覆盖写入状态。"""


class JsonFileStateStore:
    """本地 JSON 文件后端，供 CLI 与离线脚本使用。

    写入走"临时文件 + 原子替换"，避免进程在写一半时退出留下坏文件。
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


class LearnerState:
    """
    学习者状态管理类

    管理用户的所有知识点学习状态，支持持久化存储。持久化后端可替换：
    传 ``state_file`` 使用本地 JSON 文件，传 ``store`` 则接入任意后端
    （服务端用按账号隔离的 SQLite）。两者都不传时状态只存在内存里。
    """

    def __init__(
        self,
        state_file: Optional[str] = None,
        *,
        store: Optional[LearnerStateStore] = None,
    ):
        """
        初始化学习者状态

        Args:
            state_file: 状态文件路径，用于持久化存储
            store: 自定义状态后端；给出时优先于 state_file
        """
        self.knowledge_points: dict[str, KnowledgePoint] = {}
        # 本版本解析不了的条目原样留在这里，保存时按原样写回。
        # 它们不参与任何计算，只是不能被丢掉 —— 见 load() 的说明。
        self._unparsed: dict[str, Any] = {}
        self.state_file = Path(state_file) if state_file else None

        if store is not None:
            self.store: Optional[LearnerStateStore] = store
        elif self.state_file is not None:
            self.store = JsonFileStateStore(self.state_file)
        else:
            self.store = None

        # 载入已有状态（后端为空时是一次无副作用的空读）
        if self.store is not None:
            self.load()
    
    def add_knowledge_point(
        self,
        name: str,
        target_mastery: float = 0.8,
        note: str = "",
        initial_mastery: float = 0.0
    ) -> KnowledgePoint:
        """
        添加或获取知识点
        
        Args:
            name: 知识点名称
            target_mastery: 期望掌握度（B权重）
            note: 用户备注
            initial_mastery: 初始掌握度（A权重）
            
        Returns:
            KnowledgePoint实例
        """
        # 本版本重新写入同名知识点时，它取代原样保留的那份旧数据。
        self._unparsed.pop(name, None)
        if name not in self.knowledge_points:
            self.knowledge_points[name] = KnowledgePoint(
                name=name,
                actual_mastery=initial_mastery,
                target_mastery=target_mastery,
                note=note
            )
        else:
            # 更新现有知识点的期望掌握度和备注
            kp = self.knowledge_points[name]
            kp.target_mastery = target_mastery
            if note:
                kp.note = note
        
        self._auto_save()
        return self.knowledge_points[name]
    
    def get_knowledge_point(self, name: str) -> Optional[KnowledgePoint]:
        """获取知识点"""
        return self.knowledge_points.get(name)
    
    def update_mastery(
        self,
        name: str,
        new_mastery: float,
        score: float,
        feedback: str
    ) -> bool:
        """
        更新知识点掌握度
        
        Args:
            name: 知识点名称
            new_mastery: 新的掌握度
            score: 本次评分
            feedback: 反馈内容
            
        Returns:
            是否更新成功
        """
        kp = self.knowledge_points.get(name)
        if kp is None:
            return False
        
        kp.update_mastery(new_mastery, score, feedback)
        self._auto_save()
        return True
    
    def list_knowledge_points(self) -> list[KnowledgePoint]:
        """列出所有知识点"""
        return list(self.knowledge_points.values())
    
    def get_progress_summary(self) -> dict:
        """
        获取学习进度摘要
        
        Returns:
            包含总数、已掌握数、平均掌握度的字典
        """
        total = len(self.knowledge_points)
        if total == 0:
            return {
                "total": 0,
                "mastered": 0,
                "average_mastery": 0.0
            }
        
        mastered = sum(
            1 for kp in self.knowledge_points.values()
            if kp.is_mastered()
        )
        avg_mastery = sum(
            kp.actual_mastery for kp in self.knowledge_points.values()
        ) / total
        
        return {
            "total": total,
            "mastered": mastered,
            "average_mastery": round(avg_mastery, 3)
        }
    
    def to_dict(self) -> dict[str, Any]:
        """整份状态的可序列化表示，含原样保留的未知条目"""
        data: dict[str, Any] = dict(self._unparsed)
        data.update(
            {name: kp.to_dict() for name, kp in self.knowledge_points.items()}
        )
        return data

    def save(self) -> None:
        """把状态写入配置的后端；未配置后端时静默跳过"""
        if self.store is None:
            return
        self.store.write(self.to_dict())

    def load(self) -> None:
        """从配置的后端读取状态；未配置后端时静默跳过。

        解析不了的条目（回滚到旧版本后遇到新版本写入的字段，或手工改坏的数据）
        既不能让整份状态加载失败，**也不能直接丢掉**：每次评分都会整体重写这份
        状态，丢掉就等于下一次无关操作把它从库里永久抹除。因此这里把它们原样
        收进 ``_unparsed``，保存时再原样写回。
        """
        if self.store is None:
            return
        data = self.store.read()
        loaded: dict[str, KnowledgePoint] = {}
        unparsed: dict[str, Any] = {}
        for name, kp_data in (data or {}).items():
            if not isinstance(kp_data, dict):
                unparsed[name] = kp_data
                continue
            try:
                loaded[name] = KnowledgePoint.from_dict(kp_data)
            except TypeError:
                logger.warning("保留但不解析的知识点状态: %s", name)
                unparsed[name] = kp_data
        self.knowledge_points = loaded
        self._unparsed = unparsed

    def _auto_save(self) -> None:
        """自动保存（如果配置了持久化后端）"""
        if self.store is not None:
            self.save()
