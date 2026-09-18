"""模型管理业务表：模型配置与加密存储的密钥。

明文密钥不落库：API Key 经 app/core/secret_box.py 加密后存入 model_secrets，
与普通业务表分离，删除模型时随级联一并清理。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class ModelConfig(Base):
    """一个 OpenAI Chat Completions 兼容的模型服务配置。"""

    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    # 价格为"每百万 token / 元"；None 表示未填写，与 0（免费）区分
    input_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_input_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 删除模型时由 ORM 级联删除密钥行，保证密钥不残留
    secret: Mapped["ModelSecret | None"] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ModelSecret(Base):
    """模型 API Key 的加密存储，主密钥在本地 secret.key 文件中。"""

    __tablename__ = "model_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("model_configs.id"), nullable=False, unique=True
    )
    key_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    key_blob: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    model: Mapped["ModelConfig"] = relationship(back_populates="secret")
