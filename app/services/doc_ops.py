"""문서 수정 연산 엔진 — 순수 함수. DB를 만지지 않는다.

apply_operations(pages, ops) -> 새 pages. 하나라도 실패하면 OpError를 던지고 입력은 바뀌지 않는다(사본에 적용).
규칙(contracts.md Document절): 존재하지 않는 대상, 자기 뒤로 이동, 다른 페이지의 after_block_id, 중복 ID는 거부.
replace_block_content는 content만 바꾸고 block_id·type·fact_ids·evidence_refs는 보존한다.
"""
from __future__ import annotations

import copy
from typing import Any

from app.models import Block, Operation, Page

CONTENT_KEYS: dict[str, set[str]] = {
    "heading": {"text", "level"},
    "paragraph": {"text"},
    "list": {"items"},
    "image": {"asset_id", "alt", "caption", "fit"},
    "image_placeholder": {"description"},
}


class OpError(Exception):
    def __init__(self, index: int, op: str, reason: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{index}] {op}: {reason}")
        self.index = index
        self.op = op
        self.reason = reason
        self.details = details or {}


def check_content(block_type: str, content: dict[str, Any]) -> str | None:
    """블록 type별 content 모양(계약 표). 문제가 있으면 이유를 돌려준다."""
    keys = CONTENT_KEYS.get(block_type)
    if keys is None:
        return f"알 수 없는 블록 type: {block_type}"
    if set(content) != keys:
        return f"{block_type} content는 {sorted(keys)} 키만 가져야 합니다"
    if block_type == "heading" and (not isinstance(content["text"], str) or not content["text"].strip()
                                    or content["level"] not in (1, 2, 3)):
        return "heading은 비어 있지 않은 text와 level 1~3이 필요합니다"
    if block_type == "paragraph" and (not isinstance(content["text"], str) or not content["text"].strip()):
        return "paragraph text가 비어 있습니다"
    if block_type == "list" and (not isinstance(content["items"], list) or not content["items"]
                                 or not all(isinstance(i, str) and i.strip() for i in content["items"])):
        return "list items는 비어 있지 않은 문자열 배열이어야 합니다"
    if block_type == "image" and (not isinstance(content["asset_id"], str) or content["fit"] not in ("contain", "crop")):
        return "image는 asset_id 문자열과 fit(contain/crop)이 필요합니다"
    if block_type == "image_placeholder" and not isinstance(content["description"], str):
        return "image_placeholder description은 문자열이어야 합니다"
    return None


def _index(pages: list[Page]) -> tuple[dict[str, Page], dict[str, tuple[Page, int]]]:
    page_by_id: dict[str, Page] = {}
    block_at: dict[str, tuple[Page, int]] = {}
    for page in pages:
        page_by_id[page.page_id] = page
        for i, block in enumerate(page.blocks):
            block_at[block.block_id] = (page, i)
    return page_by_id, block_at


def _insert_after(items: list, after_id: str | None, id_of, item, index: int, op: str, what: str) -> None:
    if after_id is None:
        items.insert(0, item)
        return
    for i, existing in enumerate(items):
        if id_of(existing) == after_id:
            items.insert(i + 1, item)
            return
    raise OpError(index, op, f"{what} {after_id}이(가) 대상 안에 없습니다", {"after_id": after_id})


