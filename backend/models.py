# -*- coding: utf-8 -*-
"""
Pydantic 请求/响应模型
所有入参在此严格校验，非法输入直接 422 拒绝。
"""
import re

from pydantic import BaseModel, Field, field_validator

from . import security


# ==================== 通用 ====================


# ==================== 认证 ====================

class LoginRequest(BaseModel):
    captcha_id: str = Field(..., min_length=20, max_length=64)
    captcha_answer: str = Field(..., min_length=5, max_length=5)
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        if not security.validate_username(v):
            raise ValueError("用户名格式不正确（3-32位字母数字下划线）")
        return v


class LoginResponse(BaseModel):
    username: str
    expires_in: int


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=8, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def check_new_password(cls, v: str) -> str:
        if not security.is_valid_password(v):
            raise ValueError("新密码不符合安全要求（至少8位，无控制字符）")
        return v


class ChangeUsernameRequest(BaseModel):
    new_username: str = Field(..., min_length=3, max_length=32)
    current_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_username")
    @classmethod
    def check_new_username(cls, v: str) -> str:
        if not security.validate_username(v):
            raise ValueError("用户名格式不正确（3-32位字母数字下划线）")
        return v


# ==================== VPN 客户端 ====================

class ClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=32)
    remark: str = Field("", max_length=200)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        if not security.is_valid_client_name(v):
            raise ValueError("客户端名称格式不正确（2-32位字母数字下划线中划线）")
        return v

    @field_validator("remark")
    @classmethod
    def check_remark(cls, v: str) -> str:
        return security.sanitize_remark(v, 200)


class ClientUpdateRequest(BaseModel):
    remark: str = Field("", max_length=200)

    @field_validator("remark")
    @classmethod
    def check_remark(cls, v: str) -> str:
        return security.sanitize_remark(v, 200)


# ==================== DNS 黑名单 ====================

class DnsAddRequest(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    remark: str = Field("", max_length=200)

    @field_validator("domain")
    @classmethod
    def check_domain(cls, v: str) -> str:
        v = v.strip().lower()
        if not security.is_valid_domain(v):
            raise ValueError("域名格式不正确")
        return v

    @field_validator("remark")
    @classmethod
    def check_remark(cls, v: str) -> str:
        return security.sanitize_remark(v, 200)


class DnsBatchAddRequest(BaseModel):
    domains: str = Field(..., min_length=1, max_length=20000)
    """每行一个域名，或逗号/空格分隔"""

    @field_validator("domains")
    @classmethod
    def check_domains(cls, v: str) -> str:
        # 只允许字母数字中划线点逗号空白换行
        if not re.match(r"^[a-zA-Z0-9.,\s\-_\n\r]+$", v):
            raise ValueError("批量内容含有非法字符")
        return v


class DnsUpdateRequest(BaseModel):
    enabled: bool


class DnsResolveRequest(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)

    @field_validator("domain")
    @classmethod
    def check_domain(cls, v: str) -> str:
        v = v.strip().lower()
        if not security.is_valid_domain(v):
            raise ValueError("域名格式不正确")
        return v


# ==================== 审计日志 ====================

