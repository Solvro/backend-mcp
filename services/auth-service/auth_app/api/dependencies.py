from auth_app.api.dependencies import get_current_user
from auth_app.models import User
from fastapi import Depends, HTTPException, status


async def require_email_verified(user: User = Depends(get_current_user)) -> User:
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address is not verified.",
        )
    return user