def apply_operations(pages: list[Page], ops: list[Operation]) -> list[Page]:
    work = copy.deepcopy(pages)
    for index, op in enumerate(ops):
        page_by_id, block_at = _index(work)
        name = op.op

        if name == "replace_block_content":
            if op.block_id not in block_at:
                raise OpError(index, name, "블록이 없습니다", {"block_id": op.block_id})
            page, i = block_at[op.block_id]
            old = page.blocks[i]
            if (reason := check_content(old.type, op.content)) is not None:
                raise OpError(index, name, reason, {"block_id": op.block_id})
            page.blocks[i] = Block(block_id=old.block_id, type=old.type, content=op.content,
                                   fact_ids=list(old.fact_ids), evidence_refs=list(old.evidence_refs))

        elif name == "insert_block":
            if op.page_id not in page_by_id:
                raise OpError(index, name, "페이지가 없습니다", {"page_id": op.page_id})
            if op.block.block_id in block_at:
                raise OpError(index, name, "이미 있는 block_id입니다", {"block_id": op.block.block_id})
            if (reason := check_content(op.block.type, op.block.content)) is not None:
                raise OpError(index, name, reason, {"block_id": op.block.block_id})
            page = page_by_id[op.page_id]
            if op.after_block_id is not None and (op.after_block_id not in block_at
                                                  or block_at[op.after_block_id][0] is not page):
                raise OpError(index, name, "after_block_id가 그 페이지에 없습니다", {"after_block_id": op.after_block_id})
            _insert_after(page.blocks, op.after_block_id, lambda b: b.block_id, copy.deepcopy(op.block), index, name, "after_block_id")

        elif name == "delete_block":
            if op.block_id not in block_at:
                raise OpError(index, name, "블록이 없습니다", {"block_id": op.block_id})
            page, i = block_at[op.block_id]
            del page.blocks[i]

        elif name == "move_block":
            if op.block_id not in block_at:
                raise OpError(index, name, "블록이 없습니다", {"block_id": op.block_id})
            if op.target_page_id not in page_by_id:
                raise OpError(index, name, "대상 페이지가 없습니다", {"target_page_id": op.target_page_id})
            if op.after_block_id == op.block_id:
                raise OpError(index, name, "자기 자신 뒤로 옮길 수 없습니다", {"block_id": op.block_id})
            target = page_by_id[op.target_page_id]
            if op.after_block_id is not None and (op.after_block_id not in block_at
                                                  or block_at[op.after_block_id][0] is not target):
                raise OpError(index, name, "after_block_id가 대상 페이지에 없습니다", {"after_block_id": op.after_block_id})
            page, i = block_at[op.block_id]
            block = page.blocks.pop(i)
            _insert_after(target.blocks, op.after_block_id, lambda b: b.block_id, block, index, name, "after_block_id")

        elif name == "insert_page":
            if op.page.page_id in page_by_id:
                raise OpError(index, name, "이미 있는 page_id입니다", {"page_id": op.page.page_id})
            for b in op.page.blocks:
                if b.block_id in block_at:
                    raise OpError(index, name, "이미 있는 block_id입니다", {"block_id": b.block_id})
                if (reason := check_content(b.type, b.content)) is not None:
                    raise OpError(index, name, reason, {"block_id": b.block_id})
            if len({b.block_id for b in op.page.blocks}) != len(op.page.blocks):
                raise OpError(index, name, "새 페이지 안에 중복 block_id가 있습니다")
            if op.after_page_id is not None and op.after_page_id not in page_by_id:
                raise OpError(index, name, "after_page_id가 없습니다", {"after_page_id": op.after_page_id})
            _insert_after(work, op.after_page_id, lambda p: p.page_id, copy.deepcopy(op.page), index, name, "after_page_id")

        elif name == "rename_page":
            if op.page_id not in page_by_id:
                raise OpError(index, name, "페이지가 없습니다", {"page_id": op.page_id})
            page_by_id[op.page_id].title = op.title

        elif name == "move_page":
            if op.page_id not in page_by_id:
                raise OpError(index, name, "페이지가 없습니다", {"page_id": op.page_id})
            if op.after_page_id == op.page_id:
                raise OpError(index, name, "자기 자신 뒤로 옮길 수 없습니다", {"page_id": op.page_id})
            if op.after_page_id is not None and op.after_page_id not in page_by_id:
                raise OpError(index, name, "after_page_id가 없습니다", {"after_page_id": op.after_page_id})
            page = page_by_id[op.page_id]
            work.remove(page)
            _insert_after(work, op.after_page_id, lambda p: p.page_id, page, index, name, "after_page_id")

        elif name == "delete_page":
            if op.page_id not in page_by_id:
                raise OpError(index, name, "페이지가 없습니다", {"page_id": op.page_id})
            if len(work) == 1:
                raise OpError(index, name, "마지막 페이지는 지울 수 없습니다", {"page_id": op.page_id})
            work.remove(page_by_id[op.page_id])

        else:  # pydantic이 막지만 방어
            raise OpError(index, str(name), "허용되지 않은 연산입니다")
    return work


def touched_block_ids(ops: list[Operation]) -> set[str]:
    """연산이 직접 바꾸는 기존 블록 ID(삽입되는 새 블록은 제외)."""
    ids: set[str] = set()
    for op in ops:
        if op.op in ("replace_block_content", "delete_block", "move_block"):
            ids.add(op.block_id)
    return ids
