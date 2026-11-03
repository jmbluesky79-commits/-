import html
import json
import os
import re
import threading
import uuid
from datetime import datetime

import streamlit as st

DATA_FILE = "shopping_list.json"
CORRUPT_BACKUP = "shopping_list.corrupt.json"
MAX_NAME_LEN = 50
MIN_QTY, MAX_QTY = 1, 99


@st.cache_resource
def _file_lock() -> threading.Lock:
    """모든 세션(탭)이 공유하는 파일 잠금. 스크립트 재실행에도 유지된다."""
    return threading.Lock()


# ---------- 메시지 ----------
def _flash(kind: str, text: str) -> None:
    """이번(또는 다음) 렌더링에서 보여줄 메시지를 쌓아둔다."""
    st.session_state.setdefault("flash", []).append((kind, text))


# ---------- 입력값 정리 ----------
def _clean_name(name) -> tuple[str, bool]:
    """(정리된 이름, 잘렸는지 여부)."""
    name = str(name or "").strip()
    return name[:MAX_NAME_LEN], len(name) > MAX_NAME_LEN


def _clamp_qty(qty) -> int:
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        qty = MIN_QTY
    return max(MIN_QTY, min(MAX_QTY, qty))


def _normalize(raw) -> dict | None:
    """파일에서 읽은 항목 하나를 검증·보정한다. 쓸 수 없으면 None."""
    if not isinstance(raw, dict):
        return None
    name, _ = _clean_name(raw.get("name"))
    if not name:
        return None
    return {
        "id": str(raw.get("id") or uuid.uuid4().hex[:8]),
        "name": name,
        "quantity": _clamp_qty(raw.get("quantity")),
        "checked": bool(raw.get("checked", False)),
        "created_at": str(raw.get("created_at") or ""),
    }


# ---------- 데이터 함수 ----------
def load_items() -> list:
    """JSON 파일에서 목록을 불러온다. 없으면 빈 리스트.
    손상된 파일은 백업 파일로 옮기고 빈 목록으로 시작한다 (B6).
    항목은 하나씩 검증해 잘못된 필드를 보정한다 (B2·B3)."""
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("list가 아님")
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        os.replace(DATA_FILE, CORRUPT_BACKUP)
        _flash("warning", f"저장 파일이 손상되어 빈 목록으로 시작합니다. "
                          f"원본은 {CORRUPT_BACKUP}에 백업했어요.")
        return []
    except OSError:
        _flash("warning", "저장 파일을 읽을 수 없어 빈 목록으로 표시합니다.")
        return []

    items, seen = [], set()
    for raw in data:
        item = _normalize(raw)
        if item is None:
            continue
        if item["id"] in seen:  # id 중복이면 새 id 부여 (위젯 key 충돌 방지)
            item["id"] = uuid.uuid4().hex[:8]
        seen.add(item["id"])
        items.append(item)
    if len(items) != len(data):
        _flash("warning", f"저장 파일에서 읽을 수 없는 항목 {len(data) - len(items)}개를 건너뛰었어요.")
    if items != data:  # 보정한 내용을 저장해 같은 경고가 반복되지 않게 한다
        save_items(items)
    return items


