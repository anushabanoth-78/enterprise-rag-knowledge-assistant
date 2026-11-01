from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.db import repository
from app.db.models import User
from app.models.schemas import Credentials, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown so "no such user" and "wrong password" take equally long.
_DUMMY_HASH = security.hash_password("dummy-password-for-timing")


@router.post("/register", response_model=TokenOut, status_code=201,
             dependencies=[Depends(security.auth_rate_limit)])
def register(body: Credentials, db: Session = Depends(repository.get_db)):
    if repository.get_user_by_email(db, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    is_admin = body.email.lower() in settings.admin_email_set
    try:
        user = repository.create_user(db, body.email, security.hash_password(body.password), is_admin)
    except IntegrityError:  # two simultaneous registrations for the same email
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return TokenOut(access_token=security.create_access_token(user.id))


@router.post("/login", response_model=TokenOut, dependencies=[Depends(security.auth_rate_limit)])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(repository.get_db)):
    user = repository.get_user_by_email(db, form.username)
    ok = security.verify_password(form.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return TokenOut(access_token=security.create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(security.rate_limit)):
    return user


@router.post("/logout", status_code=204)
def logout(data: security.TokenData = Depends(security.get_token_data)):
    security.revoke_token(data)
