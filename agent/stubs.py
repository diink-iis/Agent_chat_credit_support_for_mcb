"""
stubs.py — ОФФЛАЙН-ЗАГЛУШКИ для прогона графа без GigaChat.

Назначение:
  - rule_based_classify — классификатор на ключевых словах (маркеры из РП-ОБ-005).
    Это временный baseline; реальный classify даёт Участник 3 на GigaChat.
  - template_generate — шаблонный генератор ответа из контекста (без LLM).
  - FakeRetriever — заглушка ретривера с контрактом Участника 1, чтобы тестировать
    узел retrieve_rag без GigaChat-эмбеддингов.

Все три инъектируются в граф через GraphDeps. В проде заменяются GigaChat-версиями
(agent/llm.py) и реальным Retriever Участника 1 — контракт узлов не меняется.
"""

from __future__ import annotations

from typing import Optional

from agent.state import AgentState, latest_client_text

# --- Маркеры из РП-ОБ-005 (для baseline-классификатора) ------------------------

_INTENT_MARKERS = (
    "хочу оформить", "хочу подать", "оформите мне", "готов взять",
    "хочу досрочно погасить", "оформите досрочное", "нужна реструктуризация",
    "хочу реструктур", "пересмотрите график", "какой продукт мне подойдёт",
    "что посоветуете", "хочу открыть счёт", "как мне получить кредит",
    "подать заявку",
)

_HUMAN_REQUEST_MARKERS = (
    "переключите на человека", "на человека", "к человеку", "человеку",
    "на оператора", "оператора", "на специалиста", "соедините",
    "не нужен мне бот", "не разговаривайте со мной", "живого человека",
)

_NEGATIVE_MARKERS = (
    "жалоба", "жаловаться", "в центробанк", "в прокуратуру", "в роспотребнадзор",
    "в суд", "иск", "возмутительно", "безобразие", "обманули", "мошенничество",
    "кража", "некомпетентность",
)

# Манипуляции, требующие ЭСКАЛАЦИИ (trigger suspicious): чужой доступ или
# переопределение инструкций (п. 7.2, 7.3, 4.4.2).
_SUSPICIOUS_MARKERS = (
    "забудь инструкции", "забудь все", "игнорируй", "ignore previous", "system prompt",
    "системный промпт", "ты теперь", "с этого момента ты", "представь что ты",
    "данные другого", "чужой клиент", "покажи данные клиента", "выведи всех клиентов",
    "моего партнёра", "моего партнера", "моего конкурента", "моего супруга",
    "у моего", "я бухгалтер", "я директор отделения", "по доверенности",
    "есть доверенность",
)

# Манипуляции, ведущие к ОТКАЗУ без эскалации: выпрашивание исключения/обещания.
_REJECT_MANIP_MARKERS = (
    "сделайте мне исключение", "сделайте исключение", "в виде исключения",
    "только для меня", "гарантируйте", "пообещайте", "обещайте", "дайте гарантию",
    "раскрой критерии скоринга", "раскройте критерии",
)

_OFFTOPIC_MARKERS = (
    "погода", "анекдот", "рецепт", "футбол", "политик", "новости",
    "стихотворение", "напиши код",
)

_DB_MARKERS = (
    "мой кредит", "моя заявка", "моей заявк", "мою заявку", "по заявке", "мой договор",
    "статус заявки", "статус моей", "мои платежи", "мой платёж", "мой платеж",
    "сколько я должен", "остаток по кредиту", "мой остаток", "у меня остаток",
    "остаток по моему", "состояние кредита", "следующий платёж", "следующий платеж",
    "когда у меня", "когда мой", "осталось по", "мне отказали", "мне одобрили",
    "мне ещё подать", "мне еще подать", "рассчитай досроч", "рассчитайте досроч",
    "какие продукты мне доступны", "что мне доступно", "по моему овердрафту",
    "по моему кредиту", "по моей заявке",
)

_PRODUCT_STEMS = {
    "оборот": "BUSINESS_OBOROT",
    "развит": "BUSINESS_RAZVITIE",
    "лимит": "BUSINESS_LIMIT",
    "старт": "BUSINESS_START",
    "перезагруз": "BUSINESS_PEREZAGRUZKA",
}


def _any(text: str, markers) -> bool:
    return any(marker in text for marker in markers)


def _detect_product(text: str) -> Optional[str]:
    cleaned = text.lower().replace("-", " ")
    for stem, code in _PRODUCT_STEMS.items():
        if f"бизнес {stem}" in cleaned:
            return code
    return None


