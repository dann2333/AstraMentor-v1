"""
AstraMentor 配置管理模块

包含API配置、模型配置和学习参数配置
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# 加载.env文件中的环境变量
try:
    from dotenv import load_dotenv

    # 查找项目根目录下的.env文件
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # 如果没有安装python-dotenv，则跳过


@dataclass
class APIConfig:
    """API配置类"""

    # 模型提供商：moonshot(kimi) / gemini / zhipu / qwen / 任意 OpenAI 兼容
    #
    # 默认一整套都指向 Kimi K3：部署时只需要给一个 ASTRA_API_KEY，其余
    # 三项不用填。想换别的模型再覆盖对应变量即可。
    provider: str = field(
        default_factory=lambda: os.getenv("ASTRA_PROVIDER", "moonshot")
    )

    # API 端点地址（只到 /v1，不要追加 /chat/completions）
    api_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "ASTRA_API_ENDPOINT", "https://api.moonshot.cn/v1"
        )
    )

    # API密钥（必须从环境变量读取，代码与镜像里都不内置）
    api_key: str = field(default_factory=lambda: os.getenv("ASTRA_API_KEY", ""))

    # 传输方式
    transport: str = "rest"

    # 默认模型
    model_name: str = field(
        default_factory=lambda: os.getenv("ASTRA_MODEL_NAME", "kimi-k3")
    )

    # 推理强度，仅对支持它的模型生效（Kimi K3：low / high / max）。
    # 默认 low：K3 关不掉思考，默认档是 max，一次星图生成能想上好几分钟，
    # 教学场景里那点质量提升完全不值这个等待和 token。
    reasoning_effort: str = field(
        default_factory=lambda: os.getenv("ASTRA_REASONING_EFFORT", "low").strip().lower()
    )

    # Web Research（Google Search Grounding）开关
    web_search_enabled: bool = field(
        default_factory=lambda: os.getenv("ASTRA_WEB_SEARCH_ENABLED", "true").lower() == "true"
    )


@dataclass
class LearningConfig:
    """学习参数配置类"""

    # 学习率（α），决定练习对总成绩的影响程度
    # 通常取值 0.2 - 0.4
    learning_rate: float = 0.3

    # 任务难度上限配置
    # 选择题/概念问答
    difficulty_concept: float = 0.4
    # 基础代码填空
    difficulty_basic_code: float = 0.7
    # 复杂项目/手写算法
    difficulty_advanced: float = 1.0

    # 默认期望掌握度
    default_target_mastery: float = 0.8


@dataclass
class StorageConfig:
    """本地数据存储配置（SQLite）"""

    # SQLite 数据库文件路径
    database_path: str = field(
        default_factory=lambda: os.getenv(
            "ASTRA_DB_PATH", str(Path("user_data") / "astramentor.db")
        )
    )


@dataclass
class AuthConfig:
    """登录与账号安全配置"""

    # 访问令牌有效期（小时），默认 7 天
    token_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("ASTRA_AUTH_TOKEN_TTL_HOURS", "168"))
    )

    # 连续登录失败多少次后临时锁定账号（0 表示不锁定）
    max_failed_attempts: int = field(
        default_factory=lambda: int(os.getenv("ASTRA_AUTH_MAX_FAILED_ATTEMPTS", "8"))
    )

    # 锁定时长（分钟）
    lockout_minutes: int = field(
        default_factory=lambda: int(os.getenv("ASTRA_AUTH_LOCKOUT_MINUTES", "15"))
    )

    # 是否允许未登录访客使用学习接口。
    # 允许时访客数据统一挂在预留的访客账号下，与任何真实账号相互隔离；
    # 设为 false 即可让整站强制登录（班级与作业接口始终要求登录，不受此开关影响）。
    allow_anonymous: bool = field(
        default_factory=lambda: os.getenv("ASTRA_ALLOW_ANONYMOUS", "true").lower()
        != "false"
    )


@dataclass
class ServerConfig:
    """对外暴露相关的配置。

    这几项的默认值是按"本机自己用"定的。开到公网时必须显式收紧——
    README 的公网部署一节列了完整清单。
    """

    # 在线 IDE 的代码执行开关。
    #
    # CodeRunner 是直接 subprocess 跑使用者提交的代码，没有沙箱：能读到
    # 容器里的一切（SQLite 库、上传的文件、环境变量里的 API Key），也能
    # 往外发网络请求。本机自己用没问题，挂到公网上就是把一个 RCE 接口
    # 摆在门口，所以公网部署一律设成 false。
    code_runner_enabled: bool = field(
        default_factory=lambda: os.getenv("ASTRA_CODE_RUNNER_ENABLED", "true").lower()
        != "false"
    )

    # 允许跨域访问的来源，逗号分隔。默认 "*" 只适合本机开发。
    # 单容器部署时前后端同源，这里填自己的域名即可（或干脆留空）。
    cors_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv("ASTRA_CORS_ORIGINS", "*").split(",")
            if origin.strip()
        ]
    )

    # 单次注册开关。公开部署又不想让任何人都能建号时设成 false，
    # 已有账号照常登录。
    registration_enabled: bool = field(
        default_factory=lambda: os.getenv("ASTRA_REGISTRATION_ENABLED", "true").lower()
        != "false"
    )


@dataclass
class Config:
    """全局配置类"""

    api: APIConfig = field(default_factory=APIConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


# 全局配置实例
config = Config()


def get_config() -> Config:
    """获取全局配置实例"""
    return config
