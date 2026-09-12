"""``/users``: administrator account management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import UserServiceDep, require_min_role
from app.api.schemas.users import ResetPasswordRequest, UserCreateRequest, UserResponse, UserUpdateRequest
from app.core.security import CurrentUser
from app.domain.enums import Role

router = APIRouter(prefix="/users", tags=["users"])

Admin = Annotated[CurrentUser, Depends(require_min_role(Role.ADMIN))]


@router.get("", response_model=list[UserResponse], summary="List users")
def list_users(_user: Admin, service: UserServiceDep, active_only: bool = False) -> list[UserResponse]:
    return [UserResponse.from_record(u) for u in service.list(active_only=active_only)]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED, summary="Create a user")
def create_user(body: UserCreateRequest, user: Admin, service: UserServiceDep) -> UserResponse:
    record = service.create(
        user,
        username=body.username,
        password=body.password,
        role=body.role,
        display_name=body.display_name,
        email=body.email,
        reason=body.reason,
    )
    return UserResponse.from_record(record)


@router.get("/{user_id}", response_model=UserResponse, summary="One user")
def get_user(user_id: str, _user: Admin, service: UserServiceDep) -> UserResponse:
    return UserResponse.from_record(service.get(user_id))


@router.patch("/{user_id}", response_model=UserResponse, summary="Activate or deactivate a user")
def update_user(user_id: str, body: UserUpdateRequest, user: Admin, service: UserServiceDep) -> UserResponse:
    return UserResponse.from_record(service.set_active(user_id, body.active, user, body.reason))


@router.post("/{user_id}/reset-password", response_model=UserResponse, summary="Reset a user's password")
def reset_password(
    user_id: str, body: ResetPasswordRequest, user: Admin, service: UserServiceDep
) -> UserResponse:
    return UserResponse.from_record(service.reset_password(user_id, body.password, user, body.reason))


__all__ = ["router"]