def rule_based_classify(state: AgentState) -> dict:
    """
    Baseline-классификатор на ключевых словах. Возвращает тот же словарь,
    что ожидает узел classify. Приоритет — как в п. 4.1 (эскалация важнее инфо).

    ВНИМАНИЕ: это заглушка. edge_no_data / edge_conflict по ключевым словам
    надёжно не ловятся — их различает реальный классификатор Участника 3.
    """
    text = latest_client_text(state).lower()
    product = _detect_product(text)

    # 1. Детекторы безопасности — высший приоритет (п. 4.1, 7.2/7.3).
    #    Чужой доступ / переопределение инструкций → эскалация (suspicious).
    if _any(text, _SUSPICIOUS_MARKERS):
        return _result("edge_manipulation", "suspicious", needs_db=False, needs_rag=False)
    #    Выпрашивание исключения/обещания → отказ без эскалации.
    if _any(text, _REJECT_MANIP_MARKERS):
        return _result("edge_manipulation", None, needs_db=False, needs_rag=False)

    # 2. Эскалация по намерению/негативу.
    if _any(text, _HUMAN_REQUEST_MARKERS):
        return _result("escalation_negative", "human_request", needs_db=False,
                       needs_rag=False, product=product)
    if _any(text, _NEGATIVE_MARKERS):
        markers = [m for m in _NEGATIVE_MARKERS if m in text]
        return _result("escalation_negative", "negative", needs_db=False,
                       needs_rag=False, product=product, negative_markers=markers)
    if _any(text, _INTENT_MARKERS):
        return _result("escalation_sales", "intent", needs_db=False,
                       needs_rag=False, product=product)

    # 3. Офтоп.
    if _any(text, _OFFTOPIC_MARKERS):
        return _result("offtopic", None, needs_db=False, needs_rag=False)

    # 4. Транзакционные (нужна БД клиента).
    if _any(text, _DB_MARKERS):
        return _result("transactional", None, needs_db=True, needs_rag=True,
                       product=product)

    # 5. По умолчанию — информационный запрос (нужен RAG).
    return _result("info", None, needs_db=False, needs_rag=True, product=product)


def _result(category, trigger, needs_db, needs_rag, product=None, negative_markers=None) -> dict:
    return {
        "category": category,
        "escalation_trigger": trigger,
        "needs_db": needs_db,
        "needs_rag": needs_rag,
        "detected_product": product,
        "negative_markers": negative_markers or [],
    }


def template_generate(state: AgentState) -> str:
    """
    Шаблонный генератор ответа без LLM. Не «умный» — нужен только чтобы граф
    выдавал осмысленный текст в тестах. В проде заменяется GigaChat.
    """
    category = state.get("category")
    tool_results = state.get("tool_results", {})

    # Отказ из-за анонимного канала / чужих данных.
    if tool_results.get("access_denied"):
        return ("Эти сведения доступны только авторизованному клиенту по его "
                "собственным данным. Войдите в интернет-банк или мобильное "
                "приложение, чтобы я мог помочь.")

    if category == "offtopic":
        return ("Я помогаю по вопросам кредитования малого и микробизнеса. "
                "По этой теме подсказать не смогу.")

    if category == "edge_no_data":
        return ("В действующих регламентах нет информации по этому вопросу, "
                "поэтому не могу дать точный ответ и не буду предполагать.")

    if category == "edge_manipulation":
        return ("Не могу выполнить эту просьбу. Я работаю строго в рамках "
                "регламента и не раскрываю служебные сведения и данные других клиентов.")

    # Информационный / транзакционный ответ — собираем из контекста.
    parts: list[str] = []
    if tool_results.get("profile"):
        profile = tool_results["profile"]
        parts.append(
            f"По вашему профилю ({profile.get('legal_form')}, "
            f"{profile.get('industry')}): данные получены."
        )
    if tool_results.get("loans"):
        parts.append(f"Действующих кредитов: {len(tool_results['loans'])}.")
    if tool_results.get("applications"):
        parts.append(f"Заявок в истории: {len(tool_results['applications'])}.")

    context = state.get("retrieved_context", "")
    if context:
        sources = ", ".join(
            f"{tag['source']}" for tag in state.get("scope_tags", [])[:3]
        )
        parts.append(f"[шаблон] Ответ подготовлен по источникам: {sources or 'нет'}.")

    if not parts:
        return ("[шаблон] Недостаточно данных для ответа — в проде здесь ответит GigaChat.")
    return " ".join(parts)


class FakeRetriever:
    """
    Заглушка ретривера с контрактом Участника 1 (.retrieve(query, product_filter, k)).
    Возвращает синтетические чанки, чтобы тестировать узел retrieve_rag без эмбеддингов.
    """

    def retrieve(self, query: str, product_filter: Optional[str] = None, k: int = 5) -> list[dict]:
        hits = []
        for i in range(k):
            scope = "specific" if (product_filter and i == 0) else "general"
            hits.append({
                "chunk_id": f"fake#{i}",
                "doc_id": "01_credit_products.md",
                "doc_code": "credit_products",
                "section_id": f"2.{i}",
                "title": "Заглушка",
                "text": f"[FAKE chunk {i}] релевантно запросу: {query[:60]}",
                "product_code": product_filter if scope == "specific" else None,
                "scope": scope,
                "priority": 1 if scope == "specific" else 0,
                "score": round(0.9 - i * 0.1, 3),
            })
        return hits
