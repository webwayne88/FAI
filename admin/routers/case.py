# routers/case.py
from fastapi import APIRouter, HTTPException, File, UploadFile, Depends, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete, func, or_
from docx import Document
import re
from typing import List, Optional
import io

from db.database import get_db
from db.models import Case

router = APIRouter()

def parse_docx_file(file_content: bytes):
    """
    Парсинг DOCX файла. Кейсы определяются по нескольким пустым
    строкам, идущим подряд.
    """
    try:
        doc = Document(io.BytesIO(file_content))
        cases = []
        
        # Получаем все параграфы, убирая лишние пробелы по краям
        all_paragraphs = [p.text.strip() for p in doc.paragraphs]
        
        current_case_lines = []
        full_cases_text = []

        # Проходим по всем параграфам, чтобы собрать их в отдельные кейсы
        for i, p_text in enumerate(all_paragraphs):
            # Проверяем, является ли текущая строка частью разделителя
            # (3 или более пустых строки подряд)
            is_separator = (
                not p_text and
                i > 0 and not all_paragraphs[i-1] and
                i > 1 and not all_paragraphs[i-2]
            )

            # Если нашли разделитель и у нас есть накопленный текст кейса,
            # сохраняем его и начинаем собирать новый.
            if is_separator and current_case_lines:
                full_cases_text.append("\n".join(current_case_lines))
                current_case_lines = []
            # Если строка не пустая, добавляем ее к текущему кейсу
            elif p_text:
                current_case_lines.append(p_text)
        
        # Не забываем добавить последний кейс в список
        if current_case_lines:
            full_cases_text.append("\n".join(current_case_lines))

        # Теперь обрабатываем каждый собранный текстовый блок кейса
        for text_block in full_cases_text:
            if not text_block.strip():
                continue

            # Ищем раздел "РОЛИ И ИНТЕРЕСЫ"
            roles_pattern = re.compile(r'РОЛИ И ИНТЕРЕСЫ|Роли и интересы', re.IGNORECASE)
            roles_match = roles_pattern.search(text_block)
            
            content_part = text_block
            roles_part = ""

            if roles_match:
                # Все, что до "РОЛИ И ИНТЕРЕСЫ", — это описание
                content_part = text_block[:roles_match.start()].strip()
                # Все, что после, — это роли (удаляем само слово и лишние символы)
                roles_part = text_block[roles_match.end():].lstrip(' :').strip()

            # Считаем, что первая строка — это всегда заголовок
            lines = content_part.split('\n')
            title = lines[0].strip() if lines else "Кейс без названия"
            content = '\n'.join(lines[1:]).strip()
            
            cases.append({
                "title": title,
                "content": content,
                "roles": roles_part
            })
            
        return cases
    except Exception as e:
        # Это поможет увидеть ошибку в консоли, если что-то пойдет не так
        print(f"Ошибка при парсинге DOCX: {e}") 
        return []