def save_items(items: list) -> None:
    """목록을 JSON 파일로 저장한다. 임시 파일에 쓴 뒤 교체해 중간에 깨지지 않게 한다."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def _mutate(fn) -> None:
    """파일의 최신 목록을 읽어 fn으로 바꾸고 바로 저장한다 (F-05, B1).
    세션에 들고 있던 옛 목록이 아니라 파일 기준이라 다른 탭의 변경을 덮어쓰지 않는다."""
    with _file_lock():
        items = fn(load_items())
        save_items(items)
    st.session_state.shopping_items = items


# ---------- 기능 함수 ----------
def add_item(name: str, qty: int) -> bool:
    """F-01 아이템 추가. 성공 여부 반환."""
    name, truncated = _clean_name(name)
    if not name:
        _flash("warning", "아이템 이름을 입력해 주세요.")
        return False
    new_item = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "quantity": _clamp_qty(qty),
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _mutate(lambda items: items + [new_item])
    _flash("success", f"'{name}'을(를) 추가했어요.")
    if truncated:
        _flash("info", f"이름이 길어서 앞 {MAX_NAME_LEN}자까지만 저장했어요.")
    return True


def update_item(item_id: str, name: str, qty: int) -> bool:
    """F-02 아이템 수정. 성공 여부 반환."""
    name, truncated = _clean_name(name)
    if not name:
        _flash("warning", "빈 이름으로는 저장할 수 없어요.")
        return False
    found = []

    def apply(items):
        out = []
        for it in items:
            if it["id"] == item_id:
                found.append(True)
                it = {**it, "name": name, "quantity": _clamp_qty(qty)}
            out.append(it)
        return out

    _mutate(apply)
    if not found:
        _flash("warning", "다른 곳에서 이미 삭제된 항목이에요.")
    elif truncated:
        _flash("info", f"이름이 길어서 앞 {MAX_NAME_LEN}자까지만 저장했어요.")
    return True


def delete_item(item_id: str) -> None:
    """F-03 아이템 삭제."""
    _mutate(lambda items: [it for it in items if it["id"] != item_id])
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None


def toggle_item(item_id: str, checked: bool | None = None) -> None:
    """F-04 체크 상태 변경. checked를 주면 그 값으로, 없으면 반전.
    체크박스 값을 그대로 쓰면 다른 탭과 동시에 눌러도 화면과 저장 상태가 어긋나지 않는다."""
    _mutate(lambda items: [
        {**it, "checked": (not it["checked"]) if checked is None else checked}
        if it["id"] == item_id else it
        for it in items
    ])


def clear_checked() -> None:
    """F-03 체크된 항목 일괄 삭제."""
    removed = []

    def apply(items):
        remaining = [it for it in items if not it["checked"]]
        removed.append(len(items) - len(remaining))
        return remaining

    _mutate(apply)
    if st.session_state.editing_id not in {it["id"] for it in st.session_state.shopping_items}:
        st.session_state.editing_id = None
    _flash("success", f"체크된 항목 {removed[0]}개를 삭제했어요.")


# ---------- 콜백 (위젯 → 기능 함수) ----------
def on_add() -> None:
    add_item(st.session_state.get("new_name", ""), st.session_state.get("new_qty", 1))


def on_toggle(item_id: str) -> None:
    toggle_item(item_id, st.session_state.get(f"chk_{item_id}"))


def on_start_edit(item_id: str) -> None:
    st.session_state.editing_id = item_id


def on_save_edit(item_id: str) -> None:
    ok = update_item(
        item_id,
        st.session_state.get(f"edit_name_{item_id}", ""),
        st.session_state.get(f"edit_qty_{item_id}", 1),
    )
    if ok:
        st.session_state.editing_id = None


def on_cancel_edit() -> None:
    st.session_state.editing_id = None


def md_escape(text: str) -> str:
    """사용자 입력이 마크다운으로 해석되지 않도록 이스케이프."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>:$])", r"\\\1", text)


# ---------- 상태 초기화 ----------
st.set_page_config(page_title="쇼핑리스트", page_icon="🛒")

# 매 실행마다 파일에서 최신 목록을 읽는다 → 다른 탭의 변경이 바로 반영된다 (B1)
with _file_lock():
    st.session_state.shopping_items = load_items()
if "editing_id" not in st.session_state:
    st.session_state.editing_id = None
if st.session_state.editing_id not in {it["id"] for it in st.session_state.shopping_items}:
    st.session_state.editing_id = None  # 다른 탭에서 삭제된 항목을 편집 중이었다면 해제



# ---------- 스타일 ----------
CUSTOM_CSS = """
<style>
.block-container { max-width: 720px; padding-top: 2.5rem; }

/* 목록 행: 모바일에서도 한 줄 유지 */
.st-key-item_list [data-testid="stHorizontalBlock"],
.st-key-add_area [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.5rem;
}
.st-key-item_list [data-testid="stColumn"],
.st-key-add_area [data-testid="stColumn"] {
    min-width: 0 !important;
}
.st-key-item_list [data-testid="stHorizontalBlock"] {
    padding: 0.15rem 0.5rem;
    border-radius: 0.5rem;
    transition: background-color 0.15s;
}
.st-key-item_list [data-testid="stHorizontalBlock"]:hover {
    background-color: rgba(128, 128, 128, 0.08);
}
.st-key-item_list [data-testid="stElementContainer"] { margin-bottom: 0; }
.st-key-item_list .stMarkdown p { margin: 0; }
.st-key-item_list [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.st-key-item_list .stMarkdown { display: flex; align-items: center; min-height: 2rem; }
.st-key-add_area .stFormSubmitButton p { white-space: nowrap; }
@media (max-width: 640px) {
    .st-key-add_area .stFormSubmitButton button { padding-left: 0.4rem; padding-right: 0.4rem; }
    .st-key-add_area [data-testid="stHorizontalBlock"] { gap: 0.4rem; }
}

/* 아이템 이름 */
.item-name { overflow-wrap: anywhere; font-size: 1.02rem; }
.item-qty {
    font-size: 0.8rem;
    color: rgba(128, 128, 128, 0.95);
    margin-left: 0.4rem;
    white-space: nowrap;
}
.item-checked .item-label {
    text-decoration: line-through;
    color: rgba(128, 128, 128, 0.8);
}
.item-checked .item-qty { text-decoration: line-through; }

/* 아이콘 버튼 가운데 정렬 */
.st-key-item_list .stButton button { padding: 0.15rem 0.4rem; min-height: 2rem; }
.st-key-item_list .stButton { display: flex; justify-content: center; }

.summary { color: rgba(128, 128, 128, 0.95); font-size: 0.95rem; margin-bottom: 0.25rem; }
</style>
"""


