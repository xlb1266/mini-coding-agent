"""SQLAlchemy 声明基类。

独立成模块，避免 base.py（模型注册处）与 models 包相互等待：
模型统一从本模块导入 Base，业务表在 app/db/base.py 中注册。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
