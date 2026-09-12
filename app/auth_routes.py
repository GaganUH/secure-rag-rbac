from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth_service import SESSION_LIFETIME, authenticate_user, create_auth_session
from app.database import get_database_session
from app.dependencies import get_current_user
from app.models import User
from app.schemas import CurrentUserResponse, LoginRequest, TokenResponse


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(
    login_data: LoginRequest,
    session: Annotated[Session, Depends(get_database_session)],
) -> TokenResponse:
    user = authenticate_user(session, login_data.username, login_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, _auth_session = create_auth_session(session, user)
    return TokenResponse(
        access_token=access_token,
        expires_in_seconds=int(SESSION_LIFETIME.total_seconds()),
    )


@router.get("/me", response_model=CurrentUserResponse)
def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role.name,
        is_active=current_user.is_active,
        permission_version=current_user.permission_version,
    )