def item_label_html(item: dict) -> str:
    """이름은 HTML·마크다운 모두 이스케이프해서 사용자 입력이 그대로 보이게 한다."""
    name = md_escape(html.escape(item["name"], quote=False))
    cls = "item-name item-checked" if item["checked"] else "item-name"
    return (f'<span class="{cls}"><span class="item-label">{name}</span>'
            f'<span class="item-qty">x{item["quantity"]}</span></span>')


# ---------- 화면 ----------
st.html(CUSTOM_CSS)
st.title("🛒 쇼핑리스트")

items = st.session_state.shopping_items
total = len(items)
done = sum(1 for it in items if it["checked"])
ratio = done / total if total else 0.0

# [요약] 진행률 (F-04)
st.markdown(f'<div class="summary">전체 {total}개 중 {done}개 구매 완료</div>',
            unsafe_allow_html=True)
st.progress(ratio, text=f"{round(ratio * 100)}%")
if total and done == total:
    st.success("🎉 모두 구매 완료!")

# [메시지]
for kind, text in st.session_state.pop("flash", []):
    if kind == "success":
        st.toast(text, icon="✅")
    elif kind == "info":
        st.info(text, icon="ℹ️")
    else:
        st.warning(text, icon="⚠️")

st.divider()

# [추가 영역] (F-01)
with st.container(key="add_area"):
    with st.form("add_form", clear_on_submit=True, border=False):
        c_name, c_qty, c_btn = st.columns([4.6, 1.7, 1.5], vertical_alignment="bottom")
        c_name.text_input("아이템 이름", key="new_name", placeholder="예: 우유")
        c_qty.number_input("수량", key="new_qty", min_value=MIN_QTY, max_value=MAX_QTY,
                           value=1, step=1, help=f"{MIN_QTY}~{MAX_QTY} 사이로 입력하세요")
        c_btn.form_submit_button("추가", icon=":material/add:", on_click=on_add, type="primary",
                                 use_container_width=True)

st.divider()

# [목록 영역] (F-02, F-03, F-04) — 미체크 항목 먼저 표시 (저장 순서는 그대로)
if not items:
    st.info("아직 아이템이 없어요. 위에서 추가해 보세요!", icon="📝")
else:
    with st.container(key="item_list"):
        for item in sorted(items, key=lambda it: it["checked"]):
            iid = item["id"]
            if st.session_state.editing_id == iid:
                c_chk, c_name, c_qty, c_save, c_cancel = st.columns(
                    [0.6, 3.6, 1.6, 0.9, 0.9], vertical_alignment="center")
                c_chk.checkbox("구매 완료", value=item["checked"], key=f"chk_{iid}",
                               on_change=on_toggle, args=(iid,),
                               label_visibility="collapsed")
                c_name.text_input("이름", value=item["name"], key=f"edit_name_{iid}",
                                  label_visibility="collapsed")
                c_qty.number_input("수량", value=item["quantity"], key=f"edit_qty_{iid}",
                                   min_value=MIN_QTY, max_value=MAX_QTY, step=1,
                                   label_visibility="collapsed")
                c_save.button("💾", key=f"save_{iid}", on_click=on_save_edit,
                              args=(iid,), help="저장", type="tertiary")
                c_cancel.button("↩️", key=f"cancel_{iid}", on_click=on_cancel_edit,
                                help="취소", type="tertiary")
            else:
                c_chk, c_name, c_edit, c_del = st.columns(
                    [0.6, 5, 0.9, 0.9], vertical_alignment="center")
                c_chk.checkbox("구매 완료", value=item["checked"], key=f"chk_{iid}",
                               on_change=on_toggle, args=(iid,),
                               label_visibility="collapsed")
                c_name.markdown(item_label_html(item), unsafe_allow_html=True)
                c_edit.button("✏️", key=f"edit_{iid}", on_click=on_start_edit,
                              args=(iid,), help="수정", type="tertiary")
                c_del.button("🗑️", key=f"del_{iid}", on_click=delete_item,
                             args=(iid,), help="삭제", type="tertiary")

    # [하단 버튼 영역] (F-03 일괄 삭제)
    st.divider()
    st.button("🧹 체크된 항목 모두 삭제", on_click=clear_checked, disabled=done == 0)
