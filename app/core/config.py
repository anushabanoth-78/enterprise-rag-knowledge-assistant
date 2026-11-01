from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRETS = {"", "change-me", "change-me-to-a-long-random-string"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"  # set to "production" to enforce startup safety checks
    database_url: str = "postgresql+psycopg://rag:rag@db:5432/rag"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:3000"
    admin_emails: str = ""  # comma separated; these emails become admins at registration

    # --- storage: "local" for development, "s3" for any S3-compatible store (Cloudflare R2, AWS S3, MinIO)
    storage_backend: str = "local"
    upload_dir: str = "/data/uploads"
    s3_endpoint_url: str = ""  # R2: https://<account_id>.r2.cloudflarestorage.com
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    # --- upload limits
    max_upload_mb: int = 50
    max_pages_per_document: int = 500
    max_documents_per_user: int = 100

    # --- retrieval / ingestion
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_words: int = 220
    chunk_overlap: int = 40
    # Cross-encoder logit below which a passage is treated as "not relevant". TUNE this on your own
    # benchmark (experiments/benchmark.jsonl); it is the knob behind "answer not found" behaviour.
    min_rerank_score: float = -13.0
    candidates: int = 30

    # --- evaluation files (results go to a writable volume in Docker)
    benchmark_path: str = "experiments/benchmark.jsonl"
    results_path: str = "experiments/results.csv"

    # --- LLM
    hosted_base_url: str = "https://api.openai.com/v1"
    hosted_api_key: str = ""
    hosted_model: str = "gpt-4o-mini"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"
    llm_timeout_s: float = 60.0
    history_messages: int = 6  # prior messages sent to the LLM for follow-up questions
    rewrite_followups: bool = True  # rewrite "and for contractors?" into a standalone search query

    # --- rate limits (per minute)
    rate_limit_per_minute: int = 60
    auth_rate_limit_per_minute: int = 10  # per client IP, applied to register/login
    trust_proxy_headers: bool = False  # True only behind a proxy you control (uses X-Forwarded-For)

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    def assert_safe_for_production(self) -> None:
        if self.environment.lower() != "production":
            return
        problems = []
        if self.jwt_secret in INSECURE_SECRETS or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be a random string of at least 32 characters")
        if self.storage_backend == "local":
            problems.append("STORAGE_BACKEND=local does not work when API and worker run on separate hosts")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must not contain '*'")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


settings = Settings()