@router.post("/upload")
async def upload_cases_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Загрузить и распарсить файл с кейсами"""
    try:
        # Читаем содержимое файла
        file_content = await file.read()
        
        # Парсим файл
        cases_data = parse_docx_file(file_content)
        
        # Очищаем старые кейсы
        await db.execute(delete(Case))
        await db.commit()
        
        # Сохраняем новые кейсы в базу
        for case_data in cases_data:
            # Пропускаем пустые кейсы
            if not case_data["title"] or not case_data["content"]:
                continue
                
            db_case = Case(
                title=case_data["title"],
                content=case_data["content"],
                roles=case_data.get("roles", "")
            )
            db.add(db_case)
        
        await db.commit()
        
        # Получаем количество сохраненных кейсов
        result = await db.execute(select(Case))
        saved_cases = result.scalars().all()
        
        return {
            "message": f"Успешно загружено {len(saved_cases)} кейсов",
            "count": len(saved_cases)
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при загрузке файла: {str(e)}"
        )

@router.get("/", response_model=dict)
async def get_cases(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=100),
    search: Optional[str] = Query(None)
):
    """Получить список всех кейсов с пагинацией и поиском"""
    try:
        # Вычисляем смещение
        offset = (page - 1) * per_page
        
        # Базовый запрос
        query = select(Case)
        
        # Добавляем условие поиска, если указано
        if search:
            search_filter = or_(
                Case.title.ilike(f"%{search}%"),
                Case.content.ilike(f"%{search}%"),
                Case.roles.ilike(f"%{search}%")
            )
            query = query.where(search_filter)
        
        # Получаем общее количество кейсов (с учетом фильтра поиска)
        total_count_query = select(func.count(Case.id))
        if search:
            total_count_query = total_count_query.where(search_filter)
            
        total_count_result = await db.execute(total_count_query)
        total_count = total_count_result.scalar()
        
        # Получаем кейсы для текущей страницы
        result = await db.execute(
            query
            .order_by(Case.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        cases = result.scalars().all()
        
        # Вычисляем общее количество страниц
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        
        return {
            "cases": [
                {
                    "id": case.id,
                    "title": case.title,
                    "content": case.content,
                    "roles": case.roles,
                    "is_active": case.is_active,
                    "created_at": case.created_at
                }
                for case in cases
            ],
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": page,
            "per_page": per_page
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при получении кейсов: {str(e)}"
        )

@router.post("/")
async def create_case(
    case_data: dict,  # Будем принимать JSON с данными кейса
    db: AsyncSession = Depends(get_db)
):
    """Создать новый кейс вручную"""
    try:
        # Проверяем обязательные поля
        if not case_data.get("title") or not case_data.get("content"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Название и содержание кейса обязательны"
            )
        
        # Создаем новый кейс
        db_case = Case(
            title=case_data["title"],
            content=case_data["content"],
            roles=case_data.get("roles", "")
        )
        db.add(db_case)
        await db.commit()
        await db.refresh(db_case)
        
        return {
            "message": "Кейс успешно добавлен",
            "case": {
                "id": db_case.id,
                "title": db_case.title,
                "content": db_case.content,
                "roles": db_case.roles,
                "is_active": db_case.is_active,
                "created_at": db_case.created_at
            }
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при создании кейса: {str(e)}"
        )


@router.delete("/{case_id}")
async def delete_case(case_id: int, db: AsyncSession = Depends(get_db)):
    """Удалить кейс по ID"""
    try:
        result = await db.execute(select(Case).where(Case.id == case_id))
        case = result.scalar_one_or_none()
        
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Кейс не найден"
            )
        
        await db.delete(case)
        await db.commit()
        
        return {"message": f"Кейс '{case.title}' успешно удален"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при удалении кейса: {str(e)}"
        )

@router.put("/{case_id}/toggle")
async def toggle_case(case_id: int, db: AsyncSession = Depends(get_db)):
    """Переключить активность кейса"""
    try:
        result = await db.execute(select(Case).where(Case.id == case_id))
        case = result.scalar_one_or_none()
        
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Кейс не найден"
            )
        
        case.is_active = not case.is_active
        await db.commit()
        
        status_text = "активен" if case.is_active else "неактивен"
        return {"message": f"Кейс '{case.title}' теперь {status_text}"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении кейса: {str(e)}"
        )

@router.get("/{case_id}")
async def get_case(case_id: int, db: AsyncSession = Depends(get_db)):
    """Получить кейс по ID"""
    try:
        result = await db.execute(select(Case).where(Case.id == case_id))
        case = result.scalar_one_or_none()
        
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Кейс не найден"
            )
        
        return {
            "id": case.id,
            "title": case.title,
            "content": case.content,
            "roles": case.roles,
            "is_active": case.is_active,
            "created_at": case.created_at
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при получении кейса: {str(e)}"
        )

@router.put("/{case_id}")
async def update_case(
    case_id: int, 
    case_data: dict,
    db: AsyncSession = Depends(get_db)
):
    """Обновить кейс по ID"""
    try:
        result = await db.execute(select(Case).where(Case.id == case_id))
        case = result.scalar_one_or_none()
        
        if not case:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Кейс не найден"
            )
        
        # Обновляем поля, если они предоставлены
        if "title" in case_data:
            case.title = case_data["title"]
        if "content" in case_data:
            case.content = case_data["content"]
        if "roles" in case_data:
            case.roles = case_data["roles"]
        if "is_active" in case_data:
            case.is_active = case_data["is_active"]
        
        await db.commit()
        await db.refresh(case)
        
        return {
            "message": "Кейс успешно обновлен",
            "case": {
                "id": case.id,
                "title": case.title,
                "content": case.content,
                "roles": case.roles,
                "is_active": case.is_active,
                "created_at": case.created_at
            }
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении кейса: {str(e)}"
        )
