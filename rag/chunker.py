"""
Section-aware чанкер нормативных документов банка.

Стратегия чанкинга
------------------
1. Документы разбиваются по нумерованным markdown-заголовкам (## 2, ### 2.1,
   #### 2.1.3 ...). Нумерация заголовка = `section_id`, ровно как в разметке
   `referenced_documents` из qa.jsonl ("01_credit_products.md#2.1.3").
2. Базовая единица чанка — листовая (самая глубокая) секция. Вводный текст
   родительской секции до первой подсекции сохраняется отдельным чанком с её
   section_id. Так перекрёстные ссылки внутри пункта (п. 2.4.4, № РП-КР-001)
   никогда не разрываются — секция не дробится по середине.
3. Если секция длиннее `max_chars`, она делится по границам абзацев (пустая
   строка) с перекрытием `overlap` символов. section_id сохраняется, добавляется
   индекс части `part`.

Метаданные чанка
----------------
- doc_id      : имя файла, напр. "01_credit_products.md" (совпадает с qa.jsonl)
- doc_code    : короткий код, напр. "credit_products"
- section_id  : нумерация секции, напр. "2.1.3" (или "" для преамбулы)
- title       : заголовок секции
- heading_trail: путь заголовков от корня (для контекста и реранкинга)
- product_code: код продукта, если секция относится к конкретному продукту
- scope       : "specific" (частный/продуктовый пункт) | "general" (общий)
- priority    : числовой приоритет выдачи (specific > general)
- part        : индекс части при дроблении длинной секции

Скрипт не требует сторонних пакетов (только стандартная библиотека).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

# --- маппинг продуктов: имя в заголовке -> product_code (как в clients.sqlite) ---
PRODUCT_NAME_TO_CODE = {
    "Бизнес-Оборот": "BUSINESS_OBOROT",
    "Бизнес-Развитие": "BUSINESS_RAZVITIE",
    "Бизнес-Лимит": "BUSINESS_LIMIT",
    "Бизнес-Старт": "BUSINESS_START",
    "Бизнес-Перезагрузка": "BUSINESS_PEREZAGRUZKA",
}

# scope -> числовой приоритет выдачи. Частный (продуктовый) случай важнее общего.
SCOPE_PRIORITY = {"specific": 1, "general": 0}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# Нумерация в начале заголовка: "2.", "2.1.", "2.1.3.", "4.3.2." и т.п.
NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_code: str
    section_id: str
    title: str
    heading_trail: list[str]
    product_code: Optional[str]
    scope: str
    priority: int
    part: int
    text: str
    char_len: int = field(default=0)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _detect_product(title: str) -> Optional[str]:
    for name, code in PRODUCT_NAME_TO_CODE.items():
        if name in title:
            return code
    return None


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    """Дробит длинный текст по абзацам с перекрытием, не разрывая абзац."""
    if len(text) <= max_chars:
        return [text]
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            parts.append(cur.strip())
            # перекрытие: хвост предыдущей части
            tail = cur[-overlap:] if overlap else ""
            cur = (tail + "\n\n" + p).strip()
        else:
            cur = (cur + "\n\n" + p).strip() if cur else p
    if cur.strip():
        parts.append(cur.strip())
    return parts


def chunk_document(
    path: Path,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[Chunk]:
    doc_id = path.name
    doc_code = re.sub(r"^\d+_", "", path.stem)
    lines = path.read_text(encoding="utf-8").splitlines()

    # Разбор на секции по заголовкам с отслеживанием стека нумерации.
    sections: list[dict] = []
    # текущий стек: список (level, number, title) для построения heading_trail
    stack: list[tuple[int, str, str]] = []
    cur_lines: list[str] = []
    cur_meta: Optional[dict] = None

    def flush():
        nonlocal cur_lines, cur_meta
        if cur_meta is not None:
            body = "\n".join(cur_lines).strip()
            if body:
                cur_meta["text"] = body
                sections.append(cur_meta)
        cur_lines = []

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            raw_title = m.group(2).strip()
            nm = NUMBER_RE.match(raw_title)
            if nm:
                number = nm.group(1)
                title = nm.group(2).strip()
            else:
                number = ""  # заголовок верхнего уровня (название документа) без номера
                title = raw_title
            # поддерживаем стек по глубине нумерации
            depth = number.count(".") + 1 if number else 0
            stack[:] = [s for s in stack if (s[1].count(".") + 1 if s[1] else 0) < depth] if number else []
            stack.append((level, number, title))
            # наследуем product_code от родителей или из текущего заголовка
            product = _detect_product(title)
            if not product:
                for _, _, t in reversed(stack[:-1]):
                    p = _detect_product(t)
                    if p:
                        product = p
                        break
            cur_meta = {
                "doc_id": doc_id,
                "doc_code": doc_code,
                "section_id": number,
                "title": title,
                "heading_trail": [t for _, _, t in stack],
                "product_code": product,
            }
        else:
            if cur_meta is None:
                # преамбула до первого заголовка
                cur_meta = {
                    "doc_id": doc_id,
                    "doc_code": doc_code,
                    "section_id": "",
                    "title": "(преамбула)",
                    "heading_trail": [],
                    "product_code": None,
                }
            cur_lines.append(line)
    flush()

    # Формируем чанки: дробим длинные секции, проставляем scope/priority.
    chunks: list[Chunk] = []
    for sec in sections:
        product = sec["product_code"]
        scope = "specific" if product else "general"
        priority = SCOPE_PRIORITY[scope]
        parts = _split_long(sec["text"], max_chars, overlap)
        for i, ptext in enumerate(parts):
            sid = sec["section_id"] or "0"
            cid = f"{doc_code}#{sid}" + (f"~{i}" if len(parts) > 1 else "")
            # в тело чанка добавляем заголовочный контекст — помогает и поиску, и LLM
            trail = " > ".join(sec["heading_trail"]) if sec["heading_trail"] else sec["title"]
            header_line = f"[{doc_id} · п.{sec['section_id']}] {trail}".strip()
            full_text = f"{header_line}\n{ptext}"
            chunks.append(
                Chunk(
                    chunk_id=cid,
                    doc_id=sec["doc_id"],
                    doc_code=sec["doc_code"],
                    section_id=sec["section_id"],
                    title=sec["title"],
                    heading_trail=sec["heading_trail"],
                    product_code=product,
                    scope=scope,
                    priority=priority,
                    part=i,
                    text=full_text,
                    char_len=len(full_text),
                )
            )
    return chunks


def build_corpus(
    documents_dir: Path,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(documents_dir.glob("*.md")):
        chunks.extend(chunk_document(path, max_chars=max_chars, overlap=overlap))
    return chunks


def save_corpus(chunks: list[Chunk], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    import argparse

    here = Path(__file__).resolve().parent
    default_docs = here.parent / "data" / "documents"
    default_out = here / "corpus_chunks.jsonl"

    ap = argparse.ArgumentParser(description="Чанкинг нормативных документов МСБ")
    ap.add_argument("--documents", type=Path, default=default_docs)
    ap.add_argument("--out", type=Path, default=default_out)
    ap.add_argument("--max-chars", type=int, default=1200)
    ap.add_argument("--overlap", type=int, default=150)
    args = ap.parse_args()

    chunks = build_corpus(args.documents, args.max_chars, args.overlap)
    save_corpus(chunks, args.out)

    from collections import Counter

    by_doc = Counter(c.doc_id for c in chunks)
    by_scope = Counter(c.scope for c in chunks)
    by_prod = Counter(c.product_code for c in chunks if c.product_code)
    lens = [c.char_len for c in chunks]
    print(f"Готово: {len(chunks)} чанков -> {args.out}")
    print(f"Параметры: max_chars={args.max_chars}, overlap={args.overlap}")
    print(f"Длина чанка: min={min(lens)}, avg={sum(lens)//len(lens)}, max={max(lens)}")
    print("По документам:")
    for k, v in sorted(by_doc.items()):
        print(f"  {k}: {v}")
    print(f"По scope: {dict(by_scope)}")
    print(f"По продуктам: {dict(by_prod)}")
