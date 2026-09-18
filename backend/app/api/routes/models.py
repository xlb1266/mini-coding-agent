"""模型管理路由：列表、新增、编辑、删除、设为默认、测试连接。

一致性约定：
- 有模型时必须且只有一个默认模型（保存第一个/无默认时自动成为默认，
  切换与删除在同一事务内完成）；
- 被 Agent 引用的模型不允许删除（先检查 Agent 引用，再执行默认项切换
  规则），需用户先更换模型、移除引用或删除相关 Agent；
- 密钥只经 SecretBox 加密后落 model_secrets 表，任何响应与错误信息
  不携带密钥正文，仅返回 hasApiKey 状态。
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.secret_box import SecretBox, SecretBoxError, get_secret_box
from app.db.models.model_config import ModelConfig, ModelSecret
from app.db.session import get_db
from app.schemas.model import ModelCreateRequest, ModelOut, ModelUpdateRequest, TestConnectionOut
from app.services.agent_refs import agent_names_using_model
from app.services.model_client import MAX_TEST_TOKENS, ModelCallError, chat_completion

router = APIRouter(prefix="/models", tags=["models"])

# 校验失败时返回 {"detail": {"message", "fields"}}，前端据此在字段旁显示原因
FIELD_ERROR_STATUS = 422
DEFAULT_CONFLICT_STATUS = 409
# 资源仍被 Agent 引用时禁止删除（引用检查在后端执行）
REFERENCE_CONFLICT_STATUS = 409


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _field_error(errors: dict[str, str], message: str = "表单校验未通过，请检查填写内容") -> HTTPException:
    return HTTPException(status_code=FIELD_ERROR_STATUS, detail={"message": message, "fields": errors})


def _validate_payload(payload: ModelCreateRequest | ModelUpdateRequest) -> dict[str, str]:
    """跨字段与格式校验，返回 {驼峰字段名: 原因}。"""
    errors: dict[str, str] = {}

    if not payload.display_name.strip():
        errors["displayName"] = "请填写显示名称"
    if not payload.model_identifier.strip():
        errors["modelIdentifier"] = "请填写模型标识"

    errors.update(_validate_base_url(payload.base_url))

    if payload.context_length < 1:
        errors["contextLength"] = "上下文长度必须为正整数"
    if payload.max_output_tokens < 1:
        errors["maxOutputTokens"] = "最大输出长度必须为正整数"
    elif payload.max_output_tokens > payload.context_length:
        errors["maxOutputTokens"] = "最大输出长度不能超过上下文长度"

    if not 0 <= payload.temperature <= 2:
        errors["temperature"] = "温度范围为 0–2"

    for field_name, key, label in (
        ("input_price", "inputPrice", "输入单价"),
        ("output_price", "outputPrice", "输出单价"),
        ("cached_input_price", "cachedInputPrice", "缓存命中输入单价"),
    ):
        value = getattr(payload, field_name)
        if value is not None and value < 0:
            errors[key] = f"{label}不能为负数"

    return errors


def _validate_base_url(raw_url: str) -> dict[str, str]:
    url = raw_url.strip()
    if not url:
        return {"baseUrl": "请填写服务地址"}
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"baseUrl": "请填写以 http:// 或 https:// 开头的完整服务地址"}
    if parsed.path.rstrip("/").lower().endswith("/chat/completions"):
        return {"baseUrl": "服务地址不需要包含 /chat/completions，请填写到 /v1 为止"}
    return {}


def _normalize_base_url(raw_url: str) -> str:
    return raw_url.strip().rstrip("/")


def _get_or_404(db: Session, model_id: int) -> ModelConfig:
    model = db.get(ModelConfig, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="模型不存在或已被删除")
    return model


def _to_out(model: ModelConfig) -> ModelOut:
    return ModelOut(
        id=model.id,
        display_name=model.display_name,
        model_identifier=model.model_identifier,
        base_url=model.base_url,
        context_length=model.context_length,
        max_output_tokens=model.max_output_tokens,
        temperature=model.temperature,
        input_price=model.input_price,
        output_price=model.output_price,
        cached_input_price=model.cached_input_price,
        is_default=model.is_default,
        has_api_key=model.secret is not None,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _apply_fields(model: ModelConfig, payload: ModelCreateRequest | ModelUpdateRequest) -> None:
    model.display_name = payload.display_name.strip()
    model.model_identifier = payload.model_identifier.strip()
    model.base_url = _normalize_base_url(payload.base_url)
    model.context_length = payload.context_length
    model.max_output_tokens = payload.max_output_tokens
    model.temperature = payload.temperature
    model.input_price = payload.input_price
    model.output_price = payload.output_price
    model.cached_input_price = payload.cached_input_price
    model.updated_at = _utcnow()


def _upsert_secret(db: Session, model: ModelConfig, api_key: str, box: SecretBox) -> None:
    nonce, blob = box.encrypt(api_key.strip())
    secret = model.secret
    if secret is None:
        secret = ModelSecret(model_id=model.id, key_nonce=nonce, key_blob=blob, updated_at=_utcnow())
        db.add(secret)
        model.secret = secret
    else:
        secret.key_nonce = nonce
        secret.key_blob = blob
        secret.updated_at = _utcnow()


@router.get("")
def list_models(db: Session = Depends(get_db)) -> list[ModelOut]:
    """默认模型排在最前，其余按最近更新倒序。"""
    models = (
        db.query(ModelConfig)
        .order_by(ModelConfig.is_default.desc(), ModelConfig.updated_at.desc())
        .all()
    )
    return [_to_out(model) for model in models]


@router.get("/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db)) -> ModelOut:
    return _to_out(_get_or_404(db, model_id))


@router.post("", status_code=201)
def create_model(
    payload: ModelCreateRequest,
    db: Session = Depends(get_db),
    box: SecretBox = Depends(get_secret_box),
) -> ModelOut:
    errors = _validate_payload(payload)
    if errors:
        raise _field_error(errors)

    model = ModelConfig(created_at=_utcnow(), updated_at=_utcnow())
    _apply_fields(model, payload)
    if payload.api_key and payload.api_key.strip():
        _upsert_secret(db, model, payload.api_key, box)

    # 一致性：无默认模型时（包括保存第一个模型），自动把新模型设为默认
    has_default = db.query(ModelConfig.id).filter(ModelConfig.is_default.is_(True)).first() is not None
    model.is_default = not has_default

    db.add(model)
    db.commit()
    db.refresh(model)
    return _to_out(model)


@router.put("/{model_id}")
def update_model(
    model_id: int,
    payload: ModelUpdateRequest,
    db: Session = Depends(get_db),
    box: SecretBox = Depends(get_secret_box),
) -> ModelOut:
    model = _get_or_404(db, model_id)

    errors = _validate_payload(payload)
    if errors:
        raise _field_error(errors)

    _apply_fields(model, payload)
    # api_key 为空表示保留原密钥；只有显式提供新密钥才替换，编辑其他字段不会清除密钥
    if payload.api_key and payload.api_key.strip():
        _upsert_secret(db, model, payload.api_key, box)

    db.commit()
    db.refresh(model)
    return _to_out(model)


@router.put("/{model_id}/default")
def set_default_model(model_id: int, db: Session = Depends(get_db)) -> ModelOut:
    model = _get_or_404(db, model_id)

    # 同一事务内完成切换，保证任意时刻至多一个默认模型
    db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).update({"is_default": False})
    model.is_default = True

    db.commit()
    db.refresh(model)
    return _to_out(model)


@router.delete("/{model_id}", status_code=204)
def delete_model(
    model_id: int,
    new_default_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> None:
    model = _get_or_404(db, model_id)

    # 引用检查先于默认项切换规则：被 Agent 引用的模型一律不允许删除，
    # 需用户先在对应 Agent 中更换模型、移除引用或删除相关 Agent
    ref_names = agent_names_using_model(db, model_id)
    if ref_names:
        raise HTTPException(
            status_code=REFERENCE_CONFLICT_STATUS,
            detail=f"该模型正在被 Agent 使用：{'、'.join(f'「{name}」' for name in ref_names)}。"
            "请先在对应 Agent 中更换模型，或删除相关 Agent 后再删除该模型",
        )

    others_exist = db.query(ModelConfig.id).filter(ModelConfig.id != model_id).first() is not None

    new_default: ModelConfig | None = None
    if model.is_default and others_exist:
        # 删除默认模型且还有其他模型时，必须先指定新的默认模型
        if new_default_id is None:
            raise HTTPException(
                status_code=DEFAULT_CONFLICT_STATUS,
                detail="该模型是默认模型，请先选择新的默认模型再删除",
            )
        if new_default_id == model_id:
            raise HTTPException(status_code=FIELD_ERROR_STATUS, detail="新的默认模型不能是被删除的模型本身")
        new_default = db.get(ModelConfig, new_default_id)
        if new_default is None:
            raise HTTPException(status_code=FIELD_ERROR_STATUS, detail="所选的新默认模型不存在")

    # 级联删除密钥行；切换默认与删除在同一事务提交
    db.delete(model)
    if new_default is not None:
        new_default.is_default = True
    db.commit()


@router.post("/{model_id}/test")
def test_model(
    model_id: int,
    db: Session = Depends(get_db),
    box: SecretBox = Depends(get_secret_box),
) -> TestConnectionOut:
    model = _get_or_404(db, model_id)

    api_key: str | None = None
    if model.secret is not None:
        try:
            api_key = box.decrypt(model.secret.key_nonce, model.secret.key_blob)
        except SecretBoxError:
            return TestConnectionOut(
                ok=False,
                error_type="secret",
                message="本地保存的密钥无法解密",
                hint="主密钥文件可能被移动或重建，请重新编辑该模型并填写 API Key",
            )

    max_tokens = max(1, min(model.max_output_tokens, MAX_TEST_TOKENS))
    try:
        reply = chat_completion(
            base_url=model.base_url,
            model_identifier=model.model_identifier,
            api_key=api_key,
            max_tokens=max_tokens,
        )
    except ModelCallError as error:
        return TestConnectionOut(
            ok=False, error_type=error.error_type, message=error.message, hint=error.hint
        )

    return TestConnectionOut(ok=True, reply=reply)
