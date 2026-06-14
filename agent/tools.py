"""
tools.py — инструменты доступа к БД клиентов (задача 2.1).

Три read-only инструмента: профиль клиента, действующие кредиты, заявки.
Каждый оформлен дважды:
  1) как обычная функция (get_client_info / get_active_loans / get_applications) —
     её детерминированно вызывает узел query_db;
  2) как LangChain-инструмент с описанием для LLM (CLIENT_TOOLS) — на случай
     tool-calling и для соответствия требованию «tool-функции с описаниями для LLM».

Инструменты НЕ проверяют авторизацию сами — они принимают уже авторизованный
client_id. Проверку выполняет вызывающий код через auth.ensure_self_access, а
соединение открыто read-only (db.get_connection). Это разделение делает
инструменты простыми и тестируемыми, а правило доступа — единым (в auth.py).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional, Union

from langchain_core.tools import tool

from agent.db import get_connection, row_to_dict

DbPath = Optional[Union[Path, str]]


def get_client_info(client_id: str, db_path: DbPath = None) -> Optional[dict]:
    """
    Вернуть профиль клиента по client_id.

    Returns:
        Словарь с полями таблицы clients, либо None, если клиент не найден.
    """
    connection = get_connection(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        return row_to_dict(row)
    finally:
        connection.close()


def get_active_loans(client_id: str, db_path: DbPath = None) -> list[dict]:
    """
    Вернуть действующие кредитные договоры клиента (от свежих к старым).

    Все записи в credit_products — это действующие договоры, поэтому возвращаем
    их все.

    Returns:
        Список словарей (пустой, если кредитов нет).
    """
    connection = get_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM credit_products WHERE client_id = ? "
            "ORDER BY contract_date DESC",
            (client_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_applications(client_id: str, db_path: DbPath = None) -> list[dict]:
    """
    Вернуть историю заявок клиента (включая активные), от свежих к старым.

    Returns:
        Список словарей (пустой, если заявок нет).
    """
    connection = get_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM applications WHERE client_id = ? "
            "ORDER BY application_date DESC",
            (client_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


# --- Детерминированный подбор продуктов (задача доступности, п. 4.1 РП-КР-001) ---
# Скоринг ГАТИТ доступ (D → ничего; C → только Оборот/Старт/Лимит), но его значение
# НЕ раскрывается клиенту (п. 6.2 РП-ОБ-005): причины отказа формулируются по
# фактическим порогам (выручка, срок, счёт, отрасль, сегмент), а скоринговое
# исключение — нейтрально («оценка надёжности профиля ниже требуемой»).

# Градации скоринга и доступная линейка по п. 4.1.
_SCORE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}
# Продукты, требующие рейтинг не ниже B (Развитие, Перезагрузка).
_REQUIRES_B = {"BUSINESS_RAZVITIE", "BUSINESS_PEREZAGRUZKA"}

# Закрытые для «Бизнес-Старт» группы ОКВЭД (п. 2.4.4).
_START_BLOCKED_OKVED = ("64", "65", "66", "92", "02", "47.73")

_PRODUCT_NAMES = {
    "BUSINESS_OBOROT": "Бизнес-Оборот",
    "BUSINESS_RAZVITIE": "Бизнес-Развитие",
    "BUSINESS_LIMIT": "Бизнес-Лимит",
    "BUSINESS_START": "Бизнес-Старт",
    "BUSINESS_PEREZAGRUZKA": "Бизнес-Перезагрузка",
}


def _months_since(date_str: Optional[str], today: datetime.date) -> Optional[int]:
    """Полных месяцев от date_str (ISO) до today; None, если даты нет."""
    if not date_str:
        return None
    try:
        d = datetime.date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None
    return (today.year - d.year) * 12 + (today.month - d.month) - (1 if today.day < d.day else 0)


def get_eligible_products(
    client_id: str,
    db_path: DbPath = None,
    today: Optional[datetime.date] = None,
) -> dict:
    """
    Детерминированно определить, какие кредитные продукты доступны клиенту, по
    документированным порогам РП-КР-001 (выручка, срок ведения деятельности, срок
    счёта, сегмент, отрасль) и правилу доступа по рейтингу (п. 4.1).

    Возвращает структуру для генератора. Значение скоринга НЕ включается — только
    вердикт eligible + причины по фактическим порогам.

    Returns:
        {
          "segment": "micro|small|out_of_segment|unknown",
          "stop_factors": [..],            # п. 3.6, блокируют всё
          "products": [{"code","name","eligible","reasons":[..]}, ...],
        }
    """
    today = today or datetime.date.today()
    profile = get_client_info(client_id, db_path)
    if not profile:
        return {"segment": "unknown", "stop_factors": [], "products": []}

    revenue = profile.get("annual_revenue") or 0
    legal_form = profile.get("legal_form") or ""
    is_self_employed = "самозанят" in legal_form.lower()
    okved = str(profile.get("okved_main") or "")
    score = (profile.get("credit_score") or "").upper()
    score_rank = _SCORE_RANK.get(score, 0)
    biz_months = _months_since(profile.get("registration_date"), today)
    has_account = bool(profile.get("has_account_in_bank"))
    account_months = _months_since(profile.get("account_open_date"), today) if has_account else None
    turnover = profile.get("avg_monthly_turnover") or 0
    loans = get_active_loans(client_id, db_path)
    existing_debt = sum(l.get("principal_outstanding") or 0 for l in loans)
    has_active_start = any(l.get("product_code") == "BUSINESS_START" for l in loans)
    has_overdraft = any(l.get("product_code") == "BUSINESS_LIMIT" for l in loans)

    # Сегмент (п. 1.2).
    if revenue > 800_000_000:
        segment = "out_of_segment"
    elif revenue > 120_000_000:
        segment = "small"
    else:
        segment = "micro"

    # Стоп-факторы (п. 3.6) — блокируют все продукты.
    stop_factors: list[str] = []
    notes = (profile.get("notes") or "").lower()
    if "bankrupt" in notes or "банкрот" in notes:
        stop_factors.append("открытое производство по делу о банкротстве (п. 3.6)")
    if profile.get("has_active_overdue") and (profile.get("max_overdue_days_12m") or 0) > 30:
        stop_factors.append("просроченная задолженность перед Банком свыше 30 дней")
    if segment == "out_of_segment":
        stop_factors.append("выручка превышает 800 млн руб. — сегмент среднего бизнеса, "
                            "вне линейки МСБ")

    def check(code: str) -> dict:
        reasons: list[str] = []
        # Стоп-факторы и сегмент.
        if stop_factors:
            return {"code": code, "name": _PRODUCT_NAMES[code], "eligible": False,
                    "reasons": list(stop_factors)}
        # Самозанятые — только Бизнес-Старт (Приложение А).
        if is_self_employed and code != "BUSINESS_START":
            reasons.append("продукт недоступен самозанятым (только Бизнес-Старт)")
        # Доступ по рейтингу (п. 4.1) — без раскрытия значения.
        if score_rank == 0:
            reasons.append("оценка надёжности профиля ниже требуемой")
        elif code in _REQUIRES_B and score_rank < _SCORE_RANK["B"]:
            reasons.append("оценка надёжности профиля ниже требуемой для этого продукта")
        # Срок счёта в Банке.
        min_account = 3 if code == "BUSINESS_START" else 6
        if not has_account:
            reasons.append("нет расчётного счёта в Банке (можно открыть при подаче)")
        elif account_months is not None and account_months < min_account:
            reasons.append(f"срок счёта в Банке {account_months} мес < {min_account} мес")

        # Продуктовые пороги.
        if code == "BUSINESS_OBOROT":
            if biz_months is not None and biz_months < 12:
                reasons.append(f"срок деятельности {biz_months} мес < 12 мес")
            if revenue < 6_000_000:
                reasons.append("годовая выручка < 6 млн руб.")
        elif code == "BUSINESS_RAZVITIE":
            if biz_months is not None and biz_months < 24:
                reasons.append(f"срок деятельности {biz_months} мес < 24 мес")
            if revenue < 15_000_000:
                reasons.append("годовая выручка < 15 млн руб.")
        elif code == "BUSINESS_LIMIT":
            if biz_months is not None and biz_months < 12:
                reasons.append(f"срок деятельности {biz_months} мес < 12 мес")
            if turnover < 500_000:
                reasons.append("среднемесячные обороты по счёту < 500 тыс. руб.")
            if has_active_start:
                reasons.append("не предоставляется одновременно с Бизнес-Старт (п. 2.3.5)")
        elif code == "BUSINESS_START":
            if biz_months is not None and biz_months < 6:
                reasons.append(f"срок деятельности {biz_months} мес < 6 мес")
            if is_self_employed:
                if revenue < 1_200_000:
                    reasons.append("подтверждённый доход < 1,2 млн руб. (для самозанятых)")
            elif revenue < 2_400_000:
                reasons.append("годовая выручка < 2,4 млн руб.")
            if okved.startswith(_START_BLOCKED_OKVED):
                reasons.append(f"отрасль (ОКВЭД {okved}) закрыта для Бизнес-Старт (п. 2.4.4)")
            if existing_debt > 3_000_000:
                reasons.append("сумма действующих обязательств > 3 млн руб.")
            if has_overdraft:
                reasons.append("не предоставляется одновременно с действующим овердрафтом (п. 2.3.5)")
        elif code == "BUSINESS_PEREZAGRUZKA":
            if biz_months is not None and biz_months < 18:
                reasons.append(f"срок деятельности {biz_months} мес < 18 мес")
            if revenue < 10_000_000:
                reasons.append("годовая выручка < 10 млн руб.")

        return {"code": code, "name": _PRODUCT_NAMES[code],
                "eligible": not reasons, "reasons": reasons}

    products = [check(code) for code in _PRODUCT_NAMES]
    return {"segment": segment, "stop_factors": stop_factors, "products": products}


# --- LangChain-обёртки с описаниями для LLM ------------------------------------
# Узел query_db вызывает функции выше напрямую. Обёртки ниже нужны для требования
# «описания для LLM» и на случай tool-calling-режима. Авторизация при этом
# по-прежнему обеспечивается выше по стеку (узел query_db + auth.ensure_self_access).


@tool
def client_info_tool(client_id: str) -> Optional[dict]:
    """Профиль авторизованного клиента: форма (ООО/ИП/Самозанятый), отрасль,
    выручка, скоринг, отношения с банком (счёт, зарплатный проект), история
    просрочек. Использовать ТОЛЬКО для собственных данных авторизованного клиента."""
    return get_client_info(client_id)


@tool
def active_loans_tool(client_id: str) -> list[dict]:
    """Действующие кредитные договоры авторизованного клиента: остаток долга,
    ставка, срок, дата и сумма следующего платежа, просрочка, обеспечение.
    Только для собственных данных авторизованного клиента."""
    return get_active_loans(client_id)


@tool
def applications_tool(client_id: str) -> list[dict]:
    """История заявок авторизованного клиента со статусами и решениями.
    Только для собственных данных авторизованного клиента."""
    return get_applications(client_id)


@tool
def eligible_products_tool(client_id: str) -> dict:
    """Детерминированный подбор кредитных продуктов, доступных авторизованному
    клиенту, по документированным порогам (выручка, срок деятельности, срок счёта,
    сегмент, отрасль) и правилу доступа по рейтингу. Значение скоринга НЕ
    возвращается — только вердикт и нескоринговые причины. Использовать для
    вопросов «какие кредиты мне доступны / на что я могу рассчитывать»."""
    return get_eligible_products(client_id)


# Список инструментов для биндинга к LLM (если потребуется tool-calling).
CLIENT_TOOLS = [client_info_tool, active_loans_tool, applications_tool, eligible_products_tool]
