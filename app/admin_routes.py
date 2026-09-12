from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_database_session
from app.dependencies import require_admin
from app.models import User
from app.schemas import (
    ManagedUserResponse,
    UserCreateRequest,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
)
from app.user_service import (
    UserNotFoundError,
    UsernameAlreadyExistsError,
    change_user_role,
    change_user_status,
    create_user,
    list_users,
)


router = APIRouter(prefix="/admin", tags=["Admin"])


def to_user_response(user: User) -> ManagedUserResponse:
    return ManagedUserResponse(
        id=user.id,
        username=user.username,
        role=user.role.name,
        is_active=user.is_active,
        permission_version=user.permission_version,
    )


@router.post(
    "/users",
    response_model=ManagedUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_managed_user(
    user_data: UserCreateRequest,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> ManagedUserResponse:
    try:
        user = create_user(
            session,
            user_data.username,
            user_data.password,
            user_data.role,
        )
    except UsernameAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return to_user_response(user)


@router.get("/users", response_model=list[ManagedUserResponse])
def read_users(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> list[ManagedUserResponse]:
    return [to_user_response(user) for user in list_users(session)]


@router.patch("/users/{user_id}/role", response_model=ManagedUserResponse)
def update_user_role(
    user_id: int,
    update: UserRoleUpdateRequest,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> ManagedUserResponse:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An Admin cannot change their own role.",
        )
    try:
        user = change_user_role(session, user_id, update.role)
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return to_user_response(user)


@router.patch("/users/{user_id}/status", response_model=ManagedUserResponse)
def update_user_status(
    user_id: int,
    update: UserStatusUpdateRequest,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> ManagedUserResponse:
    if user_id == admin.id and not update.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An Admin cannot deactivate their own account.",
        )
    try:
        user = change_user_status(session, user_id, update.is_active)
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return to_user_response(user)

