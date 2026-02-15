"""
Memory System Types

定义记忆系统的核心数据类型：
- Observation: 原子级别的观察记录（每个工具调用生成一个）
- SessionSummary: 会话级别的摘要（每个 Prompt 生成一个）

参考 claude-mem 设计，添加 project 和 discovery_tokens 字段
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional


class ObservationType(Enum):
    """观察类型 - 基于工具调用的分类"""
    BUGFIX = "bugfix"           # 修复了某个 bug
    FEATURE = "feature"         # 实现了新功能
    REFACTOR = "refactor"       # 代码重构
    CHANGE = "change"           # 一般性修改
    DISCOVERY = "discovery"     # 发现/调研
    DECISION = "decision"       # 做出决策


@dataclass
class ObservationInput:
    """
    创建 Observation 的输入
    
    由 KimiSoul._grow_context() 在工具调用后生成
    参考 claude-mem 设计，添加 project 和 discovery_tokens 字段
    """
    session_id: str
    project: str                        # 🔴 工作目录/项目路径（核心过滤字段，参考 claude-mem）
    type: ObservationType
    title: str                          # 简短标题（如 "Fix auth token validation"）
    subtitle: str = ""                  # 可选副标题
    facts: list[str] = field(default_factory=list)      # 关键事实列表
    narrative: str = ""                 # 详细描述（用于向量搜索内容生成，参考 claude-mem）
    concepts: list[str] = field(default_factory=list)   # 概念标签（如 ["auth", "jwt", "token"]）
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tool_name: Optional[str] = None     # 调用的工具名
    prompt_number: int = 0              # 当前 Prompt 序号
    discovery_tokens: int = 0           # 🔴 发现成本（ROI 追踪，参考 claude-mem）
    created_at: datetime = field(default_factory=datetime.now)


@dataclass  
class Observation:
    """
    完整的 Observation 记录（包含数据库生成的 ID）
    参考 claude-mem 设计
    """
    id: int
    session_id: str
    project: str                        # 🔴 工作目录/项目路径
    type: ObservationType
    title: str
    subtitle: str
    facts: list[str]
    narrative: str                      # 详细描述（用于向量搜索）
    concepts: list[str]
    files_read: list[str]
    files_modified: list[str]
    tool_name: Optional[str]
    prompt_number: int
    discovery_tokens: int               # 🔴 发现成本
    created_at: datetime
    embedding: Optional[bytes] = None   # 向量嵌入（二进制 blob）


@dataclass
class SummaryInput:
    """
    创建 SessionSummary 的输入
    
    由 KimiSoul.run() 在每个 Prompt 结束时生成
    参考 claude-mem 设计，添加 project 和 discovery_tokens 字段
    """
    session_id: str
    project: str                        # 🔴 工作目录/项目路径（核心过滤字段）
    request: str = ""           # 用户最初的请求
    investigated: str = ""      # 调研了什么
    learned: str = ""           # 学到了什么
    completed: str = ""         # 完成了什么
    next_steps: str = ""        # 下一步建议
    notes: str = ""             # 其他备注
    prompt_number: int = 0
    discovery_tokens: int = 0           # 🔴 发现成本（ROI 追踪）
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class SessionSummary:
    """
    完整的 SessionSummary 记录
    参考 claude-mem 设计
    """
    id: int
    session_id: str
    project: str                        # 🔴 工作目录/项目路径
    request: str
    investigated: str
    learned: str
    completed: str
    next_steps: str
    notes: str
    prompt_number: int
    discovery_tokens: int               # 🔴 发现成本
    created_at: datetime
    embedding: Optional[bytes] = None


@dataclass
class UserPromptInput:
    """
    创建 UserPrompt 的输入
    
    存储用户的原始请求，用于时间线展示和检索
    参考 claude-mem 设计，独立存储 prompts
    """
    session_id: str
    project: str                        # 工作目录/项目路径
    prompt_number: int                  # Prompt 序号
    prompt_text: str                    # 用户输入的原始文本
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class UserPrompt:
    """
    完整的 UserPrompt 记录（包含数据库生成的 ID）
    参考 claude-mem 设计
    """
    id: int
    session_id: str
    project: str
    prompt_number: int
    prompt_text: str
    created_at: datetime


@dataclass
class SearchFilters:
    """
    搜索过滤器（参考 claude-mem 设计）
    
    用于 HybridSearcher 的 Filter → Rank → Intersect 流程
    🔴 project 是核心过滤字段，优先使用
    """
    session_id: Optional[str] = None        # 限制特定会话
    project: Optional[str] = None           # 🔴 工作目录/项目路径（核心过滤字段，参考 claude-mem）
    types: Optional[list[ObservationType]] = None   # 限制观察类型
    concepts: Optional[list[str]] = None    # 概念标签（AND 逻辑）
    files: Optional[list[str]] = None       # 文件路径列表（匹配 files_read 或 files_modified）
    date_after: Optional[datetime] = None   # 时间范围开始
    date_before: Optional[datetime] = None  # 时间范围结束
    tool_name: Optional[str] = None         # 特定工具
    prompt_number_min: Optional[int] = None
    prompt_number_max: Optional[int] = None


@dataclass
class SearchResult:
    """
    搜索结果
    """
    observation: Observation
    score: float                # 相似度分数（0-1）
    rank: int                   # 排名


# 用于嵌入计算的文本模板（参考 claude-mem）
# 包含 narrative 字段，用于生成高质量的向量表示
OBSERVATION_EMBED_TEMPLATE = """\
Title: {title}
Subtitle: {subtitle}
Type: {type}
Facts: {facts}
Narrative: {narrative}
Concepts: {concepts}
Files: {files}
"""

SUMMARY_EMBED_TEMPLATE = """\
Request: {request}
Investigated: {investigated}
Learned: {learned}
Completed: {completed}
Next Steps: {next_steps}
Notes: {notes}
"""


def format_observation_for_embedding(obs: ObservationInput | Observation) -> str:
    """将 Observation 格式化为用于嵌入计算的文本（参考 claude-mem）"""
    return OBSERVATION_EMBED_TEMPLATE.format(
        title=obs.title,
        subtitle=getattr(obs, 'subtitle', ''),
        type=obs.type.value if isinstance(obs.type, ObservationType) else obs.type,
        facts=', '.join(obs.facts) if obs.facts else '',
        narrative=getattr(obs, 'narrative', ''),
        concepts=', '.join(obs.concepts) if obs.concepts else '',
        files=', '.join(obs.files_modified or obs.files_read or [])
    )


def format_summary_for_embedding(summary: SummaryInput | SessionSummary) -> str:
    """将 Summary 格式化为用于嵌入计算的文本"""
    return SUMMARY_EMBED_TEMPLATE.format(
        request=summary.request,
        investigated=getattr(summary, 'investigated', ''),
        learned=getattr(summary, 'learned', ''),
        completed=getattr(summary, 'completed', ''),
        next_steps=getattr(summary, 'next_steps', ''),
        notes=getattr(summary, 'notes', '')
    )
