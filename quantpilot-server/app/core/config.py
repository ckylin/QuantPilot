"""Runtime settings for the QuantPilot server.

All settings can be overridden via environment variables prefixed with ``QP_``
(e.g. ``QP_LLM_API_KEY``) or a ``.env`` file at the project root.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# quantpilot-server/  (app/core/config.py -> app/core -> app -> quantpilot-server)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="QP_",
        extra="ignore",
    )

    # --- LLM: 统一接口，默认 DeepSeek，其他供应商可切换 ---
    # provider: deepseek | openai | qwen | custom（均为 OpenAI 兼容 /chat/completions）
    llm_provider: str = "deepseek"
    # 默认模型 deepseek-flash（可用 QP_LLM_MODEL 覆盖，如 deepseek-chat / deepseek-reasoner）
    llm_model: str = "deepseek-flash"
    llm_small_model: str = "deepseek-flash"
    llm_large_model: str = "deepseek-flash"
    # apikey 通过环境变量 QP_LLM_API_KEY 预留；不填时自动降级为 MockProvider（离线可跑）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    request_timeout: float = 120.0

    # --- Generation control ---
    spec_max_retries: int = 1  # Stage A: extra attempts after validation failure
    code_max_retries: int = 1  # Stage B: extra attempts after AST rejection
    repair_max_rounds: int = 2  # Stage C: max repair rounds after exec error

    # --- Paths ---
    prompts_dir: Path = PROJECT_ROOT / "prompts"

    # --- Storage ---
    # 留空则数据工坊使用内存目录 + JSON 文件（现有行为）；
    # 配置后资产/血缘/质量报告/导入审核记录写入 PostgreSQL。
    # 例：postgresql+psycopg://quantpilot:secret@localhost:5432/quantpilot
    database_url: str = ""
    db_echo: bool = False
    import_max_bytes: int = 10 * 1024 * 1024  # CSV 上传上限

    # --- 策略观察期（Track）---
    # 同时观察上限。不是算力限制，是注意力限制：同时盯 20 个等于没盯。
    track_active_limit: int = 3

    # --- API server ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"  # 逗号分隔；H5 本地预览用
    sandbox_runner: str = "podman"  # podman | local | demo
    podman_bin: str = ""            # 可选：podman.exe 绝对路径（PATH 未刷新时）

    # --- qlib sandbox (Phase 0 reference) ---
    qlib_provider_uri: str = "/data/qlib"
    sandbox_image: str = "localhost/quantpilot/sandbox:0.1"
    qlib_region: str = "cn"


settings = Settings()
