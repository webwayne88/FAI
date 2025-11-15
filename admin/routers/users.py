# admin/routers/users.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, or_
from db.database import get_db
from db.models import User, TimePreference
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

router = APIRouter(prefix="/users", tags=["users"])


class UserResponse(BaseModel):
    id: int
    tg_id: Optional[int]
    full_name: Optional[str]
    university: Optional[str]
    registered: bool
    time_preference: Optional[TimePreference]
    wins_count: int
    sum_points: int
    matches_played: int
    eliminated: bool
    total_transcription_length: int


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    university: Optional[str] = None
    contact: Optional[str] = None
    time_preference: Optional[str] = None
    wins_count: Optional[int] = None
    sum_points: Optional[int] = None
    matches_played: Optional[int] = None
    registered: Optional[bool] = None
    eliminated: Optional[bool] = None


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    """Обновление информации о пользователе"""
    try:
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(
                status_code=404, detail="Пользователь не найден")

        # Получаем только переданные поля (исключаем None значения)
        update_data = {k: v for k, v in user_data.dict().items()
                       if v is not None}

        # Обрабатываем time_preference отдельно - только если оно передано и не пустое
        if "time_preference" in update_data:
            pref_key = update_data.pop("time_preference")
            if pref_key:  # Только если не пустая строка
                try:
                    enum_member = getattr(TimePreference, pref_key)
                    setattr(user, "time_preference", enum_member)
                except AttributeError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid time_preference key '{pref_key}'."
                    )
            # Если pref_key пустая строка - просто игнорируем, не меняем текущее значение

        # Обновляем остальные поля
        for key, value in update_data.items():
            setattr(user, key, value)

        await db.commit()
        await db.refresh(user)

        return user
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка обновления пользователя: {str(e)}"
        )


@router.get("/", response_model=List[UserResponse])
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    # Добавляем eliminated в сортировку
    sort_by: str = Query(
        "full_name", regex="^(id|full_name|university|contact|matches_played|wins_count|sum_points|registered|eliminated)$"),
    order: str = Query("asc", regex="^(asc|desc)$"),
    search: Optional[str] = Query(None),
    hide_eliminated: bool = Query(False),  # Новый параметр для фильтрации
    db: AsyncSession = Depends(get_db)
):
    """Получение списка пользователей с пагинацией и сортировкой"""
    try:
        query = select(User)

        if search:
            query = query.where(
                or_(
                    User.full_name.ilike(f"%{search}%"),
                    User.university.ilike(f"%{search}%")
                )
            )

        # Фильтрация по eliminated
        if hide_eliminated:
            query = query.where(User.eliminated == False)

        sort_column = getattr(User, sort_by, None)
        if sort_column is not None:
            if order == "asc":
                query = query.order_by(sort_column.asc())
            else:
                query = query.order_by(sort_column.desc())

        query = query.offset(skip).limit(limit)

        result = await db.execute(query)
        users = result.scalars().all()

        return users
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка получения пользователей: {str(e)}"
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Получение информации о конкретном пользователе"""
    try:
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=404, detail="Пользователь не найден")

        return user
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка получения пользователя: {str(e)}"
        )


@router.get("/stats/count")
async def get_users_count(
    hide_eliminated: bool = Query(False),  # Добавляем параметр фильтрации
    db: AsyncSession = Depends(get_db)
):
    """Получение общего количества пользователей"""
    try:
        query = select(func.count(User.id))

        if hide_eliminated:
            query = query.where(User.eliminated == False)

        count = await db.scalar(query)
        return {"total_users": count}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка получения количества пользователей: {str(e)}"
        )
