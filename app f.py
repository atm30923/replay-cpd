import base64
import json
import os
import re
import time
import uuid
from datetime import datetime
from html import escape
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from PIL import Image
from streamlit_drawable_canvas import st_canvas


BASE = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(BASE, "photos")
MEMORY_DIR = os.path.join(BASE, "memories")
HANDWRITING_DIR = os.path.join(BASE, "handwriting")
MUSIC_DIR = os.path.join(BASE, "music")
os.makedirs(PHOTO_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(HANDWRITING_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)
load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")

st.set_page_config(page_title="Re:Play", layout="centered", initial_sidebar_state="collapsed")


def media_reference_available(path):
    if not path:
        return False
    if str(path).startswith(("http://", "https://", "data:")):
        return True
    return os.path.exists(path)


def local_photo_path_for(memory_id):
    return os.path.join(PHOTO_DIR, f"{memory_id}.jpg")


class MemoryStore:
    """Storage boundary for memory metadata.

    Local JSON is the default implementation. A Firebase implementation can
    keep the same public methods while storing records in Realtime Database
    and media references as Firebase Storage URLs.
    """

    def save(self, memory):
        raise NotImplementedError

    def load_all(self):
        raise NotImplementedError

    def delete(self, memory_id):
        raise NotImplementedError

    def record_path(self, memory_id):
        return None


class LocalMemoryStore(MemoryStore):
    def __init__(self, memory_dir):
        self.memory_dir = memory_dir

    def record_path(self, memory_id):
        return os.path.join(self.memory_dir, f"{memory_id}.json")

    def save(self, memory):
        memory_id = memory.get("id")
        if not memory_id:
            return
        with open(self.record_path(memory_id), "w", encoding="utf-8") as file:
            json.dump(memory, file, ensure_ascii=False, indent=2)

    def load_all(self):
        memories = []
        for name in os.listdir(self.memory_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.memory_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as file:
                    memory = json.load(file)
            except Exception:
                continue
            memory["id"] = memory.get("id") or os.path.splitext(name)[0]
            memories.append(memory)
        return memories

    def delete(self, memory_id):
        path = self.record_path(memory_id)
        if os.path.exists(path):
            os.remove(path)


class FirebaseMemoryStore(MemoryStore):
    def __init__(self):
        raise NotImplementedError(
            "Firebase storage is not configured yet. Implement this class with "
            "Realtime Database records and Firebase Storage media URLs."
        )


def create_memory_store():
    store_kind = os.getenv("MEMORY_STORE", "local").strip().lower()
    if store_kind == "firebase":
        return FirebaseMemoryStore()
    return LocalMemoryStore(MEMORY_DIR)


memory_store = create_memory_store()

DEFAULTS = {
    "page": "splash",
    "photo_path": None,
    "memory_id": None,
    "selected_track": None,
    "spotify_tracks": [],
    "spotify_query": "",
    "music_search_results": [],
    "music_last_search_query": "",
    "music_show_more": False,
    "play_memory": None,
    "selected_memory_id": None,
    "album_category": "",
    "album_selected_memory_id": None,
    "upload_signature": None,
    "memory_text": "",
    "handwriting_path": None,
    "handwriting_json": None,
    "handwriting_image_data": None,
    "write_mode": "handwriting",
    "current_question_index": 0,
    "question_answers": {},
    "write_completed": False,
    "pen_color": "#000000",
    "pen_width": 3,
    "stroke_width": 3,
    "drawing_tool": "pen",
    "canvas_revision": 0,
    "handwriting_redo_stack": [],
    "analysis_done": False,
    "analysis_label": "사진 속 순간",
    "suggested_categories": [],
    "music_query": "",
    "keywords": ["추억사진", "인물사진", "옛날 분위기"],
    "question": "이 사진을 찍던 날은 어떤 날이었나요?",
    "questions": [
        "이 사진을 찍던 날은 어떤 날이었나요?",
        "사진 속 사람들과 어떤 추억이 있나요?",
        "이 장면을 보면 가장 먼저 떠오르는 감정은 무엇인가요?",
    ],
    "selected_category": "",
    "selected_categories": [],
    "home_tab": "album",
    "home_playing_memory_id": None,
    "playback_memories": [],
    "playback_index": 0,
    "playback_started_at": 0.0,
    "playback_caption_index": 0,
    "playback_caption_started_at": 0.0,
    "playback_song_index": 0,
    "playback_song_started_at": 0.0,
    "editing_memory_id": None,
    "edit_mode": "create",
    "music_return_page": "home",
    "scan_upload_notice": "",
    "nfc_uid": "",
    "nfc_label": "RE:01",
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


def go(page):
    st.session_state["page"] = page
    st.rerun()


def normalize_nfc_label(value="RE01"):
    """Convert NFC UID/text into the visible category name.

    Examples:
    - RE01 -> RE:01
    - RE:01 -> RE:01
    - RE02 -> RE:02
    """
    raw = str(value or "RE01").strip().upper().replace(" ", "")
    if raw.startswith("RE:"):
        suffix = raw.split(":", 1)[1] or "01"
        return f"RE:{suffix.zfill(2) if suffix.isdigit() else suffix}"
    if raw.startswith("RE") and len(raw) > 2:
        suffix = raw[2:] or "01"
        return f"RE:{suffix.zfill(2) if suffix.isdigit() else suffix}"
    return raw or "RE:01"


def current_nfc_category():
    return normalize_nfc_label(st.session_state.get("nfc_label") or st.session_state.get("nfc_uid") or "RE01")


def set_current_nfc_category(label):
    label = normalize_nfc_label(label)
    st.session_state.nfc_label = label
    st.session_state.album_category = label
    st.session_state.selected_category = label
    st.session_state.selected_categories = [label]
    return label


def memory_nfc_category(memory):
    """Return the NFC album/category this memory belongs to.

    Old memories that do not have NFC metadata are treated as RE:01 so they
    still appear in the first cassette during demos.
    """
    memory = memory or {}
    explicit = memory.get("nfc_label") or memory.get("nfc_category") or memory.get("nfc_uid")
    if explicit:
        return normalize_nfc_label(explicit)

    for category in memory_categories(memory):
        if str(category).strip().upper().replace(" ", "").startswith("RE"):
            return normalize_nfc_label(category)

    return "RE:01"


def memory_belongs_to_current_nfc(memory, label=None):
    label = normalize_nfc_label(label or current_nfc_category())
    return memory_nfc_category(memory) == label


def memories_for_current_nfc(source_memories=None, label=None):
    label = normalize_nfc_label(label or current_nfc_category())
    source = source_memories if source_memories is not None else load_memories()
    return [memory for memory in source if memory_belongs_to_current_nfc(memory, label)]


def handle_nfc_detected(uid="RE01"):
    label = normalize_nfc_label(uid)
    st.session_state["nfc_uid"] = str(uid or label)
    set_current_nfc_category(label)
    st.session_state["selected_memory_id"] = None
    st.session_state["album_selected_memory_id"] = None
    st.session_state["play_memory"] = None
    st.session_state["home_tab"] = "album"
    st.session_state["page"] = "nfc_recognized"


def set_previous_page():
    current = st.session_state.get("page", "home")
    if current == "write":
        preserve_write_inputs()
    if current == "scan_upload":
        st.session_state.page = "home"
    elif current in ("scan_running", "analyzing", "scan_done"):
        st.session_state.page = "scan_upload"
    elif current == "write":
        st.session_state.page = "scan_done"
    elif current == "memory_done":
        st.session_state.page = "write"
    elif current == "music":
        if st.session_state.get("edit_mode") == "music_edit":
            finish_existing_music_edit()
        else:
            st.session_state.page = "write"
    elif current in ("category_loading", "category_edit", "album_done", "nfc_scan", "nfc_done", "video", "player", "player_legacy"):
        # 현재 버전에서는 카테고리/앨범완료/재생 화면을 사용하지 않음
        st.session_state.page = "home"
    elif current == "memory_edit":
        st.session_state.page = "home"
    else:
        st.session_state.page = "home"


def go_back():
    set_previous_page()
    st.rerun()


def reset_flow():
    st.session_state.photo_path = None
    st.session_state.memory_id = None
    st.session_state.selected_track = None
    st.session_state.spotify_tracks = []
    st.session_state.spotify_query = ""
    st.session_state.music_search_results = []
    st.session_state.music_last_search_query = ""
    st.session_state.music_show_more = False
    st.session_state.play_memory = None
    st.session_state.album_category = ""
    st.session_state.album_selected_memory_id = None
    st.session_state.upload_signature = None
    st.session_state.memory_text = ""
    st.session_state.handwriting_path = None
    st.session_state.handwriting_json = None
    st.session_state.handwriting_image_data = None
    st.session_state.write_mode = "handwriting"
    st.session_state.current_question_index = 0
    st.session_state.question_answers = {}
    for index in range(3):
        st.session_state.pop(f"question_answer_{index}", None)
    st.session_state.write_completed = False
    st.session_state.pen_color = "#000000"
    st.session_state.pen_width = 3
    st.session_state.stroke_width = 3
    st.session_state.drawing_tool = "pen"
    st.session_state.canvas_revision = 0
    st.session_state.handwriting_redo_stack = []
    st.session_state.analysis_done = False
    st.session_state.analysis_label = "사진 속 순간"
    st.session_state.suggested_categories = []
    st.session_state.music_query = ""
    st.session_state.keywords = ["추억사진", "인물사진", "옛날 분위기"]
    st.session_state.question = "이 사진을 찍던 날은 어떤 날이었나요?"
    st.session_state.questions = [
        "이 사진을 찍던 날은 어떤 날이었나요?",
        "사진 속 사람들과 어떤 추억이 있나요?",
        "이 장면을 보면 가장 먼저 떠오르는 감정은 무엇인가요?",
    ]
    current_category = current_nfc_category()
    st.session_state.album_category = current_category
    st.session_state.selected_category = current_category
    st.session_state.selected_categories = [current_category]
    st.session_state.home_playing_memory_id = None
    st.session_state.playback_memories = []
    st.session_state.playback_index = 0
    st.session_state.playback_started_at = 0.0
    st.session_state.playback_caption_index = 0
    st.session_state.playback_caption_started_at = 0.0
    st.session_state.playback_song_index = 0
    st.session_state.playback_song_started_at = 0.0
    st.session_state.editing_memory_id = None
    st.session_state.edit_mode = "create"
    st.session_state.music_return_page = "home"
    st.session_state.scan_upload_notice = ""


def reset_scan_inputs():
    st.session_state.photo_path = None
    st.session_state.memory_id = None
    st.session_state.upload_signature = None
    st.session_state.selected_track = None
    st.session_state.spotify_tracks = []
    st.session_state.spotify_query = ""
    st.session_state.music_search_results = []
    st.session_state.music_last_search_query = ""
    st.session_state.music_show_more = False
    st.session_state.memory_text = ""
    st.session_state.analysis_done = False
    st.session_state.analysis_label = "사진 속 순간"
    st.session_state.suggested_categories = []
    st.session_state.music_query = ""
    st.session_state.keywords = ["추억사진", "인물사진", "옛날 분위기"]
    st.session_state.question = "이 사진을 찍던 날은 어떤 날이었나요?"
    st.session_state.questions = [
        "이 사진을 찍던 날은 어떤 날이었나요?",
        "사진 속 사람들과 어떤 추억이 있나요?",
        "이 장면을 보면 가장 먼저 떠오르는 감정은 무엇인가요?",
    ]
    st.session_state.handwriting_path = None
    st.session_state.handwriting_json = None
    st.session_state.handwriting_image_data = None
    st.session_state.current_question_index = 0
    st.session_state.question_answers = {}
    for index in range(3):
        st.session_state.pop(f"question_answer_{index}", None)
    st.session_state.write_completed = False
    st.session_state.pen_color = "#000000"
    st.session_state.pen_width = 3
    st.session_state.stroke_width = 3
    st.session_state.drawing_tool = "pen"
    st.session_state.canvas_revision = 0
    st.session_state.handwriting_redo_stack = []
    st.session_state.scan_upload_notice = ""


def html(markup):
    st.markdown(markup.strip(), unsafe_allow_html=True)


REPLAY_LOGO_SVG = """
<svg viewBox="0 0 213 96" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="RE:PLAY logo">
  <g fill="#000">
    <rect x="32" y="10" width="14" height="43"/>
    <rect x="52" y="10" width="14" height="43"/>
    <circle cx="88" cy="31.5" r="21.5"/>
    <polygon points="108,10 146,31.5 108,53"/>
    <polygon points="144,10 159,10 139,53 124,53"/>
    <rect x="161" y="10" width="14" height="43"/>
  </g>
  <rect x="70" y="26.5" width="58" height="10" fill="#fff"/>
  <polygon points="108,10 146,31.5 108,53" fill="#000"/>
  <polygon points="144,10 159,10 139,53 124,53" fill="#000"/>
  <text x="32" y="73" fill="#000" font-family="Arial Black, Arial, sans-serif" font-size="16" font-weight="900" letter-spacing="7">RE : PLAY</text>
</svg>
"""
REPLAY_LOGO_SRC = f"data:image/svg+xml;charset=utf-8,{quote(REPLAY_LOGO_SVG.strip())}"
SPLASH_LOGO_HTML = f'<img class="replay-logo-img splash-logo-img" src="{REPLAY_LOGO_SRC}" alt="RE:PLAY logo">'
HOME_LOGO_HTML = f'<img class="replay-logo-img home-brand-logo" src="{REPLAY_LOGO_SRC}" alt="RE:PLAY logo">'
FLOW_LOGO_HTML = f'<img class="replay-logo-img flow-logo-img" src="{REPLAY_LOGO_SRC}" alt="RE:PLAY logo">'


def render_global_back_button():
    if st.session_state.get("page") in ("splash", "home", "nfc_intro", "nfc_recognized"):
        return
    html("""
<style>
a.back,
a.music-back,
.st-key-write_back_button {
    display:none !important;
}
</style>
""")


def image_to_base64(path):
    with open(path, "rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")


def image_src(path):
    if not path:
        return ""
    if str(path).startswith(("http://", "https://", "data:")):
        return path
    if not os.path.exists(path):
        return ""
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{image_to_base64(path)}"


def save_photo(file):
    memory_id = str(uuid.uuid4())[:8]
    path = local_photo_path_for(memory_id)
    image = Image.open(file).convert("RGB")
    image.save(path, quality=92)
    st.session_state.memory_id = memory_id
    st.session_state.photo_path = path


def restore_photo(memory_id):
    if not memory_id:
        return
    path = local_photo_path_for(memory_id)
    if os.path.exists(path):
        st.session_state.memory_id = memory_id
        st.session_state.photo_path = path
    handwriting = handwriting_path_for(memory_id)
    if handwriting:
        st.session_state.handwriting_path = handwriting


def save_handwriting(image_data):
    if image_data is None:
        return None
    if not st.session_state.memory_id:
        st.session_state.memory_id = str(uuid.uuid4())[:8]
    path = os.path.join(HANDWRITING_DIR, f"{st.session_state.memory_id}.png")
    image = Image.fromarray(image_data.astype("uint8"), mode="RGBA")
    paper = Image.new("RGBA", image.size, "WHITE")
    paper.alpha_composite(image)
    paper.convert("RGB").save(path, "PNG")
    st.session_state.handwriting_path = path
    return path


def canvas_has_ink(image_data):
    if image_data is None:
        return False
    return bool((image_data[:, :, :3] < 245).any())


def remember_canvas_state(canvas_key):
    canvas_state = st.session_state.get(canvas_key)
    if not isinstance(canvas_state, dict):
        return
    raw_canvas = canvas_state.get("raw") or canvas_state.get("json_data")
    if isinstance(raw_canvas, str):
        try:
            raw_canvas = json.loads(raw_canvas)
        except Exception:
            raw_canvas = None
    if not isinstance(raw_canvas, dict):
        return
    objects = raw_canvas.get("objects")
    existing = st.session_state.get("handwriting_json")
    existing_objects = existing.get("objects") if isinstance(existing, dict) else []
    if isinstance(objects, list) and (objects or not existing_objects):
        st.session_state.handwriting_json = raw_canvas
        st.session_state.handwriting_json_key = canvas_key


def set_pen_color(hex_color, canvas_key=None):
    if canvas_key:
        remember_canvas_state(canvas_key)
    st.session_state.pen_color = hex_color
    st.session_state.drawing_tool = "pen"


def set_drawing_tool(tool, canvas_key=None):
    if canvas_key:
        remember_canvas_state(canvas_key)
    st.session_state.drawing_tool = tool


def bump_canvas_revision():
    st.session_state.canvas_revision = int(st.session_state.get("canvas_revision", 0) or 0) + 1


def clear_handwriting_canvas():
    st.session_state.handwriting_json = None
    st.session_state.handwriting_json_key = None
    st.session_state.handwriting_image_data = None
    st.session_state.handwriting_redo_stack = []
    st.session_state.force_canvas_redraw = True
    bump_canvas_revision()


def write_canvas_key(question_index=None):
    index = current_question_index() if question_index is None else question_index
    revision = int(st.session_state.get("canvas_revision", 0) or 0)
    memory_id = st.session_state.get("memory_id") or "new"
    return f"handwriting_canvas_{memory_id}_q{index}_r{revision}"


def move_write_question(next_index):
    save_current_question_answer()
    st.session_state.current_question_index = max(0, min(2, next_index))
    clear_handwriting_canvas()


def undo_handwriting(canvas_key=None):
    if canvas_key:
        remember_canvas_state(canvas_key)
    drawing = st.session_state.get("handwriting_json")
    if not isinstance(drawing, dict):
        return
    objects = drawing.get("objects")
    if not isinstance(objects, list) or not objects:
        return
    redo_stack = st.session_state.get("handwriting_redo_stack")
    if not isinstance(redo_stack, list):
        redo_stack = []
    redo_stack.append(objects.pop())
    st.session_state.handwriting_redo_stack = redo_stack
    st.session_state.handwriting_json = drawing
    st.session_state.force_canvas_redraw = True
    bump_canvas_revision()


def redo_handwriting():
    drawing = st.session_state.get("handwriting_json")
    if not isinstance(drawing, dict):
        drawing = {"version": "5.2.4", "objects": []}
    objects = drawing.setdefault("objects", [])
    redo_stack = st.session_state.get("handwriting_redo_stack")
    if not isinstance(redo_stack, list) or not redo_stack:
        return
    objects.append(redo_stack.pop())
    st.session_state.handwriting_redo_stack = redo_stack
    st.session_state.handwriting_json = drawing
    st.session_state.force_canvas_redraw = True
    bump_canvas_revision()


def current_canvas_key():
    return f"handwriting_canvas_{st.session_state.get('memory_id') or 'new'}"


def preserve_write_inputs():
    text_value = st.session_state.get("memory_text_area_record")
    voice_value = st.session_state.get("voice_transcript_preview")
    if text_value is not None:
        st.session_state.memory_text = text_value
    if voice_value is not None:
        st.session_state.memory_text = voice_value
    remember_canvas_state(current_canvas_key())


def switch_write_mode(mode):
    preserve_write_inputs()
    save_current_question_answer()
    st.session_state.write_mode = mode


def ensure_selected_categories():
    categories = st.session_state.get("selected_categories")
    if not isinstance(categories, list):
        current = st.session_state.get("selected_category", "")
        categories = [current] if current else []

    # NFC cassette name is the main album/category. Keep it attached to every memory.
    nfc_category = current_nfc_category() if "current_nfc_category" in globals() else ""
    if nfc_category and nfc_category not in categories:
        categories.insert(0, nfc_category)

    st.session_state.selected_categories = categories
    st.session_state.selected_category = ", ".join(categories) if categories else ""
    return categories


def sync_selected_category():
    categories = ensure_selected_categories()
    st.session_state.selected_category = ", ".join(categories) if categories else ""


def toggle_category(option):
    categories = ensure_selected_categories()
    if option in categories:
        categories.remove(option)
    else:
        categories.append(option)
    st.session_state.selected_categories = categories
    sync_selected_category()


def add_direct_category():
    value = st.session_state.get("category_direct_input", "").strip()
    if not value:
        return
    categories = ensure_selected_categories()
    if value not in categories:
        categories.append(value)
    st.session_state.selected_categories = categories
    st.session_state.category_direct_text = ""
    st.session_state.category_direct_input = ""
    sync_selected_category()


def handwriting_path_for(memory_id):
    if not memory_id:
        return None
    path = os.path.join(HANDWRITING_DIR, f"{memory_id}.png")
    return path if os.path.exists(path) else None


def fallback_photo_analysis():
    questions = [
        "이 사진을 찍던 날은 어떤 날이었나요?",
        "사진 속 사람들과 어떤 추억이 있나요?",
        "이 장면을 보면 가장 먼저 떠오르는 감정은 무엇인가요?",
    ]
    return {
        "keywords": ["추억사진", "인물사진", "옛날 분위기"],
        "analysis_label": "사진 속 순간",
        "question": questions[0],
        "questions": questions,
        "categories": ["오래된 추억", "따뜻한 순간", "기억하고 싶은 날"],
        "music_query": "korean nostalgic family ballad",
    }


def analyze_photo_with_ai(path):
    if not OPENAI_API_KEY or not path or not media_reference_available(path):
        return fallback_photo_analysis()

    prompt = """
업로드된 사진을 보고 Re:Play 서비스에 쓸 한국어 분석 정보를 만들어줘.

규칙:
- 사진에 실제로 보이는 단서만 기반으로 해.
- 시대, 연도, 계절은 확실하지 않으면 절대 단정하지 마.
- 키워드는 각각 2~8글자 정도로 짧게.
- scene_label은 화면 칩에 들어갈 짧은 상황 설명이야. 예: "생일을 축하하는 가족", "바닷가 여행", "교복 입은 친구들".
- 질문은 사진 속 인물, 장소, 상황, 분위기 같은 시각 단서에 맞춰 서로 다르게 만들어.
- 질문은 사용자가 사진 속 기억을 떠올릴 수 있는 자연스러운 한 문장.
- categories는 이 사진이 들어갈 앨범/플레이리스트 이름 후보 3~5개야. 사진 속 상황에 맞게 구체적으로 만들어.
- music_query는 Spotify 검색에 쓸 짧은 검색어야. 사진 분위기와 상황에 맞는 장르/무드/상황을 섞어줘. 매번 "memories"처럼 똑같은 말만 쓰지 마.
- 반드시 JSON만 반환해. 형식:
{"keywords":["키워드1","키워드2","키워드3"],"scene_label":"짧은상황설명","questions":["질문1","질문2","질문3"],"categories":["카테고리1","카테고리2","카테고리3"],"music_query":"spotify search query"}
"""
    body = {
        "model": OPENAI_VISION_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_src(path)}},
                ],
            }
        ],
        "max_tokens": 650,
        "temperature": 0.2,
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=25,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        fallback = fallback_photo_analysis()
        keywords = [str(item).strip() for item in data.get("keywords", []) if str(item).strip()]
        raw_questions = data.get("questions") or [data.get("question", "")]
        questions = [str(item).strip() for item in raw_questions if str(item).strip()]
        categories = [str(item).strip() for item in data.get("categories", []) if str(item).strip()]
        analysis_label = str(data.get("scene_label") or data.get("analysis_label") or "").strip()
        music_query = str(data.get("music_query") or "").strip()
        if len(keywords) < 3 or not questions:
            return fallback_photo_analysis()
        if len(questions) < 3:
            questions = (questions + fallback["questions"])[:3]
        if not analysis_label:
            analysis_label = keywords[0] if keywords else fallback["analysis_label"]
        if not categories:
            categories = fallback["categories"]
        if not music_query:
            music_query = " ".join(categories[:2] + keywords[:2]) or fallback["music_query"]
        return {
            "keywords": keywords[:3],
            "analysis_label": analysis_label[:28],
            "question": questions[0],
            "questions": questions[:3],
            "categories": categories[:5],
            "music_query": music_query,
        }
    except Exception:
        return fallback_photo_analysis()


def transcribe_audio(audio_file):
    if not OPENAI_API_KEY or audio_file is None:
        return ""
    try:
        audio_file.seek(0)
        files = {
            "file": (audio_file.name or "voice.webm", audio_file.read(), audio_file.type or "audio/webm"),
        }
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            data={"model": OPENAI_TRANSCRIBE_MODEL, "language": "ko"},
            files=files,
            timeout=60,
        )
        response.raise_for_status()
        return response.json().get("text", "").strip()
    except Exception:
        return ""


def update_photo_analysis():
    if st.session_state.analysis_done:
        return
    result = analyze_photo_with_ai(st.session_state.photo_path)
    st.session_state.keywords = result["keywords"]
    st.session_state.analysis_label = result.get("analysis_label", "사진 속 순간")
    st.session_state.question = result["question"]
    st.session_state.questions = result["questions"]
    st.session_state.suggested_categories = result.get("categories", [])
    st.session_state.music_query = result.get("music_query", "")
    current_category = current_nfc_category()
    st.session_state.album_category = current_category
    st.session_state.selected_category = current_category
    st.session_state.selected_categories = [current_category]
    st.session_state.analysis_done = True


def ensure_question_answers():
    answers = st.session_state.get("question_answers")
    if not isinstance(answers, dict):
        answers = {}
        st.session_state.question_answers = answers
    return answers


def current_question_index():
    questions = st.session_state.get("questions") or [st.session_state.get("question", "")]
    count = max(1, min(3, len(questions)))
    index = int(st.session_state.get("current_question_index", 0) or 0)
    index = max(0, min(index, count - 1))
    st.session_state.current_question_index = index
    return index


def sync_answer_to_memory_text():
    answers = ensure_question_answers()
    questions = st.session_state.get("questions") or []
    lines = []
    for index, _question in enumerate(questions[:3]):
        answer = str(answers.get(str(index), "")).strip()
        if answer:
            # 홈 카드/재생 자막에는 질문 문구 없이 사용자가 기록한 답변만 저장한다.
            lines.append(answer)
    st.session_state.memory_text = "\n\n".join(lines).strip()
    return st.session_state.memory_text


def save_current_question_answer():
    index = current_question_index()
    answers = ensure_question_answers()
    candidate_keys = [
        f"question_answer_{index}",
        f"question_answer_text_{index}",
        f"voice_transcript_{index}",
    ]
    value = None
    for key in candidate_keys:
        candidate = st.session_state.get(key)
        if candidate is not None:
            value = candidate
            break
    if value is not None:
        answers[str(index)] = str(value).strip()
    else:
        answers.setdefault(str(index), "")
    st.session_state.question_answers = answers
    sync_answer_to_memory_text()


def set_write_question_index(index, canvas_key=None):
    if canvas_key:
        remember_canvas_state(canvas_key)
    save_current_question_answer()
    questions = st.session_state.get("questions") or fallback_photo_analysis()["questions"]
    count = max(1, min(3, len(questions)))
    st.session_state.current_question_index = max(0, min(count - 1, int(index or 0)))


def cycle_pen_width(canvas_key=None):
    if canvas_key:
        remember_canvas_state(canvas_key)
    current_width = int(st.session_state.get("stroke_width", st.session_state.get("pen_width", 3)) or 3)
    next_width = 1 if current_width >= 7 else current_width + 1
    st.session_state.pen_width = next_width
    st.session_state.stroke_width = next_width


def update_existing_memory_from_write(memory_id):
    memory = load_memory_record(memory_id)
    if not memory:
        return False

    handwriting = (
        st.session_state.get("handwriting_path")
        or memory.get("handwriting")
        or handwriting_path_for(memory_id)
    )
    memory["questions"] = st.session_state.get("questions") or memory.get("questions") or []
    memory["question"] = (
        st.session_state.get("question")
        or (memory["questions"][0] if memory.get("questions") else memory.get("question", ""))
    )
    memory["question_answers"] = ensure_question_answers()
    sync_answer_to_memory_text()
    note_text = st.session_state.get("memory_text", "").strip()
    if not note_text:
        note_text = memory_question_answer_text(memory, include_questions=False)
    memory["note"] = note_text
    if handwriting:
        memory["handwriting"] = handwriting

    save_memory_record(memory)
    st.session_state.play_memory = memory
    st.session_state.selected_memory_id = memory_id
    st.session_state.memory_id = memory_id
    return True


def finish_write_record(canvas_key=None):
    if canvas_key:
        remember_canvas_state(canvas_key)
    if st.session_state.get("write_completed") and st.session_state.get("play_memory"):
        st.session_state.page = "memory_done"
        return

    save_current_question_answer()
    handwriting_image = st.session_state.get("handwriting_image_data")
    if handwriting_image is not None and canvas_has_ink(handwriting_image):
        save_handwriting(handwriting_image)

    edit_memory_id = st.session_state.get("editing_memory_id")
    if edit_memory_id and is_existing_edit_flow():
        update_existing_memory_from_write(edit_memory_id)
        st.session_state.editing_memory_id = None
        st.session_state.edit_mode = "create"
        st.session_state.write_completed = False
        st.session_state.page = "home"
        return

    save_memory()
    st.session_state.write_completed = True
    st.session_state.page = "memory_done"


def fallback_memory_summary(memory=None):
    memory = memory or {}
    keywords = memory.get("keywords") or st.session_state.get("keywords", [])
    questions = memory.get("questions") or st.session_state.get("questions", [])
    category = memory.get("category") or st.session_state.get("selected_category", "추억")
    analysis_label = memory.get("analysis_label") or st.session_state.get("analysis_label") or category
    note = " ".join(str(memory.get("note") or st.session_state.get("memory_text", "")).split())
    lead = ", ".join(keywords[:2]) if keywords else category
    prompt = questions[0] if questions else st.session_state.get("question", "")

    if note:
        note_preview = note if len(note) <= 54 else f"{note[:54]}..."
        return f"{analysis_label} 속에 '{note_preview}'라는 기억이 담겨 있어요. {lead}의 분위기가 함께 느껴져요."
    return f"{analysis_label} 장면이에요. {lead}의 단서가 보여요. {prompt}"


def summarize_memory_with_ai(memory):
    fallback = fallback_memory_summary(memory)
    if not OPENAI_API_KEY:
        return fallback

    note = str(memory.get("note") or "").strip()
    keywords = ", ".join(memory.get("keywords") or [])
    questions = " / ".join(memory.get("questions") or [])
    categories = ", ".join(memory_categories(memory))
    prompt = f"""
사진과 사용자가 직접 기록한 글을 함께 보고 Re:Play 앨범의 AI 요약을 한국어로 작성해줘.

사진 AI 분석:
- 장면: {memory.get("analysis_label") or ""}
- 키워드: {keywords}
- 회상 질문: {questions}
- 카테고리: {categories}

사용자 기록:
{note or "아직 직접 기록한 문장은 없음"}

규칙:
- 사용자가 적은 기록을 가장 중요하게 반영해.
- 사진에 보이지 않는 사실은 단정하지 마.
- 따뜻하지만 과장하지 말고, 앨범 카드에 들어갈 1~2문장으로 써.
- 120자 안팎으로 짧게.
- 요약 문장만 반환해.
"""
    content = [{"type": "text", "text": prompt}]
    photo = memory.get("photo")
    if media_reference_available(photo):
        content.append({"type": "image_url", "image_url": {"url": image_src(photo)}})

    body = {
        "model": OPENAI_VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 180,
        "temperature": 0.35,
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=20,
        )
        response.raise_for_status()
        summary = response.json()["choices"][0]["message"]["content"].strip()
        return summary or fallback
    except Exception:
        return fallback


def save_memory(include_summary=True):
    if not st.session_state.memory_id:
        st.session_state.memory_id = str(uuid.uuid4())[:8]
    handwriting = st.session_state.handwriting_path or handwriting_path_for(st.session_state.memory_id)
    nfc_category = current_nfc_category()
    categories = ensure_selected_categories()
    if nfc_category and nfc_category not in categories:
        categories.insert(0, nfc_category)
    st.session_state.selected_category = nfc_category

    # 질문 3개에 입력한 답변이 note에 비어 저장되는 문제 방지
    sync_answer_to_memory_text()
    note_text = st.session_state.get("memory_text", "").strip()
    if not note_text:
        temp_memory_for_note = {
            "questions": st.session_state.get("questions") or [],
            "question_answers": ensure_question_answers(),
        }
        note_text = memory_question_answer_text(temp_memory_for_note, include_questions=False)

    data = {
        "id": st.session_state.memory_id,
        "nfc_uid": st.session_state.get("nfc_uid", ""),
        "nfc_label": nfc_category,
        "nfc_category": nfc_category,
        "photo": st.session_state.photo_path,
        "keywords": st.session_state.keywords,
        "analysis_label": st.session_state.analysis_label,
        "question": st.session_state.question,
        "questions": st.session_state.questions,
        "suggested_categories": st.session_state.suggested_categories,
        "music_query": st.session_state.music_query,
        "note": note_text,
        "question_answers": ensure_question_answers(),
        "handwriting": handwriting,
        "selected_track": st.session_state.selected_track,
        "category": nfc_category,
        "categories": categories,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    existing_summary = (st.session_state.get("play_memory") or {}).get("summary")
    if include_summary:
        data["summary"] = summarize_memory_with_ai(data)
    elif existing_summary:
        data["summary"] = existing_summary
    memory_store.save(data)
    st.session_state.play_memory = data
    st.session_state.selected_memory_id = data["id"]


def load_memories():
    memories = []
    for memory in memory_store.load_all():
        memory_id = memory.get("id")
        if not memory_id:
            continue
        memory["id"] = memory_id
        handwriting = memory.get("handwriting") or handwriting_path_for(memory_id)
        if handwriting:
            memory["handwriting"] = handwriting
        if not media_reference_available(memory.get("photo")):
            local_photo = local_photo_path_for(memory_id)
            if os.path.exists(local_photo):
                memory["photo"] = local_photo
        if not memory.get("nfc_label"):
            memory["nfc_label"] = memory_nfc_category(memory) if "memory_nfc_category" in globals() else "RE:01"
            memory["nfc_category"] = memory["nfc_label"]
        if media_reference_available(memory.get("photo")):
            memories.append(memory)
    # 처음 기록한 사진이 맨 앞에 오도록 오래된 순서로 정렬
    memories.sort(key=lambda item: item.get("time", ""))
    return memories


def memory_categories(memory):
    values = []
    raw_categories = memory.get("categories")
    if isinstance(raw_categories, list):
        values.extend(raw_categories)
    elif isinstance(raw_categories, str):
        values.extend(raw_categories.split(","))
    raw_category = memory.get("category")
    if raw_category:
        values.extend(str(raw_category).split(","))

    categories = []
    for value in values:
        category = str(value).strip()
        if category and category not in categories:
            categories.append(category)
    return categories


def memory_title(memory):
    title = str(memory.get("title") or "").strip()
    if title:
        return title
    categories = memory_categories(memory)
    if categories:
        return categories[0]
    category = str(memory.get("category") or "").strip()
    if category:
        return category.split(",")[0].strip()
    return "이름 없는 추억"


def memory_question_answer_text(memory, include_questions=False):
    """Return only user-recorded answers from the 3 question flow.

    질문 문구(Q1. ...)는 홈 카드/자막/미리보기에는 보이지 않게 하고,
    사용자가 실제로 기록한 답변 문장만 반환한다.
    """
    memory = memory or {}
    answers = memory.get("question_answers") or memory.get("answers")

    lines = []
    if isinstance(answers, dict):
        def answer_sort_key(item):
            key, _ = item
            try:
                return int(key)
            except Exception:
                return 999

        for _key, answer in sorted(answers.items(), key=answer_sort_key):
            answer_text = str(answer or "").strip()
            if answer_text:
                lines.append(answer_text)

    elif isinstance(answers, list):
        for answer in answers:
            answer_text = str(answer or "").strip()
            if answer_text:
                lines.append(answer_text)

    return "\\n\\n".join(lines).strip()


def strip_question_labels_from_note(note):
    """Remove Q1 question lines from old saved notes and keep recorded answers."""
    raw = str(note or "").strip()
    if not raw:
        return ""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    kept = []
    skip_next_question_line = False

    for line in lines:
        # Drop lines like "Q1. 이 사진을 찍던 날은 어떤 날이었나요?"
        if re.match(r"^Q\s*\d+\s*[\.)]\s*", line):
            continue
        kept.append(line)

    cleaned = "\n".join(kept).strip()
    return cleaned


def memory_record_text(memory, include_questions=False):
    """Prefer note, but remove Q question labels; fall back to recorded answers."""
    note = strip_question_labels_from_note((memory or {}).get("note") or "")
    if note:
        return note
    return memory_question_answer_text(memory, include_questions=False)


def memory_note_preview(memory, limit=32):
    note = " ".join(memory_record_text(memory, include_questions=False).split())
    if not note:
        return "아직 기록된 문장이 없어요."
    return note if len(note) <= limit else f"{note[:limit]}..."


def memory_json_path(memory_id):
    return memory_store.record_path(memory_id)


def save_memory_record(memory):
    memory_id = memory.get("id")
    if not memory_id:
        return
    memory_store.save(memory)


def parse_category_text(text):
    raw = str(text or "").strip()
    if not raw:
        return []
    if "#" in raw:
        parts = raw.split("#")
    else:
        parts = raw.split(",")
    categories = []
    for part in parts:
        category = part.strip().strip(",")
        if category and category not in categories:
            categories.append(category)
    return categories


def group_memories_by_category(source_memories=None):
    grouped = {}
    for memory in source_memories or load_memories():
        categories = memory_categories(memory) or ["이름 없는 앨범"]
        for category in categories:
            grouped.setdefault(category, []).append(memory)
    return grouped


def shared_family_members():
    return [
        {"name": "나", "status": "지금 듣는 중", "active": True},
        {"name": "엄마", "status": "2시간 전", "active": False},
        {"name": "아빠", "status": "5시간 전", "active": False},
        {"name": "동생", "status": "1일 전", "active": False},
    ]


def memory_primary_category(memory):
    categories = memory_categories(memory or {})
    if categories:
        return categories[0]
    category = str((memory or {}).get("category") or "").strip()
    return category.split(",")[0].strip() if category else ""


def family_memory_title(memory, fallback="오늘의 추억"):
    title = str((memory or {}).get("title") or "").strip()
    if title:
        return title
    category = memory_primary_category(memory)
    return category or fallback


def family_track_text(memory):
    track = (memory or {}).get("selected_track") or {}
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    if title and artist:
        return f"{title} - {artist}"
    if title:
        return title
    return "가족과 함께 들을 추억"


def family_photo_html(memory, class_name=""):
    src = image_src(photo_path_for_memory(memory or {}))
    if src:
        return f'<img class="{class_name}" src="{escape(src, quote=True)}" alt="">'
    return '<div class="family-photo-placeholder">사진 없음</div>'


def family_playlist_groups(source_memories=None):
    memories = source_memories if source_memories is not None else load_memories()
    grouped = group_memories_by_category(memories)
    if not grouped and memories:
        grouped = {"우리 가족": memories}
    return sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)


def resolve_category_from_nfc_uid(uid):
    # NFC UID/text maps directly to an album/category label in this prototype.
    return normalize_nfc_label(uid or current_nfc_category())


def open_category_album_from_uid(uid):
    category = resolve_category_from_nfc_uid(uid)
    if not category:
        return False
    return open_category_album(category)


def selected_category_album_memories(category=None):
    picked_category = (
        category
        or st.session_state.get("selected_category")
        or st.session_state.get("album_category")
        or ""
    )
    if isinstance(picked_category, str) and "," in picked_category:
        picked_category = picked_category.split(",")[0].strip()
    return album_memories_for_category(picked_category)


def open_memory_from_home(memory_id):
    picked = next((memory for memory in load_memories() if memory.get("id") == memory_id), None)
    if not picked:
        return
    categories = memory_categories(picked)
    open_category_album(categories[0] if categories else "", memory_id)


def open_memory_edit(memory_id):
    if not memory_id:
        return
    select_album_memory(memory_id)
    st.session_state.editing_memory_id = memory_id
    st.session_state.edit_mode = "memory_edit"
    st.session_state.page = "memory_edit"


def load_memory_record(memory_id):
    if not memory_id:
        return None
    return next((memory for memory in load_memories() if memory.get("id") == memory_id), None)


def open_music_edit(memory_id):
    picked = load_memory_record(memory_id)
    if not picked:
        reset_flow()
        st.session_state.page = "music"
        return
    select_album_memory(memory_id)
    st.session_state.editing_memory_id = memory_id
    st.session_state.edit_mode = "music_edit"
    st.session_state.music_return_page = st.session_state.get("page") or "home"
    st.session_state.selected_track = picked.get("selected_track")
    st.session_state.page = "music"


def update_memory_track(memory_id, track):
    picked = load_memory_record(memory_id)
    if not picked:
        return False
    picked["selected_track"] = track
    save_memory_record(picked)
    st.session_state.play_memory = picked
    st.session_state.selected_memory_id = memory_id
    st.session_state.memory_id = memory_id
    st.session_state.selected_track = track
    return True


def current_music_memory_id():
    candidates = [
        st.session_state.get("selected_memory_id"),
        st.session_state.get("memory_id"),
        (st.session_state.get("play_memory") or {}).get("id"),
    ]
    for memory_id in candidates:
        if memory_id:
            return memory_id
    return None


def save_selected_track_to_current_memory():
    selected_track = st.session_state.get("selected_track")
    if not selected_track:
        st.session_state.music_notice = "음악을 먼저 선택해주세요."
        return False

    memory_id = current_music_memory_id()
    if not memory_id:
        st.session_state.music_notice = "음악을 저장할 추억을 찾지 못했어요."
        return False

    if not update_memory_track(memory_id, selected_track):
        st.session_state.music_notice = "음악을 저장할 추억을 찾지 못했어요."
        return False

    st.session_state.music_notice = ""
    return True


def finish_existing_music_edit():
    st.session_state.editing_memory_id = None
    st.session_state.edit_mode = "create"
    st.session_state.music_show_more = False
    st.session_state.home_tab = "music"
    st.session_state.page = "home"


def is_existing_edit_flow():
    return bool(st.session_state.get("editing_memory_id")) and st.session_state.get("edit_mode") in (
        "memory_edit",
        "music_edit",
    )


def clear_memory_track(memory_id):
    picked = load_memory_record(memory_id)
    if not picked:
        return
    picked["selected_track"] = None
    save_memory_record(picked)
    if st.session_state.get("home_playing_memory_id") == memory_id:
        st.session_state.home_playing_memory_id = None
    if st.session_state.get("selected_memory_id") == memory_id:
        st.session_state.selected_track = None
        st.session_state.play_memory = picked


def album_category_from_state(memory=None):
    explicit = str(st.session_state.get("album_category") or "").strip()
    if explicit:
        return explicit
    nfc_category = current_nfc_category()
    if nfc_category:
        return nfc_category
    selected_categories = st.session_state.get("selected_categories")
    if isinstance(selected_categories, list) and selected_categories:
        return str(selected_categories[0]).strip()
    selected = str(st.session_state.get("selected_category") or "").strip()
    if selected:
        return selected.split(",")[0].strip()
    categories = memory_categories(memory or {})
    return categories[0] if categories else ""


def album_memories_for_category(category=None, source_memories=None, strict=False):
    all_memories = source_memories if source_memories is not None else load_memories()
    if category is None:
        category = album_category_from_state(st.session_state.get("play_memory") or {})
    category = str(category or "").strip()
    if category:
        filtered = [memory for memory in all_memories if category in memory_categories(memory)]
        if filtered or strict:
            return filtered
    selected_id = st.session_state.get("album_selected_memory_id") or st.session_state.get("selected_memory_id")
    if selected_id:
        picked = [memory for memory in all_memories if memory.get("id") == selected_id]
        if picked:
            return picked
    return all_memories[:1]


def photo_path_for_memory(memory):
    candidates = [memory.get("photo")]
    memory_id = memory.get("id")
    if memory_id:
        candidates.append(local_photo_path_for(memory_id))
    for path in candidates:
        if media_reference_available(path):
            return path
    return None


def select_album_memory(memory_id):
    picked = next((memory for memory in load_memories() if memory.get("id") == memory_id), None)
    if not picked:
        return
    set_current_nfc_category(memory_nfc_category(picked))
    st.session_state.album_selected_memory_id = memory_id
    st.session_state.selected_memory_id = memory_id
    st.session_state.play_memory = picked
    st.session_state.memory_id = memory_id
    st.session_state.photo_path = photo_path_for_memory(picked)
    st.session_state.memory_text = picked.get("note", st.session_state.get("memory_text", ""))
    st.session_state.question_answers = picked.get("question_answers") or st.session_state.get("question_answers", {})
    st.session_state.handwriting_path = picked.get("handwriting") or handwriting_path_for(memory_id)
    st.session_state.analysis_label = picked.get("analysis_label") or st.session_state.get("analysis_label", "사진 속 순간")
    st.session_state.suggested_categories = picked.get("suggested_categories") or picked.get("categories") or []
    st.session_state.music_query = picked.get("music_query") or st.session_state.get("music_query", "")
    st.session_state.selected_categories = memory_categories(picked)
    st.session_state.selected_category = ", ".join(st.session_state.selected_categories)
    if not st.session_state.get("album_category") and st.session_state.selected_categories:
        st.session_state.album_category = st.session_state.selected_categories[0]


def open_category_album(category=None, memory_id=None):
    category = str(category or current_nfc_category()).strip()
    album_memories = album_memories_for_category(category, strict=bool(category))
    if category:
        st.session_state.album_category = category
    if memory_id:
        select_album_memory(memory_id)
    elif album_memories:
        select_album_memory(album_memories[0].get("id"))
        if category:
            st.session_state.album_category = category
    # 현재 버전에서는 앨범/재생 화면을 사용하지 않으므로 선택 상태만 저장하고 홈으로 이동
    st.session_state.page = "home"
    return bool(memory_id or album_memories)


def active_memory():
    memory = st.session_state.get("play_memory") or {}
    memory_id = memory.get("id") or st.session_state.get("selected_memory_id") or st.session_state.get("memory_id")
    all_memories = load_memories()
    if memory_id:
        for item in all_memories:
            if item.get("id") == memory_id:
                st.session_state.play_memory = item
                st.session_state.selected_memory_id = memory_id
                st.session_state.memory_text = item.get("note", st.session_state.get("memory_text", ""))
                st.session_state.question_answers = item.get("question_answers") or st.session_state.get("question_answers", {})
                st.session_state.analysis_label = item.get("analysis_label") or st.session_state.get("analysis_label", "사진 속 순간")
                st.session_state.suggested_categories = item.get("suggested_categories") or item.get("categories") or []
                st.session_state.music_query = item.get("music_query") or st.session_state.get("music_query", "")
                st.session_state.selected_category = item.get("category") or st.session_state.get("selected_category", "")
                st.session_state.selected_categories = item.get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
                handwriting = item.get("handwriting") or handwriting_path_for(memory_id)
                if handwriting:
                    st.session_state.handwriting_path = handwriting
                return item
    if (not memory.get("photo") or not media_reference_available(memory.get("photo"))) and all_memories:
        item = all_memories[0]
        st.session_state.play_memory = item
        st.session_state.selected_memory_id = item.get("id")
        st.session_state.memory_id = item.get("id") or st.session_state.get("memory_id")
        st.session_state.photo_path = item.get("photo") or st.session_state.get("photo_path")
        st.session_state.memory_text = item.get("note", st.session_state.get("memory_text", ""))
        st.session_state.question_answers = item.get("question_answers") or st.session_state.get("question_answers", {})
        st.session_state.analysis_label = item.get("analysis_label") or st.session_state.get("analysis_label", "사진 속 순간")
        st.session_state.suggested_categories = item.get("suggested_categories") or item.get("categories") or []
        st.session_state.music_query = item.get("music_query") or st.session_state.get("music_query", "")
        st.session_state.selected_category = item.get("category") or st.session_state.get("selected_category", "")
        st.session_state.selected_categories = item.get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
        handwriting = item.get("handwriting") or handwriting_path_for(item.get("id"))
        if handwriting:
            st.session_state.handwriting_path = handwriting
        return item
    return memory


def active_photo_path(memory=None):
    memory = memory or {}
    candidates = [
        memory.get("photo"),
        st.session_state.get("photo_path"),
    ]
    for memory_id in (memory.get("id"), st.session_state.get("memory_id"), st.session_state.get("selected_memory_id")):
        if memory_id:
            candidates.append(local_photo_path_for(memory_id))
    for path in candidates:
        if media_reference_available(path):
            st.session_state.photo_path = path
            return path
    return None


def active_handwriting_path(memory=None):
    memory = memory or {}
    candidates = [
        memory.get("handwriting"),
        st.session_state.get("handwriting_path"),
        handwriting_path_for(memory.get("id")),
        handwriting_path_for(st.session_state.get("memory_id")),
        handwriting_path_for(st.session_state.get("selected_memory_id")),
    ]
    for path in candidates:
        if media_reference_available(path):
            st.session_state.handwriting_path = path
            return path
    return None


def delete_memory(memory_id):
    if not memory_id:
        return
    memory_store.delete(memory_id)
    paths = [
        local_photo_path_for(memory_id),
        os.path.join(HANDWRITING_DIR, f"{memory_id}.png"),
    ]
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
    if st.session_state.selected_memory_id == memory_id:
        st.session_state.selected_memory_id = None
    if st.session_state.memory_id == memory_id:
        reset_flow()


def get_spotify_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]



LOCAL_MUSIC_TRACKS = [
    {
        "title": "마이웨이",
        "artist": "윤태규",
        "image": "",
        "preview_url": "",
        "file": os.path.join("music", "my_way_yoon_taegyu.mp3"),
        "source": "local",
        "search_terms": "마이웨이 윤태규 my way",
    },
    {
        "title": "하숙생",
        "artist": "최희준",
        "image": "",
        "preview_url": "",
        "file": os.path.join("music", "life_is_traveler.mp3"),
        "source": "local",
        "search_terms": "인생은 나그네길 어디서 왔다가 어디로 가는가 하숙생 최희준",
    },
]


def local_music_tracks(query=""):
    """Return local mp3 tracks bundled in the project.

    Put files in:
    music/my_way_yoon_taegyu.mp3
    music/life_is_traveler.mp3
    """
    query_text = str(query or "").strip().lower()
    tracks = []
    for track in LOCAL_MUSIC_TRACKS:
        title = str(track.get("title") or "")
        artist = str(track.get("artist") or "")
        file_path = str(track.get("file") or "")
        item = dict(track)
        resolved_file = resolve_audio_file_path(file_path, item)
        if resolved_file:
            item["file"] = resolved_file

        search_terms = str(track.get("search_terms") or "")
        haystack = f"{title} {artist} {file_path} {search_terms}".lower()
        if query_text and query_text not in haystack:
            continue
        tracks.append(item)
    return tracks


def merge_unique_tracks(*track_groups):
    merged = []
    seen = set()
    for group in track_groups:
        for track in group or []:
            normalized_title = str(track.get("title") or "").strip().lower()
            normalized_artist = str(track.get("artist") or "").strip().lower()
            key = (normalized_title, normalized_artist)
            if not normalized_title or key in seen:
                continue
            seen.add(key)
            merged.append(track)
    return merged


def spotify_recommendations(query="korean old pop memories"):
    query_text = str(query or "").lower()
    local_tracks_for_query = local_music_tracks(query)
    if not local_tracks_for_query and query_text in ("", "korean old pop memories"):
        local_tracks_for_query = local_music_tracks("")
    if any(word in query_text for word in ("birthday", "생일", "party", "축하", "cake")):
        fallback = [
            {"title": "Happy Birthday To You", "artist": "Birthday Memory", "image": "", "preview_url": ""},
            {"title": "Celebration", "artist": "Family Day", "image": "", "preview_url": ""},
            {"title": "Sweet Cake", "artist": "Re:Play", "image": "", "preview_url": ""},
            {"title": "우리의 생일", "artist": "Warm Vinyl", "image": "", "preview_url": ""},
        ]
    elif any(word in query_text for word in ("travel", "trip", "beach", "sea", "여행", "바다", "여름")):
        fallback = [
            {"title": "Summer Trip", "artist": "Memory Road", "image": "", "preview_url": ""},
            {"title": "바람 부는 날", "artist": "Re:Play", "image": "", "preview_url": ""},
            {"title": "Ocean Drive", "artist": "Soft Vinyl", "image": "", "preview_url": ""},
            {"title": "첫 여행", "artist": "Family Tape", "image": "", "preview_url": ""},
        ]
    elif any(word in query_text for word in ("school", "graduation", "friend", "학교", "졸업", "친구")):
        fallback = [
            {"title": "Graduation Day", "artist": "Youth Radio", "image": "", "preview_url": ""},
            {"title": "친구와 함께", "artist": "Re:Play", "image": "", "preview_url": ""},
            {"title": "Old Class", "artist": "Memory Tape", "image": "", "preview_url": ""},
            {"title": "Young Again", "artist": "Soft Vinyl", "image": "", "preview_url": ""},
        ]
    else:
        fallback = [
            {"title": "Family Warmth", "artist": "Memory Tape", "image": "", "preview_url": ""},
            {"title": "오래된 사진", "artist": "Re:Play", "image": "", "preview_url": ""},
            {"title": "Warm Ballad", "artist": "Vintage Radio", "image": "", "preview_url": ""},
            {"title": "Last Scene", "artist": "Soft Vinyl", "image": "", "preview_url": ""},
        ]
    try:
        token = get_spotify_token()
        if not token:
            return merge_unique_tracks(local_tracks_for_query, fallback)
        response = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": query, "type": "track", "market": "KR", "limit": 10},
            timeout=10,
        )
        response.raise_for_status()
        tracks = []
        for item in response.json().get("tracks", {}).get("items", []):
            images = item.get("album", {}).get("images", [])
            tracks.append(
                {
                    "title": item.get("name", ""),
                    "artist": ", ".join(artist["name"] for artist in item.get("artists", [])),
                    "image": images[0]["url"] if images else "",
                    "preview_url": item.get("preview_url") or "",
                }
            )
        return merge_unique_tracks(local_tracks_for_query, tracks or fallback)
    except Exception:
        return merge_unique_tracks(local_tracks_for_query, fallback)


DEFAULT_TRACK_LYRICS = [
    "오늘의 기억이 천천히 흐르고",
    "그날의 바람이 다시 불어와",
    "우리의 시간이 노래가 되어",
    "마음속에 오래 남아요",
]


def normalize_track(track):
    track = track or {}
    lyrics = track.get("lyrics")
    if not isinstance(lyrics, list) or not lyrics:
        lyrics = DEFAULT_TRACK_LYRICS
    return {
        "title": str(track.get("title") or "노래 제목").strip(),
        "artist": str(track.get("artist") or "가수").strip(),
        "image": str(track.get("image") or "").strip(),
        "preview_url": str(track.get("preview_url") or "").strip(),
        "file": str(track.get("file") or "").strip(),
        "source": str(track.get("source") or "").strip(),
        "search_terms": str(track.get("search_terms") or "").strip(),
        "lyrics": [str(line) for line in lyrics if str(line).strip()],
    }






def normalize_filename_key(value):
    return re.sub(r"[^a-z0-9가-힣]+", "", str(value or "").lower())


def list_music_files():
    try:
        return [
            name for name in os.listdir(MUSIC_DIR)
            if os.path.isfile(os.path.join(MUSIC_DIR, name))
        ]
    except Exception:
        return []


def find_music_file_by_hint(*hints):
    files = list_music_files()
    if not files:
        return ""

    audio_exts = (".mp3", ".m4a", ".mp4", ".aac", ".wav", ".ogg", ".webm")
    files = [name for name in files if name.lower().endswith(audio_exts)]
    if not files:
        return ""

    hint_values = [str(hint or "").strip() for hint in hints if str(hint or "").strip()]
    hint_basenames = [os.path.basename(hint) for hint in hint_values]
    hint_keys = [normalize_filename_key(os.path.splitext(hint)[0]) for hint in hint_basenames + hint_values]

    for hint in hint_basenames:
        for name in files:
            if name == hint:
                return os.path.join(MUSIC_DIR, name)

    lower_to_name = {name.lower(): name for name in files}
    for hint in hint_basenames:
        picked = lower_to_name.get(hint.lower())
        if picked:
            return os.path.join(MUSIC_DIR, picked)

    for name in files:
        name_key = normalize_filename_key(os.path.splitext(name)[0])
        for hint_key in hint_keys:
            if hint_key and (hint_key in name_key or name_key in hint_key):
                return os.path.join(MUSIC_DIR, name)

    return ""


def expected_music_debug_text():
    files = list_music_files()
    if not files:
        return "music 폴더가 비어 있거나 서버에 music 파일이 없습니다."
    return "music 폴더 파일: " + ", ".join(files[:8])


def resolve_audio_file_path(file_value, track=None):
    """Resolve local music file path safely."""
    file_value = str(file_value or "").strip()
    track = track or {}

    if file_value.startswith(("http://", "https://", "data:")):
        return file_value

    if file_value:
        normalized = file_value.replace("/", os.sep).replace("\\", os.sep)
        candidates = [
            normalized,
            os.path.join(BASE, normalized),
            os.path.join(MUSIC_DIR, os.path.basename(normalized)),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate

    return find_music_file_by_hint(
        file_value,
        track.get("title"),
        track.get("artist"),
        track.get("search_terms"),
    )


def sniff_audio_mime(path):
    """Detect MIME from file header, not only extension."""
    path = str(path or "")
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as file:
            head = file.read(32)
    except Exception:
        head = b""

    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if b"ftyp" in head[:16]:
        return "audio/mp4"
    if head.startswith(b"RIFF") and b"WAVE" in head[:16]:
        return "audio/wav"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"

    if ext == ".mp3":
        return "audio/mpeg"
    if ext in (".m4a", ".mp4", ".aac"):
        return "audio/mp4"
    if ext == ".wav":
        return "audio/wav"
    if ext == ".ogg":
        return "audio/ogg"
    if ext == ".webm":
        return "audio/webm"
    return "audio/mpeg"


def audio_src_for_track(track):
    """Return a browser-playable source for a local music file or preview URL."""
    track = track or {}
    local_file = resolve_audio_file_path(track.get("file"), track)
    if local_file and os.path.exists(local_file):
        try:
            with open(local_file, "rb") as audio_file:
                encoded = base64.b64encode(audio_file.read()).decode("utf-8")
            if encoded:
                return f"data:{sniff_audio_mime(local_file)};base64,{encoded}"
        except Exception:
            return ""

    preview = str(track.get("preview_url") or "").strip()
    if preview.startswith(("http://", "https://", "data:")):
        return preview
    return ""


def render_track_audio_html(track, autoplay=False, compact=False):
    """Return HTML audio markup; use inside existing custom UI."""
    src = audio_src_for_track(track)
    if not src:
        st.session_state.music_notice = expected_music_debug_text()
        return ""
    autoplay_attr = " autoplay" if autoplay else ""
    compact_class = " compact" if compact else ""
    return f"""
<div class="native-audio{compact_class}">
  <audio controls{autoplay_attr} preload="auto" src="{escape(src, quote=True)}"></audio>
</div>
"""


def render_track_audio(track, autoplay=False, compact=False):
    markup = render_track_audio_html(track, autoplay=autoplay, compact=compact)
    if markup:
        html(markup)
        return True
    return False


def get_lyrics_from_lrclib(title, artist):
    """Fetch real lyrics from LRCLIB. Returns lyric lines or [] if unavailable."""
    title = str(title or "").strip()
    artist = str(artist or "").strip()
    if not title:
        return []
    try:
        response = requests.get(
            "https://lrclib.net/api/search",
            params={"track_name": title, "artist_name": artist},
            timeout=8,
        )
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list) or not results:
            return []

        picked = results[0]
        lower_title = title.lower()
        lower_artist = artist.lower().split(",")[0].strip()
        for item in results:
            item_title = str(item.get("trackName") or "").lower()
            item_artist = str(item.get("artistName") or "").lower()
            if lower_title and lower_title in item_title and (not lower_artist or lower_artist in item_artist):
                picked = item
                break

        lyrics_text = picked.get("syncedLyrics") or picked.get("plainLyrics") or ""
        if not lyrics_text:
            return []

        lines = []
        for line in lyrics_text.splitlines():
            line = str(line).strip()
            if not line:
                continue
            while line.startswith("[") and "]" in line:
                line = line.split("]", 1)[-1].strip()
            if line and line not in lines:
                lines.append(line)
        return lines[:10]
    except Exception:
        return []


def filtered_music_tracks(tracks, query):
    normalized = [normalize_track(track) for track in tracks or []]
    search = str(query or "").strip().lower()
    if not search:
        return normalized
    return [
        track
        for track in normalized
        if search in track.get("title", "").lower()
        or search in track.get("artist", "").lower()
        or search in track.get("search_terms", "").lower()
    ]


def photo_music_query(memory=None):
    memory = memory or {}
    explicit_query = str(memory.get("music_query") or st.session_state.get("music_query") or "").strip()
    if explicit_query:
        return explicit_query

    categories = (
        memory.get("suggested_categories")
        or memory_categories(memory)
        or st.session_state.get("suggested_categories")
        or st.session_state.get("selected_categories")
        or []
    )
    keywords = memory.get("keywords") or st.session_state.get("keywords", [])
    label = str(memory.get("analysis_label") or st.session_state.get("analysis_label") or "").strip()
    scene_text = " ".join([label] + [str(item) for item in categories + keywords])

    if any(word in scene_text for word in ("생일", "파티", "축하", "케이크")):
        return "birthday family korean cheerful pop"
    if any(word in scene_text for word in ("여행", "바다", "여름", "휴가", "나들이")):
        return "travel summer korean acoustic pop"
    if any(word in scene_text for word in ("학교", "졸업", "친구", "교복")):
        return "graduation friendship korean pop"
    if any(word in scene_text for word in ("결혼", "예식", "약속")):
        return "wedding korean romantic ballad"
    if any(word in scene_text for word in ("가족", "부모", "아이", "아기")):
        return "family warm korean ballad"
    if scene_text.strip():
        return f"{scene_text} korean nostalgic song"
    return "korean nostalgic family ballad"


def category_candidates():
    keywords = [word for word in st.session_state.get("keywords", []) if word]
    question_text = " ".join(st.session_state.get("questions", []))
    scene_text = " ".join(keywords + [question_text])
    labels = []
    for label in st.session_state.get("suggested_categories", []) or []:
        cleaned_label = str(label).strip()
        if cleaned_label:
            labels.append(cleaned_label)
    rules = [
        (("가족", "부모", "형제", "자매"), "가족의 추억"),
        (("여름", "바다", "휴가", "물놀이"), "우리의 여름"),
        (("학교", "교복", "졸업", "운동장", "학창"), "학창시절"),
        (("여행", "관광", "소풍", "나들이"), "첫 가족 여행"),
        (("생일", "파티", "잔치"), "특별한 날"),
        (("결혼", "예식"), "소중한 약속"),
        (("친구", "동창"), "친구와 함께"),
        (("집", "마당", "동네"), "우리 동네"),
    ]
    for needles, label in rules:
        if any(needle in scene_text for needle in needles):
            labels.append(label)
    for word in keywords:
        cleaned = str(word).strip().replace("사진", "").replace("분위기", "")
        cleaned = cleaned.strip()
        if cleaned and len(cleaned) >= 2:
            labels.append(cleaned)
    labels.extend(["오래된 추억", "따뜻한 순간", "기억하고 싶은 날"])
    unique = []
    for label in labels:
        if label and label not in unique:
            unique.append(label)
    return unique[:5]


def memory_summary_text(memory=None):
    memory = memory or {}
    summary = str(memory.get("summary") or "").strip()
    if summary:
        return summary
    return fallback_memory_summary(memory)


def tile_html(memory=None, selected=False):
    if memory:
        cls = "tile selected" if selected else "tile"
        return f'<a class="{cls}" href="?action=select&memory={memory["id"]}" target="_self"><img src="{image_src(memory["photo"])}"></a>'
    return '<div class="tile"><span>▧</span></div>'


def photo_markup(class_name="photo-fill"):
    src = image_src(st.session_state.photo_path)
    if src:
        return f'<img class="{class_name}" src="{src}">'
    return '<span>▧</span>'


memories = load_memories()

action = st.query_params.get("action")
memory_id = st.query_params.get("memory")
note_text = st.query_params.get("note")
mode = st.query_params.get("mode")
tab = st.query_params.get("tab")
category_param = st.query_params.get("category")
tool_param = st.query_params.get("tool")
color_param = st.query_params.get("color")
if memory_id:
    restore_photo(memory_id)
if action:
    if action == "home":
        st.session_state.editing_memory_id = None
        st.session_state.edit_mode = "create"
        st.session_state.page = "home"
    elif action == "nfc_intro":
        st.session_state.page = "nfc_intro"
    elif action == "nfc_detected":
        handle_nfc_detected(st.query_params.get("uid") or "RE01")
    elif action == "nfc_recognized":
        st.session_state.page = "nfc_recognized"
    elif action == "family_home":
        st.session_state.page = "family_home"
    elif action == "home_tab":
        if tab in ("album", "music"):
            st.session_state.home_tab = tab
        st.session_state.page = "home"
    elif action == "family_music":
        st.session_state.home_tab = "music"
        st.session_state.page = "home"
    elif action == "family_play" and memory_id:
        open_memory_from_home(memory_id)
    elif action == "family_category":
        if category_param:
            open_category_album(category_param)
        else:
            st.session_state.page = "family_home"
    elif action == "record":
        active_nfc = current_nfc_category()
        reset_flow()
        set_current_nfc_category(active_nfc)
        st.session_state.page = "scan_upload"
    elif action == "select" and memory_id:
        picked = next((m for m in memories if m.get("id") == memory_id), None)
        if picked:
            st.session_state.selected_memory_id = memory_id
            set_current_nfc_category(memory_nfc_category(picked))
            st.session_state.play_memory = picked
            st.session_state.memory_text = picked.get("note", "")
            st.session_state.question_answers = picked.get("question_answers") or {}
            st.session_state.handwriting_path = picked.get("handwriting") or handwriting_path_for(memory_id)
            st.session_state.keywords = picked.get("keywords") or st.session_state.keywords
            st.session_state.analysis_label = picked.get("analysis_label") or st.session_state.get("analysis_label", "사진 속 순간")
            st.session_state.question = picked.get("question") or st.session_state.question
            st.session_state.questions = picked.get("questions") or [st.session_state.question]
            st.session_state.suggested_categories = picked.get("suggested_categories") or picked.get("categories") or []
            st.session_state.music_query = picked.get("music_query") or st.session_state.get("music_query", "")
            st.session_state.selected_category = picked.get("category") or st.session_state.selected_category
            st.session_state.selected_categories = picked.get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
            categories = memory_categories(picked)
            st.session_state.album_category = categories[0] if categories else ""
            st.session_state.album_selected_memory_id = memory_id
        open_category_album(st.session_state.get("album_category"), memory_id)
    elif action == "edit" and memory_id:
        open_memory_edit(memory_id)
    elif action == "music_edit" and memory_id:
        st.session_state.home_tab = "music"
        open_music_edit(memory_id)
    elif action == "music_delete" and memory_id:
        clear_memory_track(memory_id)
        st.session_state.home_tab = "music"
        st.session_state.page = "home"
    elif action == "home_music_play" and memory_id:
        st.session_state.home_tab = "music"
        st.session_state.home_playing_memory_id = memory_id
        st.session_state.page = "home"
    elif action == "playback_exit":
        st.session_state.playback_memories = []
        st.session_state.playback_index = 0
        st.session_state.page = "home"
    elif action == "playback":
        active_nfc = current_nfc_category()
        playback_memories = memories_for_current_nfc(load_memories(), active_nfc)
        if playback_memories:
            st.session_state.playback_memories = playback_memories
            st.session_state.playback_index = 0
            st.session_state.playback_started_at = time.time()
            st.session_state.playback_caption_index = 0
            st.session_state.playback_caption_started_at = time.time()
            st.session_state.playback_song_index = 0
            st.session_state.playback_song_started_at = time.time()
            st.session_state.page = "play_fullscreen"
        else:
            st.session_state.page = "home"
    elif action == "scan_start":
        if media_reference_available(st.session_state.get("photo_path")):
            st.session_state.page = "scan_running"
        else:
            st.session_state.scan_upload_notice = "사진을 먼저 선택해주세요."
            st.session_state.page = "scan_upload"
    elif action == "write":
        if mode in ("voice", "chat", "handwriting"):
            switch_write_mode(mode)
        st.session_state.page = "write"
    elif action == "write_question_prev":
        current_index = int(st.session_state.get("current_question_index", 0) or 0)
        set_write_question_index(current_index - 1, current_canvas_key())
        st.session_state.page = "write"
    elif action == "write_question_next":
        current_index = int(st.session_state.get("current_question_index", 0) or 0)
        set_write_question_index(current_index + 1, current_canvas_key())
        st.session_state.page = "write"
    elif action == "write_question_done":
        questions_count = len(st.session_state.get("questions") or fallback_photo_analysis()["questions"])
        current_index = int(st.session_state.get("current_question_index", 0) or 0)
        st.session_state.current_question_index = max(0, min(questions_count - 1, current_index))
        finish_write_record(current_canvas_key())
    elif action == "write_tool":
        if tool_param in ("pen", "eraser"):
            set_drawing_tool(tool_param, current_canvas_key())
        st.session_state.page = "write"
    elif action == "write_color":
        cleaned_color = str(color_param or "").strip().lstrip("#")
        if len(cleaned_color) == 6:
            set_pen_color(f"#{cleaned_color}", current_canvas_key())
        st.session_state.page = "write"
    elif action == "write_width_cycle":
        cycle_pen_width(current_canvas_key())
        st.session_state.page = "write"
    elif action == "write_undo":
        undo_handwriting(current_canvas_key())
        st.session_state.page = "write"
    elif action == "write_redo":
        redo_handwriting()
        st.session_state.page = "write"
    elif action == "music":
        if st.session_state.get("edit_mode") != "music_edit":
            st.session_state.editing_memory_id = None
            st.session_state.edit_mode = "create"
        if note_text is not None:
            st.session_state.memory_text = note_text
        st.session_state.page = "music"
    elif action == "music_loading":
        st.session_state.page = "music_loading"
    elif action == "music_done":
        st.session_state.page = "music_done"
    elif action in ("category_loading", "category_edit", "category_pick", "album_done", "nfc_scan", "nfc_done", "video", "player"):
        # 현재 버전에서는 카테고리/앨범완료/재생 화면을 사용하지 않으므로 홈으로 보낸다.
        if is_existing_edit_flow():
            finish_existing_music_edit()
        else:
            st.session_state.page = "home"
    elif action == "memory_done":
        st.session_state.page = "memory_done"
    elif action == "delete" and memory_id:
        delete_memory(memory_id)
        st.session_state.play_memory = None
        st.session_state.page = "home"
    elif action == "back":
        set_previous_page()
    st.query_params.clear()
    st.rerun()


html("""
<style>
* { box-sizing: border-box; }
.stApp { background:#efefef; color:#111; }
.block-container { max-width:920px !important; padding:28px 20px 44px !important; }
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
a { color:inherit; text-decoration:none; }

.app-card { width:760px; max-width:100%; margin:0 auto; background:#fff; overflow:hidden; }
.brand { font-size:15px; font-weight:900; }

.splash { height:508px; position:relative; display:flex; justify-content:center; align-items:center; overflow:hidden; background:#fff; }
.splash-hit { position:absolute; inset:0; z-index:5; }
.replay-logo-img { display:block; object-fit:contain; }
.splash-logo-img { width:213px; height:auto; transform:translateY(8px); }
.home-brand-logo { width:118px; height:auto; }
.flow-logo-img { width:92px; height:auto; margin:0 auto; transform:translateY(-6px); }

.home { min-height:410px; padding:32px 38px; background:radial-gradient(circle at 48% -7%, rgba(255,111,43,.55), rgba(255,160,102,.18) 18%, transparent 32%), linear-gradient(180deg, #fff7f3 0%, #fff 72%); }
.memory-panel { margin-top:26px; padding:24px; min-height:230px; border-radius:16px; background:rgba(255,255,255,.72); box-shadow:0 22px 46px rgba(0,0,0,.06); }
.tile-grid { display:grid; grid-template-columns:repeat(6, minmax(0, 1fr)); gap:14px; align-content:start; }
.tile { aspect-ratio:1.16/1; border-radius:10px; background:#e8e5e2; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#777; font-size:28px; font-weight:800; }
.tile img { width:100%; height:100%; object-fit:cover; }
.tile.selected { box-shadow:0 0 0 4px #111 inset; }
.bottom-actions { margin-top:28px; height:38px; border:3px solid #050505; border-radius:999px; display:grid; grid-template-columns:1fr 1fr; overflow:hidden; background:#050505; box-shadow:0 8px 16px rgba(0,0,0,.12); }
.bottom-actions a { display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:900; }
.bottom-actions .left { background:#fff; color:#111; border-radius:999px; }
.bottom-actions .right { background:#050505; color:#fff; }

.flow { min-height:410px; padding:26px 44px 32px; position:relative; background:radial-gradient(circle at 92% 100%, rgba(255,105,33,.28), transparent 34%), radial-gradient(circle at 0% 72%, rgba(255,105,33,.12), transparent 28%), #fff; }
.topbar { display:grid; grid-template-columns:36px 1fr 70px; align-items:center; margin-bottom:20px; font-size:14px; font-weight:800; }
.topbar .center { text-align:center; color:#b9b9b9; }
.back { width:30px; height:30px; border-radius:50%; background:#f1f1f1; display:flex; align-items:center; justify-content:center; font-size:23px; line-height:1; color:#0b67b2; font-weight:900; }
.guide { justify-self:end; height:28px; padding:0 14px; border-radius:999px; background:#fff; box-shadow:0 6px 16px rgba(0,0,0,.12); display:flex; align-items:center; justify-content:center; font-size:11px; color:#111; }

.scan-card { width:500px; max-width:100%; height:250px; margin:22px auto 0; border-radius:34px; background:rgba(255,120,39,.16); display:flex; align-items:center; justify-content:center; position:relative; overflow:hidden; }
.scan-frame { width:360px; height:170px; border-radius:16px; display:flex; align-items:center; justify-content:center; color:#222; font-size:12px; position:relative; overflow:hidden; }
.scan-frame::before { content:""; position:absolute; inset:0; border:3px solid #ff7b2d; border-left-color:transparent; border-right-color:transparent; border-radius:16px; pointer-events:none; }
.scan-frame::after { content:""; position:absolute; inset:0; border:3px solid #ff7b2d; border-top-color:transparent; border-bottom-color:transparent; border-radius:16px; pointer-events:none; }
.photo-fill { width:100%; height:100%; object-fit:cover; }
.scan-blur { filter:blur(6px) saturate(.65); opacity:.9; }
.scan-line { position:absolute; left:0; right:0; height:3px; background:#ff7b2d; box-shadow:0 0 18px rgba(255,123,45,.85); animation:scanDown 1.4s ease-in-out infinite; z-index:3; }
.scan-line.live { animation:none; top:var(--scan-y); }
.scan-label { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-size:12px; color:#111; z-index:4; }
.scan-progress-row { width:560px; max-width:90%; display:grid; grid-template-columns:1fr 44px; gap:16px; align-items:center; margin:18px auto 12px; font-size:13px; font-weight:800; }
.scan-progress-track { height:4px; border-radius:999px; background:#d8d8d8; overflow:hidden; }
.scan-progress-fill { height:100%; border-radius:999px; background:#ff7b2d; box-shadow:0 0 12px rgba(255,123,45,.45); }
.down-mark { text-align:center; color:#aaa; margin:10px 0; }
.black-pill { width:190px; height:54px; border-radius:999px; margin:0 auto; background:#050505; color:#fff; box-shadow:0 12px 26px rgba(0,0,0,.24); display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:900; }

div[data-testid="stFileUploader"] { width:360px !important; max-width:100%; height:170px; margin:-278px auto 108px; opacity:0; position:relative; z-index:50; }
div[data-testid="stFileUploader"] section,
div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] { min-height:170px !important; height:170px !important; padding:0 !important; cursor:pointer; }

.analysis-wrap { min-height:350px; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.photo-paper { width:315px; height:190px; background:#eee4d1; padding:10px; box-shadow:0 10px 24px rgba(0,0,0,.12); position:relative; }
.photo-paper img { width:100%; height:100%; object-fit:cover; filter:saturate(.75) contrast(.95); }
.keyword { position:absolute; width:92px; height:92px; border-radius:50%; background:linear-gradient(135deg, #fff, #ffe2cc); box-shadow:0 12px 28px rgba(255,116,44,.16); display:flex; align-items:center; justify-content:center; text-align:center; font-size:12px; font-weight:900; }
.kw-left { left:-70px; top:62px; }
.kw-top { right:-58px; top:-38px; }
.kw-right { right:-68px; bottom:-34px; }
.analysis-title { margin-top:26px; font-size:20px; font-weight:900; }
.analysis-sub { margin-top:6px; font-size:12px; color:#444; }
.progress-row { width:560px; max-width:90%; display:grid; grid-template-columns:1fr 44px; gap:16px; align-items:center; margin-top:26px; }
.progress-track { height:4px; border-radius:999px; background:#d8d8d8; position:relative; }
.progress-fill { position:absolute; left:0; top:0; bottom:0; width:100%; border-radius:999px; background:#ff7b2d; }
.progress-dot { position:absolute; left:100%; top:50%; width:22px; height:22px; transform:translate(-50%,-50%); border-radius:50%; background:#fff; border:2px solid #ffb275; }

.done-grid { display:grid; grid-template-columns:1fr 1fr; gap:22px; align-items:start; }
.done-title { font-size:20px; font-weight:900; margin-bottom:6px; }
.done-sub { font-size:11px; color:#555; margin-bottom:16px; }
.done-panel { min-height:210px; border-radius:18px; background:rgba(255,255,255,.72); box-shadow:0 12px 26px rgba(0,0,0,.08); padding:14px; }
.done-panel .photo-paper { width:100%; height:165px; }
.question-card { margin-top:12px; min-height:58px; border-radius:8px; background:#fff; box-shadow:0 4px 10px rgba(0,0,0,.18); padding:15px 16px; font-size:12px; line-height:1.45; font-weight:900; }
.question-card:first-child { margin-top:0; }
.wide-black { height:58px; border-radius:999px; margin-top:18px; background:#050505; color:#fff; display:flex; flex-direction:column; align-items:center; justify-content:center; box-shadow:0 12px 24px rgba(0,0,0,.24); font-size:15px; font-weight:900; }
.wide-black small { margin-top:3px; font-size:10px; font-weight:600; color:#ddd; }

.write-actions { display:flex; justify-content:flex-end; margin-top:18px; }
.next-button { width:132px; height:42px; border:0; border-radius:999px; background:#050505; color:#fff; box-shadow:0 10px 22px rgba(0,0,0,.2); display:flex; align-items:center; justify-content:center; font-family:inherit; font-size:13px; font-weight:900; cursor:pointer; }

.music-layout { display:grid; grid-template-columns:44% 56%; gap:28px; }

.native-music-wrap { min-height:520px; padding:26px 44px 32px; background:radial-gradient(circle at 92% 100%, rgba(255,105,33,.28), transparent 34%), radial-gradient(circle at 0% 72%, rgba(255,105,33,.12), transparent 28%), #fff; }
.native-music-wrap .stTextInput > div > div > input { height:36px; border:0; border-radius:4px; background:#f4f4f4; padding-left:16px; font-size:14px; }
.native-music-wrap .stTextInput > label { display:none; }
.native-music-wrap .stButton > button { height:36px; min-height:36px; border-radius:999px; background:#050505; color:#fff; box-shadow:none; }
.native-music-wrap .stButton > button:hover, .native-music-wrap .stButton > button:focus { background:#050505; color:#fff; border:0; }
.native-track-title { font-size:14px; font-weight:700; line-height:1.25; padding-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.native-track-artist { font-size:11px; color:#777; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.native-track-line { height:1px; background:#eee; margin:5px 0 6px; }
.selected-song-card { margin:10px 0 10px; padding:10px 12px; border-radius:12px; background:rgba(239,90,40,.10); border-left:4px solid #ef5a28; font-size:13px; font-weight:800; }
.preview-caption { color:#777; font-size:12px; margin:8px 0; }
.large-photo { height:136px; background:#dde7f1; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#6a8396; }
.large-photo img, .play-photo img { width:100%; height:100%; object-fit:cover; }
.turntable { height:178px; margin-top:16px; background:#ddd; position:relative; overflow:hidden; }
.small-record { position:absolute; width:150px; height:150px; left:52%; bottom:14px; transform:translateX(-50%); border-radius:50%; background:radial-gradient(circle, #1d1d20 0 50%, #070708 72%, #151515 100%); }
.small-record::after { content:""; position:absolute; left:50%; top:50%; width:44px; height:44px; margin:-22px; border-radius:50%; background:#f35f27; border:3px solid #111; }
.playlist { height:300px; overflow:hidden; padding-top:18px; }
.track { display:grid; grid-template-columns:28px 1fr 24px; gap:10px; align-items:center; min-height:30px; border-bottom:1px solid #eee; font-size:12px; }
.cover { width:24px; height:24px; background:#dde7f1; overflow:hidden; }
.cover img { width:100%; height:100%; object-fit:cover; }
.track-title { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.plus { color:#ff4b4b; font-weight:900; text-align:center; }
.player { min-height:520px; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; position:relative; padding:32px 44px; }
.record-scene { position:relative; width:300px; height:180px; }
.play-photo { position:absolute; left:30px; top:28px; z-index:2; width:150px; height:120px; border-radius:4px; background:#dde7f1; overflow:hidden; display:flex; align-items:center; justify-content:center; color:#6a8396; }
.record-scene .small-record { left:206px; z-index:1; }
.song-name { margin-top:8px; font-size:16px; font-weight:900; }
.memory-review { width:100%; max-width:560px; margin-top:18px; border-radius:20px; background:#fff; box-shadow:0 14px 32px rgba(0,0,0,.08); padding:18px; text-align:left; }
.review-label { font-size:12px; font-weight:900; color:#777; margin-bottom:8px; }
.review-note { min-height:70px; font-size:14px; line-height:1.42; color:#111; white-space:pre-wrap; }
.music-chip { margin-top:14px; min-height:42px; border-radius:999px; background:#f5f5f5; display:flex; align-items:center; justify-content:center; gap:10px; padding:0 16px; font-size:13px; font-weight:900; }
.audio-player { width:100%; margin-top:12px; }
.review-handwriting { width:100%; margin-top:14px; border-radius:14px; border:1px solid #eee; overflow:hidden; background:#fff; }
.review-handwriting img { width:100%; display:block; }
.player-actions { display:flex; gap:12px; align-items:center; justify-content:center; margin-top:18px; }
.delete-pill { width:112px; height:38px; border-radius:999px; background:#fff; color:#d24b3f; border:1px solid rgba(210,75,63,.35); display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:900; box-shadow:0 8px 18px rgba(0,0,0,.08); }

.stButton > button { height:40px; border-radius:999px; border:0; background:#fff; box-shadow:0 8px 18px rgba(0,0,0,.12); color:#111; font-size:13px; font-weight:900; }
.stButton > button:hover, .stButton > button:focus { border:0; color:#111; background:#fff; }
@keyframes scanDown { 0% { top:0; } 100% { top:100%; } }
@keyframes spin { to { transform:rotate(360deg); } }
@media (max-width:820px) { .tile-grid{grid-template-columns:repeat(3,1fr);} .done-grid,.music-layout{grid-template-columns:1fr;} .kw-left,.kw-top,.kw-right{display:none;} }


/* Music screen fixed layout */
.music-page-shell { width:760px; max-width:100%; min-height:520px; margin:0 auto; padding:26px 44px 34px; background:radial-gradient(circle at 92% 100%, rgba(255,105,33,.28), transparent 34%), radial-gradient(circle at 0% 72%, rgba(255,105,33,.12), transparent 28%), #fff; }
.music-back { width:30px; height:30px; margin-bottom:22px; border-radius:50%; background:#f1f1f1; display:flex; align-items:center; justify-content:center; font-size:23px; line-height:1; color:#0b67b2; font-weight:900; }
.music-photo-placeholder { height:150px; border-radius:6px; background:#dde7f1; display:flex; align-items:center; justify-content:center; color:#6a8396; font-size:28px; }
.music-turntable { height:210px; margin-top:18px; background:#ddd; position:relative; overflow:hidden; display:flex; align-items:center; justify-content:center; }
.music-record { width:190px; height:190px; border-radius:50%; background:radial-gradient(circle, #1d1d20 0 49%, #070708 72%, #151515 100%); position:relative; }
.music-record::after { content:""; position:absolute; left:50%; top:50%; width:56px; height:56px; margin:-28px; border-radius:50%; background:#f35f27; border:3px solid #111; }
.music-caption { color:#777; font-size:13px; margin:12px 0 14px; }
.music-track-title { font-size:14px; font-weight:800; line-height:1.2; padding-top:1px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.music-track-title.selected { color:#ef5a28; }
.music-track-artist { font-size:11px; color:#777; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.music-row-line { height:1px; background:#eee; margin:4px 0 7px; }
.music-cover-empty { width:38px; height:38px; background:#dde7f1; border-radius:4px; }
.music-selected-box { margin:12px 0 10px; padding:10px 12px; border-radius:12px; background:rgba(239,90,40,.10); border-left:4px solid #ef5a28; font-size:13px; font-weight:900; }
.music-page-shell div[data-testid="stTextInput"] input { height:42px !important; border:0 !important; border-radius:4px !important; background:#f4f4f4 !important; padding-left:20px !important; font-size:15px !important; box-shadow:none !important; }
.music-page-shell div[data-testid="stButton"] button { height:42px !important; min-height:42px !important; border-radius:999px !important; border:0 !important; background:#050505 !important; color:#fff !important; box-shadow:0 10px 22px rgba(0,0,0,.12) !important; font-weight:900 !important; }
.music-page-shell [data-testid="stImage"] img { border-radius:0 !important; }
.music-page-shell [data-testid="stVerticalBlock"] { gap:.35rem !important; }
@media (max-width:820px) { .music-page-shell { padding:24px 24px 32px; } }

</style>
""")

html("""
<style>
:root {
    --ipad-shell-width: 1180px;
    --ipad-shell-margin: 16px;
    --ipad-shell-height: calc(100dvh - (var(--ipad-shell-margin) * 2));
    --ipad-shell-radius: 28px;
}
html,
body,
.stApp {
    min-height:100dvh !important;
    overflow:auto !important;
}
.stApp .block-container {
    width:min(var(--ipad-shell-width), calc(100vw - (var(--ipad-shell-margin) * 2))) !important;
    max-width:min(var(--ipad-shell-width), calc(100vw - (var(--ipad-shell-margin) * 2))) !important;
    min-height:min(760px, var(--ipad-shell-height)) !important;
    max-height:var(--ipad-shell-height) !important;
    margin:var(--ipad-shell-margin) auto !important;
    padding:clamp(18px, 2.6vw, 34px) clamp(18px, 3.4vw, 42px) 76px !important;
    border-radius:var(--ipad-shell-radius) !important;
    overflow-x:hidden !important;
    overflow-y:auto !important;
    position:relative !important;
    -webkit-overflow-scrolling:touch;
}
.stApp .block-container::-webkit-scrollbar {
    width:0;
    height:0;
}
.stApp .app-card,
.stApp .home,
.stApp .flow,
.stApp .native-music-wrap,
.stApp .music-page-shell {
    width:100% !important;
    max-width:100% !important;
    min-height:calc(var(--ipad-shell-height) - 68px) !important;
    height:auto !important;
    overflow:visible !important;
}
.stApp .splash {
    height:auto !important;
    min-height:calc(var(--ipad-shell-height) - 68px) !important;
}
.stApp .flow,
.stApp .home {
    padding:clamp(22px, 3vw, 38px) clamp(24px, 4vw, 48px) 76px !important;
}
.stApp .home-content,
.stApp .home-footer-actions {
    max-width:100% !important;
}
.stApp .home-track-list,
.stApp .playlist {
    max-height:clamp(240px, 42dvh, 360px) !important;
    overflow-y:auto !important;
}
.stApp .record-photo-card {
    height:clamp(220px, 35dvh, 320px) !important;
}
.stApp .pad-card {
    height:auto !important;
    min-height:clamp(300px, 52dvh, 420px) !important;
}
.stApp .music-photo-box {
    height:clamp(150px, 26dvh, 220px) !important;
}
.stApp .music-turntable {
    height:clamp(170px, 31dvh, 250px) !important;
}
.stApp .video-photo {
    min-height:clamp(290px, 50dvh, 430px) !important;
}
.stApp .video-photo img {
    height:clamp(260px, 46dvh, 400px) !important;
}
.stApp .album-photo img,
.stApp .album-note {
    height:clamp(120px, 24dvh, 170px) !important;
}
.stApp .family-shell {
    width:100% !important;
    max-width:760px !important;
    margin:0 auto !important;
    padding-bottom:72px !important;
}
.stApp .family-bottom-nav {
    bottom:0 !important;
}
@supports (height: 100svh) {
    :root {
        --ipad-shell-height: calc(100svh - (var(--ipad-shell-margin) * 2));
    }
}
@media (max-width: 900px) {
    :root {
        --ipad-shell-margin: 10px;
        --ipad-shell-radius: 22px;
    }
    .stApp .block-container {
        padding:22px 22px 72px !important;
    }
    .stApp .home-title-zone,
    .stApp .home-content {
        margin-left:0 !important;
        width:100% !important;
    }
    .stApp .home-footer-actions {
        margin-left:0 !important;
        margin-right:0 !important;
        gap:24px !important;
    }
}
@media (max-width: 640px) {
    :root {
        --ipad-shell-margin: 0px;
        --ipad-shell-radius: 0px;
    }
    .stApp .block-container {
        width:100vw !important;
        max-width:100vw !important;
        min-height:100dvh !important;
        max-height:none !important;
        margin:0 !important;
        padding:18px 16px 72px !important;
        border-radius:0 !important;
    }
    .stApp .app-card,
    .stApp .home,
    .stApp .flow,
    .stApp .music-page-shell,
    .stApp .native-music-wrap {
        min-height:auto !important;
    }
    .stApp .home-footer-actions {
        grid-template-columns:1fr !important;
    }
}
</style>
""")


page = st.session_state["page"]
render_global_back_button()

if page == "splash":
    html(f"""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {{
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    margin:0 !important;
    padding:0 !important;
    overflow:hidden !important;
    scrollbar-width:none !important;
}}
html::-webkit-scrollbar,
body::-webkit-scrollbar,
.stApp::-webkit-scrollbar,
.stApp [data-testid="stAppViewContainer"]::-webkit-scrollbar,
.stApp [data-testid="stMain"]::-webkit-scrollbar,
.stApp [data-testid="stMainBlockContainer"]::-webkit-scrollbar {{
    display:none !important;
    width:0 !important;
    height:0 !important;
}}
.stApp {{
    background:#777 !important;
    color:#111 !important;
}}
.stApp .block-container {{
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:0 !important;
    overflow:hidden !important;
    position:relative !important;
    border-radius:0 !important;
    background:transparent !important;
}}
.stApp .block-container > div,
.stApp .block-container div[data-testid="stVerticalBlock"] {{
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    gap:0 !important;
    overflow:hidden !important;
}}
header,
[data-testid="stToolbar"],
[data-testid="stDecoration"],
#MainMenu,
footer {{
    display:none !important;
}}
.splash-start-screen {{
    position:fixed;
    left:50%;
    top:50%;
    width:1180px;
    height:760px;
    transform:translate(-50%, -50%);
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
    background:#fff;
    z-index:1000;
}}
.splash-start-screen .splash-logo-img {{
    width:213px !important;
    height:auto !important;
    transform:none !important;
}}
.splash-hit {{
    position:absolute;
    inset:0;
    z-index:5;
    display:block;
    background:transparent;
    cursor:pointer;
}}
</style>
<div class="splash-start-screen">
  <a class="splash-hit" href="?action=nfc_intro" target="_self" aria-label="시작"></a>
  {SPLASH_LOGO_HTML}
</div>
""")

elif page == "nfc_intro":
    html("""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    margin:0 !important;
    overflow:hidden !important;
}
.stApp { background:#777 !important; color:#111 !important; }
.stApp .block-container {
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:0 !important;
    overflow:hidden !important;
    position:relative !important;
    border-radius:0 !important;
    background:transparent !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
.stApp .block-container div[data-testid="stVerticalBlock"] { gap:0 !important; }
.nfc-start-screen {
    position:fixed;
    left:50%;
    top:50%;
    width:1180px;
    height:760px;
    transform:translate(-50%, -50%);
    overflow:hidden;
    z-index:1000;
    background:
      radial-gradient(circle at 0% 76%, rgba(255,132,70,.22), transparent 33%),
      radial-gradient(circle at 98% 86%, rgba(255,132,70,.23), transparent 35%),
      #fbfbfb;
}
.nfc-start-title {
    position:absolute;
    left:0;
    right:0;
    top:128px;
    text-align:center;
    font-size:26px;
    line-height:1;
    font-weight:1000;
}
.nfc-start-arrow {
    position:absolute;
    left:50%;
    top:270px;
    width:54px;
    height:88px;
    transform:translateX(-50%);
}
.nfc-start-arrow::before {
    content:"";
    position:absolute;
    left:12px;
    top:0;
    width:30px;
    height:52px;
    background:linear-gradient(180deg, #fff 0 20%, #ffd6c3 20% 38%, #fff 38% 54%, #ffd6c3 54%);
}
.nfc-start-arrow::after {
    content:"";
    position:absolute;
    left:0;
    bottom:0;
    width:0;
    height:0;
    border-left:27px solid transparent;
    border-right:27px solid transparent;
    border-top:35px solid #ffd6c3;
}
.nfc-start-rings {
    position:absolute;
    left:50%;
    top:392px;
    width:760px;
    height:240px;
    transform:translateX(-50%);
}
.nfc-start-ring {
    position:absolute;
    left:50%;
    top:50%;
    transform:translate(-50%, -50%);
    border:1px solid rgba(255,123,45,.33);
    border-radius:50%;
}
.nfc-start-ring.r1 { width:200px; height:74px; background:rgba(255,123,45,.16); }
.nfc-start-ring.r2 { width:330px; height:112px; background:rgba(255,123,45,.08); }
.nfc-start-ring.r3 { width:480px; height:160px; }
.nfc-start-ring.r4 { width:660px; height:205px; }
.nfc-start-ring.r5 { width:760px; height:240px; opacity:.75; }
.nfc-start-slot {
    position:absolute;
    left:50%;
    top:50%;
    width:270px;
    height:28px;
    transform:translate(-50%, -74%);
    border:2px solid rgba(255,123,45,.48);
    border-radius:19px 19px 7px 7px;
    background:linear-gradient(180deg, rgba(255,210,188,.85), rgba(255,183,151,.48));
    box-shadow:0 10px 26px rgba(255,123,45,.16);
}
.nfc-start-label {
    position:absolute;
    left:0;
    right:0;
    top:568px;
    text-align:center;
}
.nfc-start-label strong {
    display:block;
    color:#050505;
    font-size:28px;
    line-height:1;
    font-weight:1000;
    margin-bottom:16px;
}
.nfc-start-label span {
    display:block;
    color:#111;
    font-size:18px;
    line-height:1;
    font-weight:700;
}
.nfc-start-hit {
    position:absolute;
    left:50%;
    top:340px;
    width:780px;
    height:300px;
    transform:translateX(-50%);
    display:block;
    z-index:30;
    cursor:pointer;
    background:transparent;
}
</style>
<div class="nfc-start-screen">
  <a class="nfc-start-hit" href="?action=nfc_detected&uid=RE01" target="_self" aria-label="NFC 인식"></a>
  <div class="nfc-start-title">카세트를 꽂아주세요</div>
  <div class="nfc-start-arrow"></div>
  <div class="nfc-start-rings">
    <div class="nfc-start-ring r5"></div>
    <div class="nfc-start-ring r4"></div>
    <div class="nfc-start-ring r3"></div>
    <div class="nfc-start-ring r2"></div>
    <div class="nfc-start-ring r1"></div>
    <div class="nfc-start-slot"></div>
  </div>
  <div class="nfc-start-label">
    <strong>NFC 인식 영역</strong>
  </div>
</div>
""")

elif page == "nfc_recognized":
    nfc_label = escape(st.session_state.get("nfc_label") or "RE:01")
    html(f"""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {{
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    margin:0 !important;
    overflow:hidden !important;
}}
.stApp {{ background:#777 !important; color:#111 !important; }}
.stApp .block-container {{
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:0 !important;
    overflow:hidden !important;
    position:relative !important;
    border-radius:0 !important;
    background:transparent !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
.stApp .block-container div[data-testid="stVerticalBlock"] {{ gap:0 !important; }}
.nfc-start-screen {{
    position:fixed;
    left:50%;
    top:50%;
    width:1180px;
    height:760px;
    transform:translate(-50%, -50%);
    overflow:hidden;
    z-index:1000;
    background:
      radial-gradient(circle at 0% 76%, rgba(255,132,70,.22), transparent 33%),
      radial-gradient(circle at 98% 86%, rgba(255,132,70,.23), transparent 35%),
      #fbfbfb;
}}
.nfc-start-rings {{
    position:absolute;
    left:50%;
    top:365px;
    width:760px;
    height:240px;
    transform:translateX(-50%);
}}
.nfc-start-ring {{
    position:absolute;
    left:50%;
    top:50%;
    transform:translate(-50%, -50%);
    border:1px solid rgba(255,123,45,.33);
    border-radius:50%;
}}
.nfc-start-ring.r1 {{ width:200px; height:74px; background:rgba(255,123,45,.16); }}
.nfc-start-ring.r2 {{ width:330px; height:112px; background:rgba(255,123,45,.08); }}
.nfc-start-ring.r3 {{ width:480px; height:160px; }}
.nfc-start-ring.r4 {{ width:660px; height:205px; }}
.nfc-start-ring.r5 {{ width:760px; height:240px; opacity:.75; }}
.nfc-start-card {{
    position:absolute;
    left:50%;
    top:212px;
    width:170px;
    height:268px;
    transform:translateX(-50%);
    border-radius:16px;
    background:linear-gradient(180deg, rgba(255,255,255,.42), rgba(255,245,240,.32));
    border:1px solid rgba(255,255,255,.62);
    box-shadow:0 22px 42px rgba(255,123,45,.18), inset 0 0 24px rgba(255,255,255,.30);
    backdrop-filter:blur(8px);
    animation:nfcCardDrop 2.25s cubic-bezier(.18,.82,.18,1) both;
}}
.nfc-start-card-label {{
    position:absolute;
    left:22px;
    top:27px;
    color:#fff;
    font-size:28px;
    line-height:1;
    font-weight:800;
}}
.nfc-start-card-inner {{
    position:absolute;
    left:41px;
    top:65px;
    width:88px;
    height:140px;
    border-radius:15px;
    background:linear-gradient(180deg, #ff8e69 0%, #ffd6c8 100%);
    box-shadow:0 0 22px rgba(255,104,48,.55);
}}
.nfc-start-card-logo {{
    position:absolute;
    left:0;
    right:0;
    bottom:30px;
    text-align:center;
    color:#fff;
    font-size:12px;
    line-height:.8;
    font-weight:1000;
}}
.nfc-start-card-logo small {{
    display:block;
    margin-top:3px;
    font-size:5px;
    letter-spacing:2px;
}}
.nfc-start-card-dot {{
    position:absolute;
    width:9px;
    height:9px;
    border-radius:50%;
    background:#fff;
}}
.nfc-start-card-dot.d1 {{ left:12px; top:12px; }}
.nfc-start-card-dot.d2 {{ right:12px; top:12px; }}
.nfc-start-card-dot.d3 {{ left:12px; bottom:12px; }}
.nfc-start-card-dot.d4 {{ right:12px; bottom:12px; }}
.nfc-start-big-label {{
    position:absolute;
    left:0;
    right:0;
    top:548px;
    text-align:center;
    color:#ff5b18;
    font-size:74px;
    line-height:1;
    font-weight:1000;
}}
@keyframes nfcCardDrop {{
    0% {{ opacity:0; transform:translate(-50%, -190px); }}
    100% {{ opacity:1; transform:translate(-50%, 0); }}
}}
</style>
<div class="nfc-start-screen">
  <div class="nfc-start-rings">
    <div class="nfc-start-ring r5"></div>
    <div class="nfc-start-ring r4"></div>
    <div class="nfc-start-ring r3"></div>
    <div class="nfc-start-ring r2"></div>
    <div class="nfc-start-ring r1"></div>
  </div>
  <div class="nfc-start-card">
    <div class="nfc-start-card-dot d1"></div>
    <div class="nfc-start-card-dot d2"></div>
    <div class="nfc-start-card-dot d3"></div>
    <div class="nfc-start-card-dot d4"></div>
    <div class="nfc-start-card-label">{nfc_label}</div>
    <div class="nfc-start-card-inner"></div>
    <div class="nfc-start-card-logo">Re:Play<small>RE:PLAY</small></div>
  </div>
  <div class="nfc-start-big-label">{nfc_label}</div>
</div>
""")
    time.sleep(3.6)
    go("home")

elif page == "home":
    active_nfc = current_nfc_category()
    st.session_state.album_category = active_nfc
    all_home_memories = load_memories()
    home_memories = memories_for_current_nfc(all_home_memories, active_nfc)
    memory_count = len(home_memories)
    current_tab = st.session_state.get("home_tab", "album")
    active_album = current_tab == "album"
    active_music = current_tab == "music"
    album_tab_class = " active" if active_album else ""
    music_tab_class = " active" if active_music else ""
    kicker_html = f'<div class="home-kicker">{escape(active_nfc)}</div>' if active_album else ""

    album_cards = []
    for memory in home_memories:
        memory_id_value = str(memory.get("id") or "")
        memory_id_query = quote(memory_id_value)
        photo_src = image_src(photo_path_for_memory(memory))
        photo_html = (
            f'<img src="{escape(photo_src, quote=True)}" alt="">'
            if photo_src
            else '<span class="home-photo-empty"></span>'
        )
        title = escape(memory_title(memory))
        preview = escape(memory_note_preview(memory, 34))
        album_cards.append(f"""
<article class="home-album-card">
  <a class="home-card-photo-link" href="?action=select&memory={memory_id_query}" target="_self">
    <div class="home-card-photo">{photo_html}</div>
  </a>
  <div class="home-card-title">{title}</div>
  <div class="home-card-bottom">
    <div class="home-card-note">{preview}</div>
    <a class="home-card-edit" href="?action=edit&memory={memory_id_query}" target="_self" aria-label="수정">✎</a>
  </div>
</article>
""")

    if home_memories:
        for _ in range(max(0, 4 - len(home_memories))):
            album_cards.append("""
<article class="home-album-card home-album-card-empty">
  <div class="home-card-photo"><span class="home-photo-empty"></span></div>
  <div class="home-card-title">이름 없는 추억</div>
  <div class="home-card-bottom">
    <div class="home-card-note">새 기록이 생기면 이곳에 보여요.</div>
    <span class="home-card-edit ghost">✎</span>
  </div>
</article>
""")

    album_content = (
        '<div class="home-album-strip">' + "".join(album_cards) + "</div>"
        if album_cards
        else '<div class="home-empty">아직 저장된 추억이 없어요.<br>아래 버튼으로 첫 기억을 기록해보세요.</div>'
    )

    music_memories = [memory for memory in home_memories if memory.get("selected_track")]
    home_audio_html = ""
    playing_id = st.session_state.get("home_playing_memory_id")
    playing_memory = next((memory for memory in music_memories if memory.get("id") == playing_id), None)
    if playing_memory:
        playing_track = playing_memory.get("selected_track") or {}
        if playing_track:
            home_audio_html = f"""
<div class="home-now-playing">
  <div>{escape(playing_track.get("title") or "선택한 음악")}</div>
</div>
"""

    if music_memories:
        music_cards = []
        for memory in music_memories:
            memory_id_value = str(memory.get("id") or "")
            memory_id_query = quote(memory_id_value)
            track = memory.get("selected_track") or {}
            cover_src = track.get("image") or image_src(photo_path_for_memory(memory))
            cover_html = (
                f'<img src="{escape(cover_src, quote=True)}" alt="">'
                if cover_src
                else '<span>♪</span>'
            )
            title = escape(track.get("title") or "노래 제목")
            artist = escape(track.get("artist") or "가수")
            audio_html = render_track_audio_html(track, autoplay=False, compact=False)
            if not audio_html:
                audio_html = '<div class="home-music-audio-missing">음원 파일을 찾지 못했어요.</div>'

            music_cards.append(f"""
<article class="home-music-card">
  <div class="home-music-card-top">
    <div class="home-music-cover">{cover_html}</div>
    <div class="home-music-copy">
      <div class="home-music-title">{title}</div>
      <div class="home-music-artist">By {artist}</div>
    </div>
  </div>
  <div class="home-music-audio">{audio_html}</div>
  <div class="home-music-actions">
    <a class="home-music-edit" href="?action=music_edit&memory={memory_id_query}" target="_self">수정</a>
    <a class="home-music-delete" href="?action=music_delete&memory={memory_id_query}" target="_self">삭제</a>
  </div>
</article>
""")

        music_content = '<div class="home-music-strip">' + "".join(music_cards) + "</div>"
    else:
        music_content = '<div class="home-empty">아직 연결된 음악이 없어요.<br>기억을 기록하면서 음악을 골라보세요.</div>'

    content_html = album_content if active_album else music_content

    html(f"""
<style>
.stApp {{ background:#f0f0f0 !important; color:#111 !important; }}
.block-container {{
    max-width:980px !important;
    min-height:690px !important;
    margin:0 auto !important;
    padding:32px 34px 42px !important;
    background:
      radial-gradient(circle at 52% 0%, rgba(255,128,74,.27), rgba(255,238,228,.46) 20%, transparent 39%),
      linear-gradient(180deg, #fbf6f2 0%, #f2f2f2 46%, #f7f7f7 100%) !important;
    overflow:hidden !important;
    position:relative !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
div[data-testid="stVerticalBlock"] {{ gap:0 !important; }}
.home-header {{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    min-height:78px;
}}
.home-logo {{
    width:118px;
    color:#050505;
    display:flex;
    flex-direction:column;
    align-items:flex-start;
}}
.home-tools {{
    display:flex;
    align-items:center;
    gap:18px;
    padding-right:18px;
}}
.home-search-button,
.home-family-button,
.home-help-button {{
    height:54px;
    border-radius:999px;
    background:#fff;
    box-shadow:0 18px 34px rgba(0,0,0,.045);
    display:flex;
    align-items:center;
    justify-content:center;
    color:#050505;
}}
.home-search-button {{ width:144px; justify-content:flex-end; padding-right:28px; }}
.home-family-button {{ min-width:92px; padding:0 22px; font-size:14px; font-weight:1000; }}
.home-help-button {{ width:54px; font-size:28px; font-weight:500; }}
.home-search-icon {{
    width:18px;
    height:18px;
    border:3px solid #050505;
    border-radius:50%;
    position:relative;
    display:block;
}}
.home-search-icon::after {{
    content:"";
    position:absolute;
    width:10px;
    height:3px;
    right:-8px;
    bottom:-5px;
    border-radius:999px;
    background:#050505;
    transform:rotate(45deg);
}}
.home-title-zone {{
    width:calc(100% - 96px);
    margin:0 0 0 64px;
}}
.home-kicker {{
    color:#ff5b18;
    font-size:16px;
    font-weight:800;
    line-height:1;
    margin-bottom:9px;
}}
.home-title {{
    margin:0;
    font-size:32px;
    line-height:1.08;
    font-weight:1000;
    letter-spacing:0;
}}
.home-tab-row {{
    height:56px;
    margin-top:24px;
    border-bottom:1px solid #d2d2d2;
    display:flex;
    align-items:flex-start;
    gap:46px;
}}
.home-tab {{
    height:39px;
    display:flex;
    align-items:flex-start;
    color:#4d4d4d;
    font-size:16px;
    font-weight:900;
    border-bottom:3px solid transparent;
}}
.home-tab.active {{
    color:#ff5b18;
    border-bottom-color:#ff5b18;
}}
.home-count {{
    margin-left:auto;
    padding-top:2px;
    color:#444;
    font-size:15px;
    font-weight:700;
}}
.home-content {{
    margin:50px -34px 0 50px;
    min-height:252px;
}}
.home-album-strip {{
    display:flex;
    gap:34px;
    overflow-x:auto;
    overflow-y:hidden;
    padding:0 34px 16px 0;
    scroll-snap-type:x proximity;
    scrollbar-width:none;
}}
.home-album-strip::-webkit-scrollbar {{ display:none; }}
.home-album-card {{
    flex:0 0 226px;
    min-height:252px;
    border-radius:14px;
    background:#fff;
    box-shadow:0 16px 32px rgba(0,0,0,.045);
    padding:10px;
    scroll-snap-align:start;
}}
.home-card-photo-link {{
    display:block;
    border-radius:10px;
    overflow:hidden;
}}
.home-card-photo {{
    height:145px;
    border-radius:10px;
    background:#eeeeee;
    overflow:hidden;
    display:flex;
    align-items:center;
    justify-content:center;
}}
.home-card-photo img {{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}}
.home-photo-empty {{
    width:100%;
    height:100%;
    background:#eeeeee;
    display:block;
}}
.home-card-title {{
    margin:15px 7px 0;
    min-height:24px;
    color:#111;
    font-size:16px;
    line-height:1.28;
    font-weight:900;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.home-card-bottom {{
    margin:15px 6px 0 7px;
    display:grid;
    grid-template-columns:1fr 44px;
    gap:12px;
    align-items:center;
}}
.home-card-note {{
    color:#858585;
    font-size:12px;
    line-height:1.35;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.home-card-edit {{
    width:38px;
    height:38px;
    border-radius:9px;
    background:#333;
    color:#fff;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:22px;
    line-height:1;
    box-shadow:none;
}}
.home-card-edit.ghost {{
    opacity:.45;
}}
.home-album-card-empty {{
    background:rgba(255,255,255,.76);
}}
.home-music-strip {{
    display:flex;
    gap:28px;
    overflow-x:auto;
    overflow-y:hidden;
    padding:0 34px 16px 0;
    scroll-snap-type:x proximity;
    scrollbar-width:none;
}}
.home-music-strip::-webkit-scrollbar {{ display:none; }}
.home-music-card {{
    flex:0 0 260px;
    min-height:236px;
    border-radius:16px;
    background:#fff;
    box-shadow:0 16px 32px rgba(0,0,0,.05);
    padding:14px;
    scroll-snap-align:start;
}}
.home-music-card-top {{
    display:grid;
    grid-template-columns:74px 1fr;
    gap:14px;
    align-items:center;
    min-height:78px;
}}
.home-music-cover {{
    width:74px;
    height:74px;
    border-radius:8px;
    background:#ececec;
    color:#999;
    overflow:hidden;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:28px;
}}
.home-music-cover img {{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}}
.home-music-copy {{
    min-width:0;
}}
.home-music-title {{
    color:#ff5b18;
    font-size:17px;
    line-height:1.15;
    font-weight:1000;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.home-music-artist {{
    margin-top:8px;
    color:#777;
    font-size:12px;
    line-height:1.1;
    font-weight:800;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.home-music-audio {{
    margin-top:18px;
    height:42px;
    border-radius:999px;
    background:#f0f0f0;
    overflow:hidden;
    display:flex;
    align-items:center;
    padding:0 8px;
}}
.home-music-audio .native-audio {{
    width:100%;
    height:34px;
}}
.home-music-audio .native-audio audio {{
    width:100%;
    height:34px;
    display:block;
}}
.home-music-audio-missing {{
    color:#888;
    font-size:11px;
    font-weight:800;
    width:100%;
    text-align:center;
}}
.home-music-actions {{
    margin-top:20px;
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:10px;
}}
.home-music-actions a {{
    height:38px;
    border-radius:12px;
    display:flex;
    align-items:center;
    justify-content:center;
    text-decoration:none !important;
    font-size:13px;
    font-weight:1000;
}}
.home-music-edit {{
    background:#111;
    color:#fff !important;
}}
.home-music-delete {{
    background:#f4f4f4;
    color:#cf4b38 !important;
}}
.home-empty {{
    min-height:300px;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    color:#777;
    font-size:16px;
    line-height:1.6;
    text-align:center;
}}
.home-footer-actions {{
    position:relative;
    left:auto;
    right:auto;
    bottom:auto;
    margin:42px 44px 0 44px;
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:70px;
}}
.home-footer-actions a {{
    height:58px;
    border-radius:20px;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:18px;
    font-weight:1000;
    box-shadow:0 16px 30px rgba(0,0,0,.10);
    text-decoration:none !important;
}}
.home-record-button {{
    background:#111;
    color:#fff !important;
}}
.home-play-button {{
    background:#fff;
    color:#111 !important;
}}
@media (max-width:900px) {{
    .block-container {{
        max-width:820px !important;
        min-height:600px !important;
        padding:28px 26px 36px !important;
    }}
    .home-title-zone {{
        width:100%;
        margin-left:30px;
    }}
    .home-content {{
        margin:42px -26px 0 30px;
    }}
    .home-album-card {{
        flex-basis:226px;
        min-height:252px;
    }}
    .home-card-photo {{
        height:145px;
    }}
    .home-music-card {{
        flex-basis:250px;
    }}
    .home-music-strip {{
        gap:24px;
    }}
    .home-footer-actions {{
        margin:38px 10px 0 10px;
        gap:44px;
    }}
    .home-footer-actions a {{
        height:58px;
        font-size:18px;
    }}
}}

/* home screen: hide visible scrollbar only */
html, body, .stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {{
    overflow:hidden !important;
}}
*::-webkit-scrollbar {{
    display:none !important;
    width:0 !important;
    height:0 !important;
}}
* {{
    scrollbar-width:none !important;
}}
</style>
<div class="home-header">
  <div class="home-logo">{HOME_LOGO_HTML}</div>
  <div class="home-tools">
    <div class="home-search-button" aria-label="검색"><span class="home-search-icon"></span></div>
    <a class="home-family-button" href="?action=family_home" target="_self">가족앱</a>
    <div class="home-help-button" aria-label="도움말">?</div>
  </div>
</div>
<section class="home-title-zone">
  {kicker_html}
  <h1 class="home-title">우리 가족</h1>
  <div class="home-tab-row">
    <a class="home-tab{album_tab_class}" href="?action=home_tab&tab=album" target="_self">앨범</a>
    <a class="home-tab{music_tab_class}" href="?action=home_tab&tab=music" target="_self">음악</a>
    <div class="home-count">{memory_count}장의 추억 보관중</div>
  </div>
</section>
<main class="home-content">{content_html}</main>
<div class="home-footer-actions">
  <a class="home-record-button" href="?action=record" target="_self">기억 기록하기</a>
  <a class="home-play-button" href="?action=playback" target="_self">추억 재생하기</a>
</div>
""")

elif page == "family_home":
    family_memories = load_memories()
    latest_memory = family_memories[0] if family_memories else None
    total_count = len(family_memories)

    if latest_memory:
        latest_category = memory_primary_category(latest_memory)
        latest_group = (
            album_memories_for_category(latest_category, source_memories=family_memories, strict=True)
            if latest_category
            else [latest_memory]
        )
        latest_count = len(latest_group) or 1
        latest_title = escape(family_memory_title(latest_memory))
        latest_track = escape(family_track_text(latest_memory))
        latest_photo = family_photo_html(latest_memory, "family-hero-photo")
        latest_href = f'?action=family_play&memory={quote(str(latest_memory.get("id") or ""))}'
        latest_count_text = f"{latest_count}개의 기록"
    else:
        latest_title = "오늘의 추억"
        latest_track = "아직 가족에게 공유된 추억이 없어요."
        latest_photo = '<div class="family-photo-placeholder">사진 없음</div>'
        latest_href = "#"
        latest_count_text = "0개의 기록"

    recent_cards = []
    for memory in family_memories[:12]:
        memory_id_value = str(memory.get("id") or "")
        memory_id_query = quote(memory_id_value)
        category = memory_primary_category(memory)
        grouped_count = (
            len(album_memories_for_category(category, source_memories=family_memories, strict=True))
            if category
            else 1
        )
        photo_html = family_photo_html(memory, "family-recent-photo")
        title = escape(family_memory_title(memory))
        track = escape(family_track_text(memory))
        recent_cards.append(f"""
<a class="family-recent-card" href="?action=family_play&memory={memory_id_query}" target="_self">
  <div class="family-recent-photo-wrap">{photo_html}</div>
  <div class="family-recent-title">{title}</div>
  <div class="family-recent-meta">{grouped_count}개의 기록</div>
  <div class="family-recent-track">{track}</div>
</a>
""")
    recent_html = (
        '<div class="family-scroll-row">' + "".join(recent_cards) + "</div>"
        if recent_cards
        else '<div class="family-empty">최근 재생할 추억이 아직 없어요.</div>'
    )

    member_rows = []
    for member in shared_family_members():
        active_class = " active" if member.get("active") else ""
        initial = escape(str(member.get("name", ""))[:1])
        member_rows.append(f"""
<div class="family-member">
  <div class="family-avatar{active_class}">{initial}</div>
  <div>
    <div class="family-member-name">{escape(member.get("name", ""))}</div>
    <div class="family-member-status">{escape(member.get("status", ""))}</div>
  </div>
</div>
""")
    members_html = "".join(member_rows)

    playlist_cards = []
    for category, items in family_playlist_groups(family_memories)[:8]:
        if not category:
            continue
        category_query = quote(str(category))
        first_memory = items[0] if items else {}
        photo_html = family_photo_html(first_memory, "family-playlist-photo")
        playlist_cards.append(f"""
<a class="family-playlist-card" href="?action=family_category&category={category_query}" target="_self">
  <div class="family-playlist-cover">{photo_html}<span></span></div>
  <div class="family-playlist-copy">
    <div class="family-playlist-title">{escape(category)} Playlist</div>
    <div class="family-playlist-meta">{len(items)}개의 추억</div>
    <div class="family-playlist-track">{escape(family_track_text(first_memory))}</div>
  </div>
</a>
""")
    playlist_html = (
        '<div class="family-playlist-list">' + "".join(playlist_cards) + "</div>"
        if playlist_cards
        else '<div class="family-empty">카테고리가 생기면 추천 플레이리스트가 만들어져요.</div>'
    )

    html(f"""
<style>
.stApp {{ background:#f0f0f0 !important; color:#111 !important; }}
.block-container {{
    max-width:560px !important;
    min-height:860px !important;
    margin:0 auto !important;
    padding:20px 18px 28px !important;
    background:
      radial-gradient(circle at 72% 0%, rgba(255,143,94,.30), transparent 31%),
      linear-gradient(180deg, #fff8f3 0%, #fff 42%, #f6f3f0 100%) !important;
    overflow:visible !important;
    position:relative !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
div[data-testid="stVerticalBlock"] {{ gap:0 !important; }}
.family-shell {{ padding:8px 4px 0; }}
.family-top {{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    min-height:92px;
}}
.family-logo {{ width:118px; padding-top:8px; }}
.family-top-icons {{ display:flex; align-items:center; gap:12px; }}
.family-icon {{
    width:42px;
    height:42px;
    border-radius:50%;
    background:#fff;
    box-shadow:0 14px 28px rgba(0,0,0,.07);
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:18px;
    font-weight:1000;
}}
.family-greeting {{ margin:4px 0 22px; }}
.family-greeting h1 {{
    margin:0;
    font-size:28px;
    line-height:1.18;
    font-weight:1000;
    letter-spacing:0;
}}
.family-greeting p {{
    margin:8px 0 0;
    color:#8b817b;
    font-size:14px;
    font-weight:800;
}}
.family-section-head {{
    display:flex;
    align-items:center;
    justify-content:space-between;
    margin:24px 2px 12px;
}}
.family-section-title {{
    font-size:17px;
    font-weight:1000;
}}
.family-section-count {{
    color:#ef5a28;
    font-size:12px;
    font-weight:1000;
}}
.family-today-card {{
    border-radius:28px;
    background:linear-gradient(145deg, #fff 0%, #fff6ef 100%);
    box-shadow:0 22px 44px rgba(110,72,48,.12);
    padding:18px;
    overflow:hidden;
}}
.family-art-row {{
    height:190px;
    position:relative;
    display:flex;
    align-items:center;
    justify-content:center;
}}
.family-photo-panel {{
    width:188px;
    height:152px;
    border-radius:22px;
    background:#eee;
    overflow:hidden;
    position:relative;
    z-index:2;
    box-shadow:0 16px 30px rgba(0,0,0,.11);
}}
.family-photo-panel img,
.family-recent-photo-wrap img,
.family-playlist-cover img {{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}}
.family-record {{
    width:150px;
    height:150px;
    border-radius:50%;
    margin-left:-34px;
    background:radial-gradient(circle, #f46b32 0 18%, #111 19% 55%, #050505 56% 100%);
    box-shadow:inset 10px 10px 22px rgba(255,255,255,.06), 0 18px 28px rgba(0,0,0,.16);
}}
.family-today-copy {{
    display:grid;
    grid-template-columns:1fr auto;
    gap:14px;
    align-items:end;
    margin-top:6px;
}}
.family-today-label {{
    color:#ef5a28;
    font-size:12px;
    font-weight:1000;
    margin-bottom:7px;
}}
.family-today-title {{
    font-size:24px;
    line-height:1.18;
    font-weight:1000;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.family-today-meta {{
    margin-top:8px;
    color:#8c817a;
    font-size:12px;
    line-height:1.35;
    font-weight:800;
}}
.family-play-button {{
    height:48px;
    padding:0 22px;
    border-radius:999px;
    background:#111;
    color:#fff !important;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:14px;
    font-weight:1000;
    box-shadow:0 14px 26px rgba(0,0,0,.18);
    white-space:nowrap;
}}
.family-scroll-row {{
    display:flex;
    gap:14px;
    overflow-x:auto;
    padding:2px 4px 12px 2px;
    scrollbar-width:none;
}}
.family-scroll-row::-webkit-scrollbar {{ display:none; }}
.family-recent-card {{
    flex:0 0 142px;
    border-radius:22px;
    background:#fff;
    box-shadow:0 16px 30px rgba(0,0,0,.07);
    padding:10px;
}}
.family-recent-photo-wrap {{
    height:110px;
    border-radius:17px;
    background:#eee;
    overflow:hidden;
}}
.family-recent-title {{
    margin-top:10px;
    font-size:14px;
    line-height:1.2;
    font-weight:1000;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.family-recent-meta,
.family-recent-track,
.family-playlist-meta,
.family-playlist-track {{
    color:#918984;
    font-size:11px;
    line-height:1.35;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.family-recent-track {{ margin-top:5px; }}
.family-share-card {{
    border-radius:24px;
    background:#fff;
    box-shadow:0 16px 34px rgba(0,0,0,.07);
    padding:14px;
}}
.family-member {{
    min-height:52px;
    display:flex;
    align-items:center;
    gap:12px;
    border-bottom:1px solid #f0ebe7;
}}
.family-member:last-child {{ border-bottom:0; }}
.family-avatar {{
    width:36px;
    height:36px;
    border-radius:50%;
    background:#f5f1ee;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:13px;
    font-weight:1000;
}}
.family-avatar.active {{
    background:#111;
    color:#fff;
}}
.family-member-name {{
    font-size:13px;
    font-weight:1000;
}}
.family-member-status {{
    margin-top:3px;
    color:#8e857f;
    font-size:11px;
    font-weight:800;
}}
.family-playlist-list {{
    display:grid;
    gap:12px;
}}
.family-playlist-card {{
    min-height:84px;
    border-radius:22px;
    background:#fff;
    box-shadow:0 16px 30px rgba(0,0,0,.065);
    padding:10px 12px 10px 10px;
    display:grid;
    grid-template-columns:70px 1fr;
    gap:14px;
    align-items:center;
}}
.family-playlist-cover {{
    width:70px;
    height:64px;
    border-radius:17px;
    background:#eee;
    overflow:hidden;
    position:relative;
}}
.family-playlist-cover span {{
    position:absolute;
    right:7px;
    bottom:7px;
    width:18px;
    height:18px;
    border-radius:50%;
    background:#111;
    box-shadow:inset 0 0 0 5px #ef5a28;
}}
.family-playlist-title {{
    font-size:15px;
    line-height:1.2;
    font-weight:1000;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.family-empty {{
    border-radius:22px;
    background:rgba(255,255,255,.72);
    padding:30px 18px;
    text-align:center;
    color:#8e857f;
    font-size:14px;
    line-height:1.42;
    font-weight:800;
}}
.family-photo-placeholder {{
    width:100%;
    height:100%;
    background:linear-gradient(135deg, #f1ede9, #fff8f2);
    color:#a09790;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:12px;
    font-weight:900;
}}
.family-bottom-nav {{
    position:sticky;
    bottom:16px;
    z-index:5;
    margin:26px 12px 0;
    min-height:66px;
    border-radius:999px;
    background:rgba(255,255,255,.92);
    box-shadow:0 18px 42px rgba(0,0,0,.14);
    display:grid;
    grid-template-columns:repeat(4, 1fr);
    align-items:center;
    backdrop-filter:blur(10px);
}}
.family-nav-item {{
    min-width:0;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    gap:4px;
    color:#8f8580 !important;
    font-size:10px;
    font-weight:1000;
}}
.family-nav-item span {{
    font-size:8px;
    line-height:1;
    letter-spacing:.4px;
}}
.family-nav-item.active {{
    color:#111 !important;
}}
@media (min-width:760px) {{
    .block-container {{
        border-radius:34px;
        margin-top:22px !important;
        box-shadow:0 24px 64px rgba(0,0,0,.10);
    }}
}}
@media (max-width:520px) {{
    .block-container {{ padding:16px 14px 24px !important; }}
    .family-today-copy {{ grid-template-columns:1fr; }}
    .family-play-button {{ width:100%; }}
    .family-art-row {{ height:170px; }}
    .family-photo-panel {{ width:170px; height:138px; }}
    .family-record {{ width:134px; height:134px; }}
}}
</style>
<div class="family-shell">
  <div class="family-top">
    <div class="family-logo">{HOME_LOGO_HTML}</div>
    <div class="family-top-icons">
      <div class="family-icon">!</div>
      <div class="family-icon">박</div>
    </div>
  </div>
  <div class="family-greeting">
    <h1>안녕하세요, 박일성님</h1>
    <p>오늘도 리플레이와 함께해요</p>
  </div>

  <div class="family-section-head">
    <div class="family-section-title">오늘의 플레이리스트</div>
    <div class="family-section-count">{total_count}개의 추억</div>
  </div>
  <section class="family-today-card">
    <div class="family-art-row">
      <div class="family-photo-panel">{latest_photo}</div>
      <div class="family-record"></div>
    </div>
    <div class="family-today-copy">
      <div>
        <div class="family-today-label">Shared Memory</div>
        <div class="family-today-title">{latest_title}</div>
        <div class="family-today-meta">{latest_count_text}<br>{latest_track}</div>
      </div>
      <a class="family-play-button" href="{latest_href}" target="_self">재생하기</a>
    </div>
  </section>

  <div class="family-section-head">
    <div class="family-section-title">최근 재생</div>
    <div class="family-section-count">가족이 함께 보는 중</div>
  </div>
  {recent_html}

  <div id="family-members" class="family-section-head">
    <div class="family-section-title">가족과 공유 중</div>
    <div class="family-section-count">4명</div>
  </div>
  <section class="family-share-card">
    {members_html}
  </section>

  <div class="family-section-head">
    <div class="family-section-title">추천 플레이리스트</div>
    <div class="family-section-count">카테고리별</div>
  </div>
  {playlist_html}

  <nav class="family-bottom-nav">
    <a class="family-nav-item active" href="?action=family_home" target="_self"><span>HOME</span>홈</a>
    <div class="family-nav-item"><span>SEARCH</span>검색</div>
    <a class="family-nav-item" href="?action=family_music" target="_self"><span>MUSIC</span>내 음악</a>
    <a class="family-nav-item" href="#family-members" target="_self"><span>FAMILY</span>가족</a>
  </nav>
</div>
""")

elif False and page == "home":
    home_memories = load_memories()
    memory_count = len(home_memories)
    current_tab = st.session_state.get("home_tab", "album")
    active_album = current_tab == "album"
    active_music = current_tab == "music"
    active_album_color = "#ff4b16" if active_album else "#555"
    active_music_color = "#ff4b16" if active_music else "#555"
    album_underline = "#ff4b16" if active_album else "transparent"
    music_underline = "#ff4b16" if active_music else "transparent"
    html(f"""
<style>
.stApp {{ background:#efefef !important; }}
.block-container {{
    max-width:980px !important;
    min-height:690px !important;
    margin:0 auto !important;
    padding:32px 34px 118px !important;
    background:
      radial-gradient(circle at 55% 0%, rgba(255,119,54,.25), transparent 30%),
      linear-gradient(180deg, #fff7f2 0%, #f4f4f4 38%, #f7f7f7 100%) !important;
    overflow:hidden !important;
    position:relative !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
div[data-testid="stVerticalBlock"] {{ gap:.55rem !important; }}
.home-top {{ display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:30px; }}
.home-logo {{ font-size:18px; font-weight:1000; letter-spacing:-1px; line-height:.82; }}
.home-logo small {{ display:block; font-size:6px; letter-spacing:4px; margin-top:5px; }}
.home-search {{ width:126px; height:46px; border-radius:999px; background:#fff; box-shadow:0 12px 30px rgba(0,0,0,.08); display:flex; align-items:center; justify-content:flex-end; padding-right:25px; }}
.home-search::before {{ content:""; width:14px; height:14px; border:3px solid #111; border-radius:50%; display:block; }}
.home-search::after {{ content:""; width:9px; height:3px; background:#111; transform:rotate(45deg) translate(-1px, 9px); border-radius:99px; display:block; margin-left:-3px; }}
.home-title-row {{ display:flex; align-items:end; justify-content:space-between; margin:0 42px 12px; }}
.home-title {{ font-size:32px; font-weight:1000; letter-spacing:-1px; }}
.home-count {{ font-size:17px; font-weight:700; color:#444; padding-bottom:6px; }}
.home-divider {{ height:1px; background:#d6d6d6; margin:0 42px 28px; }}
.st-key-home_tab_album button,
.st-key-home_tab_music button {{
    height:42px !important;
    min-height:42px !important;
    padding:0 4px !important;
    border:0 !important;
    border-radius:0 !important;
    background:transparent !important;
    font-size:18px !important;
    font-weight:1000 !important;
}}
.st-key-home_tab_album button {{ color:{active_album_color} !important; box-shadow:inset 0 -3px 0 {album_underline} !important; }}
.st-key-home_tab_music button {{ color:{active_music_color} !important; box-shadow:inset 0 -3px 0 {music_underline} !important; }}
.home-card {{
    min-height:274px;
    border-radius:15px;
    background:#fff;
    box-shadow:0 16px 30px rgba(0,0,0,.07);
    padding:9px 9px 14px;
    overflow:hidden;
}}
.home-card-photo {{ height:164px; border-radius:10px; background:#ededed; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#8a8a8a; font-size:22px; }}
.home-card-photo img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.home-card-title {{ margin:15px 7px 0; font-size:16px; line-height:1.25; font-weight:900; color:#111; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.home-card-note {{ margin:15px 7px 0; font-size:12px; line-height:1.4; color:#777; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.home-add-card {{ min-height:274px; border-radius:15px; background:rgba(255,255,255,.72); border:2px dashed rgba(0,0,0,.08); display:flex; align-items:center; justify-content:center; color:#222; font-size:42px; font-weight:300; box-shadow:0 16px 30px rgba(0,0,0,.04); }}
.home-empty {{ min-height:250px; display:flex; flex-direction:column; align-items:center; justify-content:center; color:#777; font-size:15px; text-align:center; }}
.home-music-card {{ border-radius:15px; background:#fff; box-shadow:0 16px 30px rgba(0,0,0,.07); padding:14px; min-height:265px; }}
.home-music-cover {{ width:100%; aspect-ratio:1/1; border-radius:10px; background:#eee; overflow:hidden; display:flex; align-items:center; justify-content:center; color:#999; font-size:24px; }}
.home-music-cover img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.home-music-title {{ margin-top:12px; font-size:17px; font-weight:1000; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.home-music-artist {{ margin-top:5px; font-size:13px; color:#777; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.home-music-link {{ margin-top:8px; font-size:12px; color:#ef5a28; font-weight:900; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.st-key-home_open_0 button, .st-key-home_open_1 button, .st-key-home_open_2 button, .st-key-home_open_3 button, .st-key-home_open_4 button, .st-key-home_open_5 button,
.st-key-home_open_6 button, .st-key-home_open_7 button, .st-key-home_open_8 button, .st-key-home_open_9 button, .st-key-home_open_10 button, .st-key-home_open_11 button,
.st-key-home_open_12 button, .st-key-home_open_13 button, .st-key-home_open_14 button, .st-key-home_open_15 button,
.st-key-home_music_edit_0 button, .st-key-home_music_edit_1 button, .st-key-home_music_edit_2 button, .st-key-home_music_edit_3 button, .st-key-home_music_edit_4 button, .st-key-home_music_edit_5 button,
.st-key-home_add_button button, .st-key-home_music_add_button button {{
    height:38px !important;
    border-radius:999px !important;
    background:#111 !important;
    color:#fff !important;
    box-shadow:0 10px 18px rgba(0,0,0,.10) !important;
    font-weight:900 !important;
}}
.st-key-home_edit_0 button, .st-key-home_edit_1 button, .st-key-home_edit_2 button, .st-key-home_edit_3 button, .st-key-home_edit_4 button, .st-key-home_edit_5 button,
.st-key-home_edit_6 button, .st-key-home_edit_7 button, .st-key-home_edit_8 button, .st-key-home_edit_9 button, .st-key-home_edit_10 button, .st-key-home_edit_11 button,
.st-key-home_edit_12 button, .st-key-home_edit_13 button, .st-key-home_edit_14 button, .st-key-home_edit_15 button {{
    height:38px !important;
    width:48px !important;
    border-radius:10px !important;
    background:#333 !important;
    color:#fff !important;
    box-shadow:none !important;
    font-size:16px !important;
}}
.st-key-home_music_delete_0 button, .st-key-home_music_delete_1 button, .st-key-home_music_delete_2 button, .st-key-home_music_delete_3 button, .st-key-home_music_delete_4 button, .st-key-home_music_delete_5 button {{
    height:38px !important;
    border-radius:999px !important;
    background:#fff !important;
    color:#d94a35 !important;
    border:1px solid rgba(217,74,53,.25) !important;
    box-shadow:0 8px 16px rgba(0,0,0,.06) !important;
}}
.st-key-home_record_footer, .st-key-home_play_footer {{ position:fixed !important; bottom:54px !important; z-index:30 !important; width:360px !important; }}
.st-key-home_record_footer {{ left:calc(50vw - 430px) !important; }}
.st-key-home_play_footer {{ right:calc(50vw - 430px) !important; }}
.st-key-home_record_footer button, .st-key-home_play_footer button {{ height:64px !important; border-radius:22px !important; font-size:20px !important; font-weight:1000 !important; box-shadow:0 14px 30px rgba(0,0,0,.12) !important; }}
.st-key-home_record_footer button {{ background:#111 !important; color:#fff !important; }}
.st-key-home_play_footer button {{ background:#fff !important; color:#111 !important; }}
@media (max-width:900px) {{
    .block-container {{ max-width:820px !important; padding:28px 26px 112px !important; }}
    .home-title-row, .home-divider {{ margin-left:28px; margin-right:28px; }}
    .st-key-home_record_footer, .st-key-home_play_footer {{ position:static !important; width:auto !important; }}
}}
</style>
<div class="home-top">
  <div class="home-logo">Re:Play<small>MEMORY</small></div>
  <div class="home-search"></div>
</div>
<div class="home-title-row">
  <div class="home-title">우리 가족</div>
  <div class="home-count">{memory_count}장의 추억 보관중</div>
</div>
""")
    tab_left, tab_mid, tab_fill = st.columns([0.08, 0.08, 0.84])
    with tab_left:
        if st.button("앨범", key="home_tab_album"):
            st.session_state.home_tab = "album"
            st.rerun()
    with tab_mid:
        if st.button("음악", key="home_tab_music"):
            st.session_state.home_tab = "music"
            st.rerun()
    html('<div class="home-divider"></div>')

    if current_tab == "album":
        if not home_memories:
            html('<div class="home-empty">아직 저장된 추억이 없어요.<br>아래 버튼으로 첫 기억을 기록해보세요.</div>')
        card_items = home_memories[:15] + [None]
        for row_start in range(0, len(card_items), 3):
            cols = st.columns(3, gap="large")
            for offset, memory in enumerate(card_items[row_start:row_start + 3]):
                index = row_start + offset
                with cols[offset]:
                    if memory is None:
                        html('<div class="home-add-card">+</div>')
                        if st.button("새 기억 추가", key="home_add_button", use_container_width=True):
                            reset_flow()
                            go("scan_upload")
                        continue
                    photo_src = image_src(photo_path_for_memory(memory))
                    photo_html = f'<img src="{escape(photo_src, quote=True)}">' if photo_src else '<span>▧</span>'
                    title = escape(memory_title(memory))
                    preview = escape(memory_note_preview(memory))
                    html(f"""
<div class="home-card">
  <div class="home-card-photo">{photo_html}</div>
  <div class="home-card-title">{title}</div>
  <div class="home-card-note">{preview}</div>
</div>
""")
                    open_col, edit_col = st.columns([0.78, 0.22], gap="small")
                    with open_col:
                        if st.button("카드 열기", key=f"home_open_{index}", use_container_width=True):
                            open_memory_from_home(memory.get("id"))
                            st.rerun()
                    with edit_col:
                        if st.button("✎", key=f"home_edit_{index}", use_container_width=True):
                            open_memory_edit(memory.get("id"))
                            st.rerun()
    else:
        music_memories = [memory for memory in home_memories if memory.get("selected_track")]
        if not music_memories:
            html('<div class="home-empty">아직 연결된 음악이 없어요.<br>기억을 기록하면서 음악을 골라보세요.</div>')
            if st.button("음악 추가하기", key="home_music_add_button", use_container_width=True):
                reset_flow()
                go("music")
        for row_start in range(0, len(music_memories), 3):
            cols = st.columns(3, gap="large")
            for offset, memory in enumerate(music_memories[row_start:row_start + 3]):
                index = row_start + offset
                track = memory.get("selected_track") or {}
                cover_src = track.get("image") or ""
                cover_html = f'<img src="{escape(cover_src, quote=True)}">' if cover_src else '<span>♪</span>'
                title = escape(track.get("title") or "노래 제목")
                artist = escape(track.get("artist") or "가수")
                linked_title = escape(memory_title(memory))
                with cols[offset]:
                    html(f"""
<div class="home-music-card">
  <div class="home-music-cover">{cover_html}</div>
  <div class="home-music-title">{title}</div>
  <div class="home-music-artist">{artist}</div>
  <div class="home-music-link">{linked_title}</div>
</div>
""")
                    if not render_track_audio(track):
                        html('<div style="font-size:11px;color:#777;font-weight:800;margin-top:6px;">음원 파일을 찾지 못했어요.</div>')
                    edit_col, delete_col = st.columns(2)
                    with edit_col:
                        if st.button("수정", key=f"home_music_edit_{index}", use_container_width=True):
                            open_music_edit(memory.get("id"))
                            st.rerun()
                    with delete_col:
                        if st.button("삭제", key=f"home_music_delete_{index}", use_container_width=True):
                            clear_memory_track(memory.get("id"))
                            st.rerun()

    footer_record, footer_play = st.columns(2)
    with footer_record:
        if st.button("기억 기록하기", key="home_record_footer", use_container_width=True):
            reset_flow()
            go("scan_upload")
    with footer_play:
        if st.button("추억 재생하기", key="home_play_footer", use_container_width=True):
            if home_memories:
                picked = home_memories[0]
                categories = memory_categories(picked)
                open_category_album(categories[0] if categories else "", picked.get("id"))
                st.rerun()
            reset_flow()
            go("scan_upload")

elif page == "memory_edit":
    edit_id = st.session_state.get("editing_memory_id") or st.session_state.get("selected_memory_id")
    edit_memory = next((memory for memory in load_memories() if memory.get("id") == edit_id), None)
    if not edit_memory:
        st.session_state.page = "home"
        st.rerun()
    photo_src = image_src(photo_path_for_memory(edit_memory))
    photo_html = f'<img src="{escape(photo_src, quote=True)}">' if photo_src else '<span>▧</span>'
    edit_categories = memory_categories(edit_memory)
    html("""
<style>
.stApp { background:#efefef !important; }
.block-container {
    max-width:900px !important;
    min-height:560px !important;
    margin:20px auto 0 !important;
    padding:34px 46px 76px !important;
    background:radial-gradient(circle at 84% 0%, rgba(255,119,54,.20), transparent 32%), #fffaf7 !important;
    overflow:visible !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
.edit-head { max-width:720px; margin:0 auto 22px; display:flex; align-items:center; justify-content:space-between; }
.edit-title { font-size:26px; font-weight:1000; }
.edit-layout { max-width:720px; margin:0 auto 18px; }
.edit-photo { width:360px; max-width:100%; height:230px; border-radius:16px; background:#eee; display:flex; align-items:center; justify-content:center; overflow:hidden; box-shadow:0 12px 24px rgba(0,0,0,.07); }
.edit-photo img { width:100%; height:100%; object-fit:cover; display:block; }
.edit-hint { width:360px; max-width:100%; margin-top:14px; color:#777; font-size:12px; line-height:1.5; }
div[data-testid="stTextInput"],
div[data-testid="stTextArea"] { max-width:720px; margin-left:auto !important; margin-right:auto !important; }
div[data-testid="stTextInput"] label,
div[data-testid="stTextArea"] label { font-size:13px !important; font-weight:900 !important; color:#111 !important; }
div[data-testid="stTextInput"] input,
div[data-testid="stTextArea"] textarea { border:0 !important; border-radius:12px !important; background:#f5f6f7 !important; box-shadow:none !important; font-size:15px !important; }
div[data-testid="stTextInput"] input { height:48px !important; }
div[data-testid="stTextArea"] textarea { min-height:140px !important; resize:none !important; }
.st-key-edit_save_button button { background:#111 !important; color:#fff !important; height:48px !important; border-radius:999px !important; }
.st-key-edit_delete_button button { background:#fff !important; color:#d94a35 !important; height:48px !important; border-radius:999px !important; border:1px solid rgba(217,74,53,.24) !important; }
</style>
<div class="edit-head"><div class="edit-title">추억 수정하기</div></div>
""")
    html(f"""
<div class="edit-layout">
  <div>
    <div class="edit-photo">{photo_html}</div>
    <div class="edit-hint">사진은 그대로 두고 제목, 기록, 카테고리만 수정할 수 있어요.</div>
  </div>
</div>
""")
    title_value = st.text_input("제목", value=str(edit_memory.get("title") or ""), key=f"edit_title_{edit_id}")
    note_value = st.text_area("기록 내용", value=str(memory_record_text(edit_memory, include_questions=True) or ""), key=f"edit_note_{edit_id}", height=140)
    category_value = st.text_input(
        "카테고리",
        value=", ".join(edit_categories),
        key=f"edit_category_{edit_id}",
        help="여러 개면 쉼표로 나눠 적어주세요.",
    )
    html("")
    save_col, delete_col = st.columns(2)
    with save_col:
        if st.button("저장하기", key="edit_save_button", use_container_width=True):
            categories = parse_category_text(category_value)
            edit_memory["title"] = title_value.strip()
            edit_memory["note"] = note_value
            edit_memory["categories"] = categories
            edit_memory["category"] = ", ".join(categories)
            edit_memory["summary"] = summarize_memory_with_ai(edit_memory)
            save_memory_record(edit_memory)
            st.session_state.play_memory = edit_memory
            st.session_state.selected_memory_id = edit_id
            st.session_state.editing_memory_id = None
            st.session_state.edit_mode = "create"
            st.session_state.page = "home"
            st.rerun()
    with delete_col:
        if st.button("삭제하기", key="edit_delete_button", use_container_width=True):
            delete_memory(edit_id)
            st.session_state.editing_memory_id = None
            st.session_state.edit_mode = "create"
            st.session_state.page = "home"
            st.rerun()

elif page == "scan_upload":
    has_photo = media_reference_available(st.session_state.get("photo_path"))
    photo_src = image_src(st.session_state.photo_path) if has_photo else ""
    photo_background = f'background-image:url("{escape(photo_src, quote=True)}") !important;' if photo_src else ""
    scan_hint = "" if photo_src else "사진을 아래 스캔 공간 위에 올려주세요"
    scan_icon_display = "display:none !important;" if photo_src else ""
    html(f"""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {{
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    max-height:100vh !important;
    margin:0 !important;
    overflow:hidden !important;
}}
*::-webkit-scrollbar {{
    display:none !important;
    width:0 !important;
    height:0 !important;
}}
* {{
    scrollbar-width:none !important;
}}
.stApp {{ background:#7c7c7a !important; }}
.stApp .block-container {{
    width:min(1180px, calc(100vw - 32px)) !important;
    max-width:min(1180px, calc(100vw - 32px)) !important;
    height:min(760px, calc(100vh - 32px)) !important;
    min-height:min(760px, calc(100vh - 32px)) !important;
    max-height:min(760px, calc(100vh - 32px)) !important;
    margin:16px auto !important;
    padding:34px 56px 74px !important;
    background:
      radial-gradient(circle at 50% 60%, rgba(255,126,45,.10), transparent 34%),
      #fafafa !important;
    overflow:hidden !important;
    border-radius:32px !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
div[data-testid="stVerticalBlock"] {{ gap:0 !important; }}
.scan-page-title {{
    display:none !important;
}}
.scan-upload-topbar {{
    display:flex;
    align-items:center;
    justify-content:flex-end;
    min-height:42px;
    margin-bottom:64px;
}}
.scan-upload-guide {{
    height:38px;
    min-width:82px;
    padding:0 16px;
    border-radius:999px;
    background:#fff;
    box-shadow:0 8px 18px rgba(0,0,0,.10);
    display:flex;
    align-items:center;
    justify-content:center;
    gap:7px;
    font-size:13px;
    color:#111;
    font-weight:900;
}}
.scan-upload-guide .guide-icon {{
    width:13px;
    height:13px;
    display:inline-block;
    border:2px solid #111;
    border-radius:3px;
    position:relative;
}}
.scan-upload-guide .guide-icon::after {{
    content:"";
    position:absolute;
    left:50%;
    top:2px;
    bottom:2px;
    width:1.5px;
    background:#111;
    transform:translateX(-50%);
}}
.stApp .st-key-scan_photo_uploader {{
    width:min(690px, 66vw) !important;
    max-width:690px !important;
    height:350px !important;
    min-height:350px !important;
    max-height:350px !important;
    margin:0 auto !important;
    padding:22px 26px !important;
    border-radius:18px !important;
    background:#fff !important;
    box-shadow:0 18px 42px rgba(255,126,45,.12) !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
    position:relative !important;
    overflow:visible !important;
}}
.st-key-scan_photo_uploader::after {{
    content:"";
    position:absolute;
    left:50%;
    top:58%;
    width:28px;
    height:28px;
    transform:translate(-50%, -50%);
    background:
      linear-gradient(#ff7b2d, #ff7b2d) left top / 11px 2px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) left top / 2px 11px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) right top / 11px 2px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) right top / 2px 11px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) left bottom / 11px 2px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) left bottom / 2px 11px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) right bottom / 11px 2px no-repeat,
      linear-gradient(#ff7b2d, #ff7b2d) right bottom / 2px 11px no-repeat;
    pointer-events:none;
    z-index:35;
    {scan_icon_display}
}}
.stApp .st-key-scan_photo_uploader div[data-testid="stFileUploader"] {{
    width:100% !important;
    max-width:100% !important;
    height:306px !important;
    min-height:306px !important;
    max-height:306px !important;
    margin:0 !important;
    opacity:1 !important;
    position:static !important;
    z-index:20 !important;
}}
.stApp .st-key-scan_photo_uploader section,
.stApp .st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"] {{
    width:100% !important;
    height:306px !important;
    min-height:306px !important;
    max-height:306px !important;
    margin:0 !important;
    padding:0 !important;
    border:0 !important;
    border-radius:4px !important;
    background-color:#f0f0f0 !important;
    {photo_background}
    background-size:contain !important;
    background-position:center !important;
    background-repeat:no-repeat !important;
    cursor:pointer !important;
    position:relative !important;
    overflow:hidden !important;
    box-shadow:none !important;
}}
.st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"]::before {{
    content:"";
    position:absolute;
    inset:14px;
    border:2px solid #bdbdbd;
    border-radius:0;
    clip-path:polygon(
        0 0, 34px 0, 34px 2px, 2px 2px, 2px 34px, 0 34px,
        0 100%, 34px 100%, 34px calc(100% - 2px), 2px calc(100% - 2px), 2px calc(100% - 34px), 0 calc(100% - 34px),
        100% 100%, calc(100% - 34px) 100%, calc(100% - 34px) calc(100% - 2px), calc(100% - 2px) calc(100% - 2px), calc(100% - 2px) calc(100% - 34px), 100% calc(100% - 34px),
        100% 0, calc(100% - 34px) 0, calc(100% - 34px) 2px, calc(100% - 2px) 2px, calc(100% - 2px) 34px, 100% 34px
    );
    pointer-events:none;
    z-index:2;
}}
.st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"]::after {{
    content:"{scan_hint}";
    position:absolute;
    left:0;
    right:0;
    top:50%;
    height:24px;
    display:flex;
    align-items:center;
    justify-content:center;
    color:#111;
    font-size:15px;
    font-weight:800;
    transform:translateY(-34px);
    pointer-events:none;
    z-index:3;
}}
.st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"] * {{
    opacity:0 !important;
    color:transparent !important;
    background:transparent !important;
    border:0 !important;
    box-shadow:none !important;
    pointer-events:none !important;
}}
.st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"] button,
.st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"] [role="button"] {{
    width:0 !important;
    height:0 !important;
    min-width:0 !important;
    min-height:0 !important;
    padding:0 !important;
    margin:0 !important;
    overflow:hidden !important;
}}
.st-key-scan_photo_uploader input[type="file"] {{
    position:absolute !important;
    inset:0 !important;
    display:block !important;
    width:100% !important;
    height:100% !important;
    opacity:0 !important;
    cursor:pointer !important;
    z-index:30 !important;
    pointer-events:auto !important;
}}
.st-key-scan_photo_uploader label,
.st-key-scan_photo_uploader small,
.st-key-scan_photo_uploader [data-testid="stFileUploaderFile"],
.st-key-scan_photo_uploader [data-testid="stFileUploaderFileData"] {{
    display:none !important;
}}
.scan-upload-down {{
    display:none;
}}
.stApp .st-key-scan_start_button {{
    width:330px !important;
    margin:52px auto 0 !important;
}}
.stApp .st-key-scan_start_button button {{
    width:100% !important;
    height:76px !important;
    min-height:76px !important;
    border-radius:8px !important;
    border:0 !important;
    background:#050505 !important;
    color:#fff !important;
    box-shadow:0 16px 26px rgba(0,0,0,.24) !important;
    font-size:22px !important;
    font-weight:1000 !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
}}
.stApp .st-key-scan_start_button button::before {{
    content:"";
    width:24px;
    height:20px;
    margin-right:44px;
    border:2px solid #ff7b2d;
    border-radius:5px;
    background:
      radial-gradient(circle at 50% 55%, transparent 0 4px, #ff7b2d 4px 5.5px, transparent 5.5px),
      linear-gradient(#ff7b2d, #ff7b2d) 50% -2px / 10px 5px no-repeat;
    display:inline-block;
    box-sizing:border-box;
}}
@media (max-width:900px) {{
    .stApp .block-container {{
        width:min(900px, calc(100vw - 24px)) !important;
        max-width:min(900px, calc(100vw - 24px)) !important;
        height:min(640px, calc(100vh - 24px)) !important;
        min-height:min(640px, calc(100vh - 24px)) !important;
        max-height:min(640px, calc(100vh - 24px)) !important;
        margin:12px auto !important;
        padding:28px 38px 64px !important;
    }}
    .scan-page-title {{ left:14px; }}
    .scan-upload-topbar {{ margin-bottom:50px; }}
    .stApp .st-key-scan_photo_uploader {{
        width:min(600px, 74vw) !important;
        height:320px !important;
        min-height:320px !important;
        max-height:320px !important;
        padding:20px 22px !important;
    }}
    .stApp .st-key-scan_photo_uploader div[data-testid="stFileUploader"],
    .stApp .st-key-scan_photo_uploader section,
    .stApp .st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"] {{
        height:280px !important;
        min-height:280px !important;
        max-height:280px !important;
    }}
}}
@media (max-width:640px) {{
    .stApp .block-container {{
        width:100vw !important;
        max-width:100vw !important;
        height:520px !important;
        min-height:520px !important;
        max-height:520px !important;
        margin:0 auto !important;
        padding:24px 24px 56px !important;
    }}
    .scan-upload-topbar {{ margin-bottom:36px; }}
    .stApp .st-key-scan_photo_uploader {{
        width:calc(100vw - 84px) !important;
        max-width:520px !important;
        height:290px !important;
        min-height:290px !important;
        max-height:290px !important;
        padding:18px !important;
        border-radius:13px !important;
    }}
    .stApp .st-key-scan_photo_uploader div[data-testid="stFileUploader"],
    .stApp .st-key-scan_photo_uploader section,
    .stApp .st-key-scan_photo_uploader [data-testid="stFileUploaderDropzone"] {{
        height:254px !important;
        min-height:254px !important;
        max-height:254px !important;
    }}
    .stApp .st-key-scan_start_button {{
        width:250px !important;
        margin-top:38px !important;
    }}
    .stApp .st-key-scan_start_button button {{
        height:60px !important;
        min-height:60px !important;
        font-size:18px !important;
    }}
}}
</style>
<div class="scan-page-title">AI 사진 분석</div>
<div class="scan-upload-topbar">
  <div class="scan-upload-guide"><span class="guide-icon"></span>가이드</div>
</div>
""")
    file = st.file_uploader(
        "사진 선택",
        type=["jpg", "jpeg", "png"],
        label_visibility="collapsed",
        key="scan_photo_uploader",
    )
    if file:
        signature = f"{file.name}:{file.size}"
        if signature != st.session_state.upload_signature:
            st.session_state.upload_signature = signature
            st.session_state.scan_upload_notice = ""
            st.session_state.analysis_done = False
            st.session_state.suggested_categories = []
            st.session_state.music_query = ""
            save_photo(file)
            st.rerun()
    html('<div class="scan-upload-down">▢</div>')
    if st.button("스캔하기", key="scan_start_button"):
        if media_reference_available(st.session_state.get("photo_path")):
            go("scan_running")
        else:
            st.session_state.scan_upload_notice = "사진을 먼저 선택해주세요."
    if st.session_state.get("scan_upload_notice"):
        st.warning(st.session_state.scan_upload_notice)

elif page == "scan_running":
    # 스캔 중에도 화면 비율이 바뀌지 않도록 scan_upload 화면과 같은 틀을 그대로 사용한다.
    # 업로더만 숨기고, 같은 스캔 영역 안에서 사진이 스캔되는 것처럼 보이게 처리한다.
    html("""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    max-height:100vh !important;
    margin:0 !important;
    overflow:hidden !important;
}
*::-webkit-scrollbar {
    display:none !important;
    width:0 !important;
    height:0 !important;
}
* {
    scrollbar-width:none !important;
}
.stApp {
    background:#7c7c7a !important;
    color:#111 !important;
}
.stApp .block-container {
    width:min(1180px, calc(100vw - 32px)) !important;
    max-width:min(1180px, calc(100vw - 32px)) !important;
    height:min(760px, calc(100vh - 32px)) !important;
    min-height:min(760px, calc(100vh - 32px)) !important;
    max-height:min(760px, calc(100vh - 32px)) !important;
    margin:16px auto !important;
    padding:34px 56px 74px !important;
    background:
      radial-gradient(circle at 50% 60%, rgba(255,126,45,.10), transparent 34%),
      #fafafa !important;
    overflow:hidden !important;
    border-radius:32px !important;
    position:relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
    display:none !important;
}
div[data-testid="stVerticalBlock"] {
    gap:0 !important;
}

/* scan_running: 이전 scan_upload 잔상 제거 */
div[data-testid="stFileUploader"],
div[data-testid="stFileUploaderDropzone"],
[data-testid="stFileUploaderDropzone"],
[data-testid="stFileUploaderFile"],
.st-key-scan_photo_uploader,
.st-key-scan_photo_uploader *,
.st-key-scan_start_button,
.st-key-scan_start_button *,
.scan-upload-button-shell,
.scan-upload-button-shell *,
.scan-upload-button-shell button,
button[kind="secondaryFormSubmit"] {
    display:none !important;
    visibility:hidden !important;
    opacity:0 !important;
    width:0 !important;
    height:0 !important;
    min-width:0 !important;
    min-height:0 !important;
    max-width:0 !important;
    max-height:0 !important;
    margin:0 !important;
    padding:0 !important;
    overflow:hidden !important;
    pointer-events:none !important;
    position:absolute !important;
    left:-9999px !important;
    top:-9999px !important;
}

.scan-running-topbar {
    display:flex;
    align-items:center;
    justify-content:flex-end;
    min-height:42px;
    margin-bottom:64px;
}
.scan-running-guide {
    height:38px;
    min-width:82px;
    padding:0 16px;
    border-radius:999px;
    background:#fff;
    box-shadow:0 8px 18px rgba(0,0,0,.10);
    display:flex;
    align-items:center;
    justify-content:center;
    gap:7px;
    font-size:13px;
    color:#111;
    font-weight:900;
}
.scan-running-guide .guide-icon {
    width:13px;
    height:13px;
    display:inline-block;
    border:2px solid #111;
    border-radius:3px;
    position:relative;
}
.scan-running-guide .guide-icon::after {
    content:"";
    position:absolute;
    left:50%;
    top:2px;
    bottom:2px;
    width:1.5px;
    background:#111;
    transform:translateX(-50%);
}
.scan-running-uploader {
    width:min(690px, 66vw);
    max-width:690px;
    height:350px;
    min-height:350px;
    max-height:350px;
    margin:0 auto;
    padding:22px 26px;
    border-radius:18px;
    background:#fff;
    box-shadow:0 18px 42px rgba(255,126,45,.12);
    display:flex;
    align-items:center;
    justify-content:center;
    position:relative;
    overflow:hidden;
}
.scan-running-box {
    width:100%;
    height:100%;
    position:relative;
    overflow:hidden;
    background:#f7f7f7;
}
.scan-running-box::before,
.scan-running-box::after {
    content:"";
    position:absolute;
    width:34px;
    height:34px;
    border-color:#cfcfcf;
    z-index:3;
}
.scan-running-box::before {
    left:0;
    top:0;
    border-left:2px solid #cfcfcf;
    border-top:2px solid #cfcfcf;
}
.scan-running-box::after {
    right:0;
    bottom:0;
    border-right:2px solid #cfcfcf;
    border-bottom:2px solid #cfcfcf;
}
.scan-running-corner-rt,
.scan-running-corner-lb {
    position:absolute;
    width:34px;
    height:34px;
    border-color:#cfcfcf;
    z-index:3;
}
.scan-running-corner-rt {
    right:0;
    top:0;
    border-right:2px solid #cfcfcf;
    border-top:2px solid #cfcfcf;
}
.scan-running-corner-lb {
    left:0;
    bottom:0;
    border-left:2px solid #cfcfcf;
    border-bottom:2px solid #cfcfcf;
}
.scan-running-photo {
    position:absolute;
    inset:16px;
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
}
.scan-running-photo img {
    width:100%;
    height:100%;
    object-fit:cover;
    filter:blur(6px) saturate(.65);
    opacity:.82;
}
.scan-running-line {
    position:absolute;
    left:16px;
    right:16px;
    height:3px;
    background:#ff7b2d;
    box-shadow:0 0 18px rgba(255,123,45,.85);
    z-index:4;
}
.scan-running-label {
    position:absolute;
    left:0;
    right:0;
    bottom:-58px;
    text-align:center;
    color:#111;
    font-size:14px;
    font-weight:1000;
}
</style>
""")
    progress_area = st.empty()
    for percent in range(0, 101, 5):
        photo = image_src(st.session_state.get("photo_path"))
        img = f'<img src="{photo}">' if photo else ''
        progress_area.markdown(f"""
<div class="scan-running-topbar">
  <div class="scan-running-guide"><span class="guide-icon"></span> 가이드</div>
</div>
<div class="scan-running-uploader">
  <div class="scan-running-box">
    <div class="scan-running-corner-rt"></div>
    <div class="scan-running-corner-lb"></div>
    <div class="scan-running-photo">{img}</div>
    <div class="scan-running-line" style="top:{percent}%;"></div>
  </div>
</div>
<div class="scan-running-label">스캔중...</div>
""", unsafe_allow_html=True)
        time.sleep(0.05)
    go("scan_done")

elif page == "analyzing":
    if not media_reference_available(st.session_state.get("photo_path")):
        go("scan_upload")
    html("""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    max-height:100vh !important;
    margin:0 !important;
    overflow:hidden !important;
}
*::-webkit-scrollbar {
    display:none !important;
    width:0 !important;
    height:0 !important;
}
* {
    scrollbar-width:none !important;
}
.stApp { background:#777 !important; }
.block-container {
    max-width:1180px !important;
    min-height:760px !important;
    padding:0 !important;
    background:
      radial-gradient(circle at 92% 100%, rgba(255,105,33,.10), transparent 36%),
      #fbfbfb !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
    overflow:hidden !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
div[data-testid="stVerticalBlock"] { width:100% !important; }
.analysis-wait-text {
    min-height:620px;
    display:flex;
    align-items:center;
    justify-content:center;
    color:#111;
    font-size:20px;
    font-weight:900;
}
</style>
<div class="analysis-wait-text">사진 분석 중..</div>
""")
    time.sleep(0.6)
    update_photo_analysis()
    go("write")

elif page == "scan_done":
    if not media_reference_available(st.session_state.get("photo_path")):
        go("scan_upload")
    src = image_src(st.session_state.photo_path)
    img = f'<img src="{src}">' if src else ""
    html(f"""
<style>
html,
body,
.stApp,
.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stMain"],
.stApp [data-testid="stMainBlockContainer"] {{
    width:100vw !important;
    height:100vh !important;
    min-height:100vh !important;
    max-height:100vh !important;
    margin:0 !important;
    overflow:hidden !important;
}}
*::-webkit-scrollbar {{
    display:none !important;
    width:0 !important;
    height:0 !important;
}}
* {{
    scrollbar-width:none !important;
}}
.stApp {{ background:#777 !important; }}
.block-container {{
    max-width:1180px !important;
    min-height:760px !important;
    padding:54px 56px 82px !important;
    background:
      radial-gradient(circle at 91% 100%, rgba(255,105,33,.12), transparent 38%),
      #fbfbfb !important;
    overflow:hidden !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
div[data-testid="stVerticalBlock"] {{ gap:0 !important; }}
.scan-done-title {{
    text-align:center;
    color:#111;
    font-size:20px;
    font-weight:1000;
    margin:0 0 52px;
}}
.scan-done-card {{
    width:min(680px, 72%) !important;
    max-width:680px;
    margin:0 auto 64px;
    padding:34px 52px;
    border-radius:20px;
    background:#fff;
    border:1px solid rgba(255,112,37,.22);
    box-shadow:0 14px 34px rgba(255,112,37,.20);
}}
.scan-done-photo {{
    width:100%;
    height:clamp(220px, 32dvh, 315px);
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
    background:#f4f4f4;
}}
.scan-done-photo img {{
    width:100%;
    height:100%;
    object-fit:contain;
    display:block;
}}
.scan-done-actions {{
    width:min(620px, 72%);
    margin:0 auto;
}}
.scan-done-actions div[data-testid="stHorizontalBlock"] {{
    gap:72px !important;
}}
.st-key-rescan_button button,
.st-key-scan_done_write_button button {{
    width:100% !important;
    height:58px !important;
    min-height:58px !important;
    border-radius:7px !important;
    border:0 !important;
    font-size:14px !important;
    font-weight:1000 !important;
    box-shadow:0 10px 22px rgba(0,0,0,.10) !important;
}}
.st-key-rescan_button button {{
    background:#050505 !important;
    color:#fff !important;
}}
.st-key-scan_done_write_button button {{
    background:#fff !important;
    color:#111 !important;
}}
.st-key-rescan_button button::before {{
    content:"▣";
    margin-right:10px;
    color:#ff7b2d;
    font-size:16px;
}}
.st-key-scan_done_write_button button::before {{
    content:"✎";
    margin-right:10px;
    color:#111;
    font-size:15px;
}}
@media (max-width:900px) {{
    .block-container {{ padding:42px 34px 74px !important; }}
    .scan-done-card {{ width:82% !important; padding:28px 38px; margin-bottom:48px; }}
    .scan-done-actions {{ width:82%; }}
    .scan-done-actions div[data-testid="stHorizontalBlock"] {{ gap:42px !important; }}
}}
@media (max-width:640px) {{
    .block-container {{ padding:32px 20px 70px !important; }}
    .scan-done-title {{ margin-bottom:32px; }}
    .scan-done-card {{ width:100% !important; padding:20px; margin-bottom:32px; }}
    .scan-done-actions {{ width:100%; }}
    .scan-done-actions div[data-testid="stHorizontalBlock"] {{ gap:16px !important; }}
}}
</style>
<div class="scan-done-title">스캔 완료!</div>
<div class="scan-done-card">
  <div class="scan-done-photo">{img}</div>
</div>
<div class="scan-done-actions">
""")
    rescan_col, write_col = st.columns(2)
    with rescan_col:
        if st.button("다시 스캔하기", key="rescan_button", use_container_width=True):
            reset_scan_inputs()
            go("scan_upload")
    with write_col:
        if st.button("기록하기", key="scan_done_write_button", use_container_width=True):
            go("analyzing")
    html("</div>")

elif page == "write":
    # 기능 우선: 기존 HTML 가짜 버튼/복잡한 CSS를 쓰지 않고 실제 st.button으로만 작동하는 안정 버전
    if not media_reference_available(st.session_state.get("photo_path")) and st.session_state.get("memory_id"):
        restore_photo(st.session_state.memory_id)

    photo_path = active_photo_path(st.session_state.get("play_memory") or {}) or st.session_state.get("photo_path")
    if not media_reference_available(photo_path):
        st.session_state.page = "scan_upload"
        st.rerun()

    if media_reference_available(photo_path) and not st.session_state.get("analysis_done"):
        update_photo_analysis()

    questions = st.session_state.get("questions") or fallback_photo_analysis()["questions"]
    if len(questions) < 3:
        questions = (questions + fallback_photo_analysis()["questions"])[:3]
    questions = questions[:3]

    idx = int(st.session_state.get("current_question_index", 0) or 0)
    idx = max(0, min(len(questions) - 1, idx))
    st.session_state.current_question_index = idx

    mode = st.session_state.get("write_mode", "handwriting")
    if mode == "chat":
        mode = "text"
    if mode not in ("handwriting", "voice", "text"):
        mode = "handwriting"
    st.session_state.write_mode = mode

    answers = ensure_question_answers()
    answer_key = f"question_answer_{idx}"
    if answer_key not in st.session_state:
        st.session_state[answer_key] = answers.get(str(idx), "")

    question = str(questions[idx])
    progress_width = int(((idx + 1) / max(len(questions), 1)) * 100)
    is_last_question = idx >= len(questions) - 1

    pen_color = st.session_state.get("pen_color", "#000000")
    pen_width = int(st.session_state.get("stroke_width", st.session_state.get("pen_width", 3)) or 3)
    st.session_state.pen_width = pen_width
    st.session_state.stroke_width = pen_width
    drawing_tool = st.session_state.get("drawing_tool", "pen")
    stroke_color = "#ffffff" if drawing_tool == "eraser" else pen_color
    stroke_width = max(pen_width * 4, 18) if drawing_tool == "eraser" else pen_width
    canvas_key = write_canvas_key(idx)

    color_name = {
        "#000000": "검정",
        "#1e88e5": "파랑",
        "#44c767": "초록",
        "#f8c51b": "노랑",
        "#ff4a3d": "빨강",
    }.get(str(pen_color).lower(), "선택 색")

    html("""
<style>
html, body, .stApp {
    margin:0 !important;
    width:100vw !important;
    min-height:100vh !important;
    overflow:hidden !important;
}
.stApp {
    background:#777 !important;
    color:#111 !important;
}
.stApp .block-container {
    width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:48px 58px 42px !important;
    overflow:hidden !important;
    border-radius:28px !important;
    background:linear-gradient(135deg, #fbfbfb 0%, #faf9f7 58%, #fff3ef 100%) !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
div[data-testid="stVerticalBlock"] { gap:0.45rem !important; }
.write-stable-status {
    display:flex;
    align-items:center;
    gap:8px;
    margin:2px 0 26px;
    font-size:13px;
    font-weight:900;
}
.write-stable-dot { width:8px; height:8px; border-radius:999px; background:#ef5a28; display:inline-block; }
.write-stable-photo {
    width:430px;
    height:240px;
    overflow:hidden;
    background:#eee;
    margin-bottom:22px;
}
.write-stable-photo img { width:100%; height:100%; object-fit:cover; display:block; }
.write-stable-progress {
    width:430px;
    height:5px;
    background:#d9d9d9;
    border-radius:999px;
    overflow:hidden;
    margin-bottom:28px;
}
.write-stable-progress span { height:100%; display:block; background:#ff6b21; }
.write-stable-question {
    width:430px;
    font-size:30px;
    font-weight:1000;
    line-height:1.22;
    word-break:keep-all;
    margin-bottom:22px;
}
.write-stable-sub {
    width:430px;
    text-align:center;
    font-size:15px;
    font-weight:800;
    margin-bottom:120px;
}
.write-mode-card,
.write-voice-card {
    width:100%;
    height:460px;
    border-radius:12px;
    background:#fff;
    box-shadow:0 8px 18px rgba(0,0,0,.035);
    padding:38px;
}
.write-voice-card {
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    text-align:center;
}
.write-voice-icon { font-size:46px; margin-bottom:14px; }
.write-voice-title { font-size:24px; font-weight:1000; margin-bottom:8px; }
.write-voice-sub { font-size:15px; font-weight:800; color:#777; margin-bottom:24px; }
.stButton button {
    cursor:pointer !important;
}
.st-key-write_mode_voice_btn button,
.st-key-write_mode_handwriting_btn button,
.st-key-write_mode_text_btn button,
.st-key-write_more_btn button {
    height:42px !important;
    min-height:42px !important;
    border-radius:999px !important;
    border:0 !important;
    background:#fff !important;
    color:#111 !important;
    box-shadow:0 8px 18px rgba(0,0,0,.10) !important;
    font-size:14px !important;
    font-weight:900 !important;
    white-space:nowrap !important;
}
.st-key-write_prev_btn button,
.st-key-write_next_btn button,
.st-key-write_done_btn button {
    height:54px !important;
    min-height:54px !important;
    border:0 !important;
    border-radius:9px !important;
    background:#fff !important;
    color:#111 !important;
    box-shadow:0 9px 18px rgba(0,0,0,.09) !important;
    font-size:18px !important;
    font-weight:1000 !important;
}
.st-key-write_done_btn button {
    background:#111 !important;
    color:#fff !important;
}
.write-toolbar-shell { display:none !important; }
.st-key-write_tool_pen_btn button,
.st-key-write_tool_eraser_btn button,
.st-key-write_undo_btn button,
.st-key-write_redo_btn button {
    height:34px !important;
    min-height:34px !important;
    border-radius:999px !important;
    border:0 !important;
    background:transparent !important;
    color:#111 !important;
    box-shadow:none !important;
    font-size:19px !important;
    font-weight:900 !important;
    padding:0 !important;
}
.st-key-write_tool_pen_btn button:hover,
.st-key-write_tool_eraser_btn button:hover,
.st-key-write_undo_btn button:hover,
.st-key-write_redo_btn button:hover {
    background:rgba(0,0,0,.04) !important;
}
.st-key-write_color_black_btn,
.st-key-write_color_blue_btn,
.st-key-write_color_green_btn,
.st-key-write_color_yellow_btn,
.st-key-write_color_red_btn {
    height:38px !important;
    min-height:38px !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
}
.st-key-write_color_black_btn button,
.st-key-write_color_blue_btn button,
.st-key-write_color_green_btn button,
.st-key-write_color_yellow_btn button,
.st-key-write_color_red_btn button {
    width:24px !important;
    min-width:24px !important;
    max-width:24px !important;
    height:24px !important;
    min-height:24px !important;
    max-height:24px !important;
    border-radius:999px !important;
    border:0 !important;
    padding:0 !important;
    margin:0 auto !important;
    transform:translateY(-2px) !important;
    box-shadow:none !important;
    overflow:hidden !important;
}
.st-key-write_color_black_btn button *,
.st-key-write_color_blue_btn button *,
.st-key-write_color_green_btn button *,
.st-key-write_color_yellow_btn button *,
.st-key-write_color_red_btn button * {
    display:none !important;
    font-size:0 !important;
    line-height:0 !important;
    color:transparent !important;
}
.st-key-write_color_black_btn button { background:#000000 !important; }
.st-key-write_color_blue_btn button { background:#1e88e5 !important; }
.st-key-write_color_green_btn button { background:#44c767 !important; }
.st-key-write_color_yellow_btn button { background:#f8c51b !important; }
.st-key-write_color_red_btn button { background:#ff4a3d !important; }
.st-key-write_color_black_btn button:hover,
.st-key-write_color_blue_btn button:hover,
.st-key-write_color_green_btn button:hover,
.st-key-write_color_yellow_btn button:hover,
.st-key-write_color_red_btn button:hover {
    filter:brightness(.96);
}
.st-key-write_width_slider div[data-testid="stSlider"] { padding-top:0 !important; margin-top:-4px !important; }
.st-key-question_answer_0 textarea,
.st-key-question_answer_1 textarea,
.st-key-question_answer_2 textarea {
    height:460px !important;
    min-height:460px !important;
    resize:none !important;
    border-radius:12px !important;
    background:#fff !important;
    box-shadow:0 8px 18px rgba(0,0,0,.035) !important;
    font-size:20px !important;
    line-height:1.55 !important;
    padding:32px !important;
}
.st-key-question_answer_0 label,
.st-key-question_answer_1 label,
.st-key-question_answer_2 label { display:none !important; }
iframe[title*="streamlit_drawable_canvas"] {
    border:0 !important;
    outline:0 !important;
    border-radius:12px !important;
    background:#fff !important;
    box-shadow:0 8px 18px rgba(0,0,0,.035) !important;
    display:block !important;
}
div[data-testid="stIFrame"] {
    border:0 !important;
    outline:0 !important;
    background:transparent !important;
    box-shadow:none !important;
    overflow:visible !important;
    margin-top:8px !important;
}
</style>
""")
    selected_selector_map = {
        "#000000": ".st-key-write_color_black_btn button",
        "#1e88e5": ".st-key-write_color_blue_btn button",
        "#44c767": ".st-key-write_color_green_btn button",
        "#f8c51b": ".st-key-write_color_yellow_btn button",
        "#ff4a3d": ".st-key-write_color_red_btn button",
    }
    selected_selector = selected_selector_map.get(str(pen_color).lower())
    if selected_selector:
        html(f"""
<style>
{selected_selector} {{
    box-shadow:0 0 0 3px #2f80ed, 0 0 0 7px #ffffff !important;
    transform:translateY(-1px) scale(1.02) !important;
}}
</style>
""")

    left_col, right_col = st.columns([0.43, 0.57], gap="large")

    with left_col:
        src = image_src(photo_path)
        photo_html = f'<img src="{escape(src, quote=True)}" alt="">' if src else ""
        html(f"""
<div class="write-stable-status"><span class="write-stable-dot"></span>기록 중</div>
<div class="write-stable-photo">{photo_html}</div>
<div class="write-stable-progress"><span style="width:{progress_width}%"></span></div>
<div class="write-stable-question">Q{idx + 1}. {escape(question)}</div>
<div class="write-stable-sub">특별한 날이었을까요?</div>
""")
        nav_col1, nav_col2 = st.columns(2, gap="small")
        with nav_col1:
            if st.button("이전", key="write_prev_btn", use_container_width=True):
                move_write_question(max(0, idx - 1))
                st.rerun()
        with nav_col2:
            if is_last_question:
                if st.button("작성 완료", key="write_done_btn", use_container_width=True):
                    finish_write_record(canvas_key)
                    st.rerun()
            else:
                if st.button("다음", key="write_next_btn", use_container_width=True):
                    move_write_question(min(len(questions) - 1, idx + 1))
                    st.rerun()

    with right_col:
        mode_cols = st.columns([1, 1, 1, 0.25], gap="small")
        with mode_cols[0]:
            if st.button("● 음성 기록", key="write_mode_voice_btn", use_container_width=True):
                save_current_question_answer()
                st.session_state.write_mode = "voice"
                st.rerun()
        with mode_cols[1]:
            if st.button("✎ 손글씨", key="write_mode_handwriting_btn", use_container_width=True):
                save_current_question_answer()
                st.session_state.write_mode = "handwriting"
                st.rerun()
        with mode_cols[2]:
            if st.button("T 텍스트 변환", key="write_mode_text_btn", use_container_width=True):
                save_current_question_answer()
                st.session_state.write_mode = "text"
                st.rerun()
        with mode_cols[3]:
            if st.button("⋮", key="write_more_btn", use_container_width=True):
                st.session_state.write_more_open = not st.session_state.get("write_more_open", False)
                st.rerun()

        if mode == "handwriting":
            tool_cols = st.columns([0.45, 0.45, 0.45, 0.45, 0.38, 0.38, 0.38, 0.38, 0.38, 1.8, 0.3], gap="small")
            with tool_cols[0]:
                if st.button("✎", key="write_tool_pen_btn"):
                    set_drawing_tool("pen")
            with tool_cols[1]:
                if st.button("▱", key="write_tool_eraser_btn"):
                    set_drawing_tool("eraser")
            with tool_cols[2]:
                if st.button("↶", key="write_undo_btn"):
                    undo_handwriting(canvas_key)
                    st.rerun()
            with tool_cols[3]:
                if st.button("↷", key="write_redo_btn"):
                    redo_handwriting()
                    st.rerun()

            color_buttons = [
                ("write_color_black_btn", "#000000"),
                ("write_color_blue_btn", "#1e88e5"),
                ("write_color_green_btn", "#44c767"),
                ("write_color_yellow_btn", "#f8c51b"),
                ("write_color_red_btn", "#ff4a3d"),
            ]
            for col, (button_key, color_value) in zip(tool_cols[4:9], color_buttons):
                with col:
                    if st.button(" ", key=button_key):
                        set_pen_color(color_value)

            with tool_cols[9]:
                new_width = st.slider(
                    "굵기",
                    min_value=1,
                    max_value=7,
                    value=pen_width,
                    key="write_width_slider",
                    label_visibility="collapsed",
                )
                if int(new_width) != pen_width:
                    st.session_state.pen_width = int(new_width)
                    st.session_state.stroke_width = int(new_width)
            with tool_cols[10]:
                st.markdown(f"<div style='height:36px;display:flex;align-items:center;font-size:12px;font-weight:900;color:#777'>{st.session_state.get('pen_width', pen_width)}</div>", unsafe_allow_html=True)

            active_pen_color = st.session_state.get("pen_color", "#000000")
            active_pen_width = int(st.session_state.get("stroke_width", st.session_state.get("pen_width", 3)) or 3)
            active_drawing_tool = st.session_state.get("drawing_tool", "pen")
            active_stroke_color = "#ffffff" if active_drawing_tool == "eraser" else active_pen_color
            active_stroke_width = max(active_pen_width * 4, 18) if active_drawing_tool == "eraser" else active_pen_width
            selected_selector = selected_selector_map.get(str(active_pen_color).lower())
            if selected_selector:
                html(f"""
<style>
{selected_selector} {{
    box-shadow:0 0 0 3px #2f80ed, 0 0 0 7px #ffffff !important;
    transform:translateY(-1px) scale(1.02) !important;
}}
</style>
""")

            canvas_initial = (
                st.session_state.get("handwriting_json")
                if st.session_state.get("force_canvas_redraw")
                else None
            )
            canvas_result = st_canvas(
                fill_color="rgba(255,255,255,0)",
                stroke_width=active_stroke_width,
                stroke_color=active_stroke_color,
                background_color="#ffffff",
                height=460,
                width=620,
                drawing_mode="freedraw",
                initial_drawing=canvas_initial,
                update_streamlit=False,
                display_toolbar=False,
                key=canvas_key,
            )
            if canvas_result is not None and canvas_result.json_data:
                st.session_state.handwriting_json = canvas_result.json_data
            if canvas_result is not None and canvas_result.image_data is not None:
                st.session_state.handwriting_image_data = canvas_result.image_data
            st.session_state.force_canvas_redraw = False

        elif mode == "voice":
            html("""
<div class="write-voice-card">
  <div class="write-voice-icon">🎙</div>
  <div class="write-voice-title">음성 기록</div>
  <div class="write-voice-sub">추억을 말로 들려주세요</div>
</div>
""")
            if hasattr(st, "audio_input"):
                voice_file = st.audio_input("음성 기록", key=f"write_voice_audio_{idx}")
            else:
                voice_file = st.file_uploader("음성 파일 업로드", type=["wav", "mp3", "m4a", "webm"], key=f"write_voice_upload_{idx}")
            if voice_file is not None:
                if st.button("텍스트로 변환하기", key=f"write_voice_transcribe_{idx}"):
                    text = transcribe_audio(voice_file)
                    if text:
                        st.session_state[answer_key] = text
                        answers[str(idx)] = text
                        st.session_state.question_answers = answers
                        sync_answer_to_memory_text()
                    else:
                        st.warning("음성을 텍스트로 변환하지 못했어요.")
                    st.rerun()
            if st.session_state.get(answer_key):
                st.info(st.session_state.get(answer_key))

        else:
            text_value = st.text_area(
                "질문 답변",
                key=answer_key,
                label_visibility="collapsed",
                placeholder="이 질문에 대한 기억을 적어주세요.",
                height=460,
            )
            answers[str(idx)] = str(text_value or "").strip()
            st.session_state.question_answers = answers
            sync_answer_to_memory_text()


elif page == "memory_done":
    memory = st.session_state.get("play_memory") or active_memory()
    if not memory or not media_reference_available(memory.get("photo") or st.session_state.get("photo_path")):
        go("home")
    photo_src = image_src(photo_path_for_memory(memory) or st.session_state.get("photo_path"))
    photo_html = f'<img src="{escape(photo_src, quote=True)}">' if photo_src else ""
    title = escape(memory_title(memory))
    preview = escape(memory_note_preview(memory, 42))
    html(f"""
<style>
.stApp {{ background:#777 !important; }}
.block-container {{
    max-width:1180px !important;
    min-height:760px !important;
    padding:66px 56px 82px !important;
    background:
      radial-gradient(circle at 18% 62%, rgba(255,105,33,.11), transparent 34%),
      radial-gradient(circle at 92% 100%, rgba(255,105,33,.10), transparent 38%),
      #fbfbfb !important;
    overflow-y:auto !important;
}}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
div[data-testid="stVerticalBlock"] {{ gap:0 !important; }}
.memory-done-title {{
    text-align:center;
    color:#111;
    font-size:22px;
    font-weight:1000;
    margin:0 0 28px;
}}
.memory-done-card {{
    width:420px;
    max-width:72%;
    margin:0 auto 48px;
    padding:14px 14px 32px;
    border-radius:14px;
    background:#fff;
    box-shadow:0 14px 34px rgba(0,0,0,.07);
}}
.memory-done-photo {{
    height:230px;
    border-radius:9px;
    overflow:hidden;
    background:#eee;
    margin-bottom:14px;
}}
.memory-done-photo img {{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}}
.memory-done-card-title {{
    color:#111;
    font-size:16px;
    line-height:1.25;
    font-weight:1000;
    margin:0 8px 18px;
}}
.memory-done-preview {{
    color:#8c837d;
    font-size:12px;
    line-height:1.5;
    font-weight:800;
    margin:0 8px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}}
.memory-done-actions {{
    width:min(620px, 72%);
    margin:0 auto;
}}
.memory-done-actions div[data-testid="stHorizontalBlock"] {{
    gap:72px !important;
}}
.st-key-memory_done_skip button,
.st-key-memory_done_add_music button {{
    width:100% !important;
    height:62px !important;
    min-height:62px !important;
    border-radius:7px !important;
    border:0 !important;
    font-size:15px !important;
    font-weight:1000 !important;
    box-shadow:0 10px 22px rgba(0,0,0,.10) !important;
}}
.st-key-memory_done_skip button {{
    background:#fff !important;
    color:#111 !important;
}}
.st-key-memory_done_add_music button {{
    background:#050505 !important;
    color:#fff !important;
}}
@media (max-width:900px) {{
    .block-container {{ padding:54px 34px 74px !important; }}
    .memory-done-card {{ max-width:82%; }}
    .memory-done-actions {{ width:82%; }}
    .memory-done-actions div[data-testid="stHorizontalBlock"] {{ gap:42px !important; }}
}}
@media (max-width:640px) {{
    .block-container {{ padding:38px 20px 70px !important; }}
    .memory-done-card {{ width:100%; max-width:100%; }}
    .memory-done-actions {{ width:100%; }}
    .memory-done-actions div[data-testid="stHorizontalBlock"] {{ gap:16px !important; }}
}}
</style>
<div class="memory-done-title">추억 생성 완료!</div>
<div class="memory-done-card">
  <div class="memory-done-photo">{photo_html}</div>
  <div class="memory-done-card-title">{title}</div>
  <div class="memory-done-preview">{preview}</div>
</div>
<div class="memory-done-actions">
""")
    skip_col, music_col = st.columns(2)
    with skip_col:
        if st.button("건너뛰기", key="memory_done_skip", use_container_width=True):
            st.session_state.page = "home"
            st.rerun()
    with music_col:
        if st.button("음악 추가하기", key="memory_done_add_music", use_container_width=True):
            st.session_state.page = "music_loading"
            st.rerun()
    html("</div>")

elif page == "music_loading":
    memory = active_memory()
    if not st.session_state.get("spotify_tracks"):
        st.session_state.spotify_tracks = spotify_recommendations(photo_music_query(memory))

    html("""
<style>
html, body, .stApp { margin:0 !important; width:100vw !important; height:100vh !important; overflow:hidden !important; }
.stApp { background:#777 !important; color:#111 !important; }
.stApp .block-container {
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:0 !important;
    overflow:hidden !important;
    background:
      radial-gradient(circle at 50% 104%, rgba(255,105,33,.82) 0, rgba(255,105,33,.42) 9%, rgba(255,105,33,.12) 20%, transparent 32%),
      linear-gradient(135deg, #fbfbfb 0%, #faf9f7 58%, #fff3ef 100%) !important;
    position:relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
div[data-testid="stVerticalBlock"] { gap:0 !important; }
.music-loading-page {
    width:1180px;
    height:760px;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    text-align:center;
    padding-bottom:130px;
}
.music-loading-pill {
    height:28px;
    padding:0 22px;
    border-radius:999px;
    background:#f3f3f3;
    display:flex;
    align-items:center;
    justify-content:center;
    color:#111;
    font-size:12px;
    font-weight:900;
    margin-bottom:74px;
}
.music-loading-title {
    font-size:28px;
    line-height:1.58;
    font-weight:500;
    letter-spacing:0;
}
.music-loading-title b { font-weight:1000; }
</style>
<div class="music-loading-page">
  <div class="music-loading-pill">AI 추천</div>
  <div class="music-loading-title">AI가 <b>당신의 추억</b>을 분석해<br>노래를 추천중이에요.</div>
</div>
""")
    time.sleep(1.6)
    st.session_state.page = "music"
    st.rerun()

elif page == "music":
    memory = active_memory()
    if not st.session_state.get("photo_path") and st.session_state.get("memory_id"):
        restore_photo(st.session_state.memory_id)

    if not st.session_state.get("spotify_tracks"):
        st.session_state.spotify_tracks = spotify_recommendations(photo_music_query(memory))

    def make_track_lyrics(track):
        """Show real lyrics attached to the track. Do not generate fake lyrics."""
        track = normalize_track(track)
        existing = track.get("lyrics")
        if isinstance(existing, list):
            clean_lines = [str(line).strip() for line in existing if str(line).strip()]
            if clean_lines and clean_lines != DEFAULT_TRACK_LYRICS:
                return clean_lines[:10]
        if isinstance(existing, str) and existing.strip():
            return [line.strip() for line in existing.splitlines() if line.strip()][:10]
        return [
            "가사를 찾지 못했어요.",
            "LRCLIB에 등록된 가사가 없는 곡일 수 있어요.",
        ]

    def normalized_music_track(track):
        track = normalize_track(track)
        track["lyrics"] = make_track_lyrics(track)
        return track

    raw_tracks = st.session_state.get("spotify_tracks") or []
    tracks = [normalized_music_track(track) for track in raw_tracks]

    raw_selected_track = st.session_state.get("selected_track") or memory.get("selected_track") or {}
    selected_track = normalized_music_track(raw_selected_track) if raw_selected_track else {}
    if selected_track:
        st.session_state.selected_track = selected_track

    photo_src = image_src(photo_path_for_memory(memory) or st.session_state.get("photo_path"))
    photo_html = f'<img src="{escape(photo_src, quote=True)}" alt="memory photo">' if photo_src else '<div class="music-photo-empty">사진 미리보기</div>'
    screen_title = escape(memory_title(memory))

    def cover_html(track, class_name="music-track-cover"):
        track = normalized_music_track(track)
        cover = track.get("image") or ""
        if cover:
            return f'<div class="{class_name}"><img src="{escape(cover, quote=True)}" alt=""></div>'
        return f'<div class="{class_name}">♪</div>'

    def lyrics_markup(track):
        lines = make_track_lyrics(track)
        html_lines = []
        for idx, line in enumerate(lines[:6]):
            active = " active" if idx == 2 else ""
            html_lines.append(f'<div class="music-lyric-line{active}">{escape(line)}</div>')
        return "".join(html_lines)

    html("""
<style>
html, body, .stApp {
    margin:0 !important;
    width:100vw !important;
    height:100vh !important;
    overflow:hidden !important;
}
.stApp {
    background:#777 !important;
    color:#111 !important;
}
.stApp .block-container {
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:34px 44px 26px !important;
    overflow:hidden !important;
    background:
        radial-gradient(circle at 105% 87%, rgba(255,104,35,.36), transparent 31%),
        radial-gradient(circle at -10% 20%, rgba(255,104,35,.10), transparent 28%),
        linear-gradient(120deg, #fbfbfb 0%, #faf9f7 54%, #fff2eb 100%) !important;
    position:relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
    display:none !important;
}
div[data-testid="stVerticalBlock"] {
    gap:0 !important;
}
div[data-testid="column"] {
    overflow:visible !important;
}
.music-left-title {
    display:flex;
    align-items:center;
    gap:8px;
    margin:0 0 8px;
    font-size:22px;
    line-height:1.05;
    font-weight:1000;
    letter-spacing:-.45px;
}
.music-left-title span {
    color:#ff6b21;
    font-size:22px;
}
.music-left-sub {
    margin:0 0 24px;
    color:#333;
    font-size:12px;
    line-height:1.2;
    font-weight:800;
}
.music-photo-card {
    width:420px;
    height:225px;
    background:#eee;
    overflow:hidden;
    display:flex;
    align-items:center;
    justify-content:center;
    margin-bottom:34px;
}
.music-photo-card img {
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}
.music-photo-empty {
    color:#777;
    font-size:13px;
    font-weight:800;
}

/* 선택 음악 카드: 사용자가 보낸 2번째 참고 이미지처럼 카드 안에 제목/커버/가사/컨트롤을 넣음 */
.music-player-card {
    width:420px;
    height:220px;
    background:#fff;
    border-radius:7px;
    box-shadow:0 12px 26px rgba(0,0,0,.08);
    overflow:hidden;
    display:grid;
    grid-template-rows:38px 116px 5px 61px;
    margin-bottom:0;
}
.music-card-header {
    height:38px;
    background:rgba(0,0,0,.025);
    display:flex;
    align-items:center;
    justify-content:space-between;
    padding:0 18px;
}
.music-card-header b {
    color:#111;
    font-size:15px;
    font-weight:1000;
    max-width:270px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.music-card-header span {
    color:#666;
    font-size:10px;
    font-weight:900;
    max-width:90px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.music-player-main {
    display:grid;
    grid-template-columns:122px 1fr;
    gap:22px;
    align-items:center;
    padding:14px 18px 12px;
}
.music-cover-large {
    width:112px;
    height:86px;
    border-radius:3px;
    background:#111;
    color:#fff;
    overflow:hidden;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:25px;
}
.music-cover-large img {
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}
.music-player-lyrics {
    height:92px;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    text-align:center;
    gap:5px;
    overflow:hidden;
}
.music-lyric-line {
    color:#9b9b9b;
    font-size:12px;
    line-height:1.15;
    font-weight:800;
    max-width:220px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.music-lyric-line.active {
    color:#111;
    font-size:14px;
    font-weight:1000;
}
.music-card-progress {
    height:5px;
    background:#e2e2e2;
    overflow:hidden;
}
.music-card-progress span {
    display:block;
    width:38%;
    height:100%;
    background:#ff6b21;
}
.music-player-controls {
    height:61px;
    display:grid;
    grid-template-columns:34px 46px 34px 1fr 34px 34px;
    align-items:center;
    gap:14px;
    padding:0 22px;
    font-size:16px;
    font-weight:1000;
}
.music-play-circle {
    width:42px;
    height:42px;
    border-radius:50%;
    background:#050505;
    color:#fff;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:15px;
}
.music-empty-card {
    width:420px;
    height:220px;
    border-radius:7px;
    background:#fff;
    box-shadow:0 12px 26px rgba(0,0,0,.08);
    display:flex;
    flex-direction:column;
    justify-content:center;
    align-items:center;
    gap:10px;
}
.music-empty-card b {
    font-size:17px;
    font-weight:1000;
}
.music-empty-card span {
    color:#777;
    font-size:12px;
    font-weight:800;
}
.music-preview-note {
    height:0;
    overflow:hidden;
}
div[data-testid="stAudio"] {
    display:none !important;
}
.native-audio {
    width:100%;
    height:36px;
    display:flex;
    align-items:center;
}
.native-audio audio {
    width:100%;
    height:34px;
    display:block;
}
.native-audio.compact {
    position:absolute;
    left:30px;
    bottom:24px;
    width:360px;
    height:34px;
    z-index:20;
    opacity:.78;
}
.native-audio.compact audio {
    width:360px;
    height:34px;
}

.music-right-title {
    margin:0 0 22px;
    color:#111;
    font-size:22px;
    line-height:1.05;
    font-weight:1000;
}
.st-key-music_search_input input {
    width:100% !important;
    height:46px !important;
    border-radius:6px !important;
    border:1.5px solid rgba(255,105,33,.62) !important;
    background:#fff !important;
    padding:0 18px !important;
    color:#111 !important;
    font-size:12px !important;
    font-weight:800 !important;
    box-shadow:none !important;
}
.st-key-music_search_input label {
    display:none !important;
}
.music-list-title {
    margin:26px 0 14px;
    color:#111;
    font-size:17px;
    line-height:1;
    font-weight:1000;
}
.music-row-spacer {
    height:63px;
    display:flex;
    align-items:center;
}
.music-row-line {
    height:1px;
    width:440px;
    margin:-3px 0 0 58px;
    background:rgba(0,0,0,.075);
}
.music-track-cover {
    width:42px;
    height:42px;
    border-radius:2px;
    background:#d8d8d8;
    color:#888;
    overflow:hidden;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:16px;
}
.music-track-cover img {
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}
.music-track-title {
    color:#111;
    font-size:13px;
    line-height:1.14;
    font-weight:1000;
    max-width:320px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.music-track-artist {
    color:#777;
    font-size:10px;
    line-height:1.1;
    margin-top:6px;
    font-weight:800;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.st-key-music_select_track_0 button,
.st-key-music_select_track_1 button,
.st-key-music_select_track_2 button,
.st-key-music_select_track_3 button,
.st-key-music_select_track_4 button,
.st-key-music_select_track_5 button,
.st-key-music_select_track_6 button,
.st-key-music_select_track_7 button,
.st-key-music_select_track_8 button,
.st-key-music_select_track_9 button {
    width:30px !important;
    min-width:30px !important;
    height:30px !important;
    min-height:30px !important;
    border-radius:50% !important;
    background:transparent !important;
    color:#ff4b56 !important;
    border:0 !important;
    box-shadow:none !important;
    font-size:18px !important;
    font-weight:1000 !important;
    padding:0 !important;
    margin-top:15px !important;
}
.st-key-music_select_track_0 button:hover,
.st-key-music_select_track_1 button:hover,
.st-key-music_select_track_2 button:hover,
.st-key-music_select_track_3 button:hover,
.st-key-music_select_track_4 button:hover,
.st-key-music_select_track_5 button:hover,
.st-key-music_select_track_6 button:hover,
.st-key-music_select_track_7 button:hover,
.st-key-music_select_track_8 button:hover,
.st-key-music_select_track_9 button:hover {
    background:rgba(255,75,86,.08) !important;
}
.st-key-music_more_button button {
    height:38px !important;
    min-height:38px !important;
    background:transparent !important;
    color:#333 !important;
    border:0 !important;
    box-shadow:none !important;
    font-size:12px !important;
    font-weight:900 !important;
    margin-top:12px !important;
}
.music-notice {
    height:18px;
    color:#ef5a28;
    font-size:12px;
    font-weight:900;
    margin:7px 0 10px;
}
.st-key-music_add_done button {
    height:62px !important;
    min-height:62px !important;
    border-radius:7px !important;
    background:#050505 !important;
    color:#fff !important;
    border:0 !important;
    box-shadow:0 10px 22px rgba(0,0,0,.14) !important;
    font-size:16px !important;
    font-weight:1000 !important;
}
</style>
""")

    left_col, right_col = st.columns([0.46, 0.54], gap="large")

    with left_col:
        html(f"""
<div class="music-left-title"><span>♪</span> 이 추억에 어울리는 음악</div>
<div class="music-left-sub">AI가 추억의 분위기와 내용을 바탕으로 선곡했어요.</div>
<div class="music-photo-card">{photo_html}</div>
""")
        if selected_track:
            html(f"""
<div class="music-player-card">
  <div class="music-card-header">
    <b>{escape(selected_track.get("title") or "노래 제목")}</b>
    <span>{escape(selected_track.get("artist") or "가수")}</span>
  </div>
  <div class="music-player-main">
    {cover_html(selected_track, "music-cover-large")}
    <div class="music-player-lyrics">{lyrics_markup(selected_track)}</div>
  </div>
  <div class="music-card-progress"><span></span></div>
  <div class="music-player-controls">
    {render_track_audio_html(selected_track, autoplay=False, compact=False) or '<div style="color:#777;font-size:12px;font-weight:800;">음원 파일을 찾지 못했어요.</div>'}
  </div>
</div>
""")
        else:
            html("""
<div class="music-empty-card">
  <b>음악을 선택해주세요.</b>
  <span>오른쪽 추천 목록에서 +를 눌러보세요.</span>
</div>
""")

    with right_col:
        html(f'<div class="music-right-title">{screen_title}</div>')
        query = st.text_input(
            "음악 검색",
            value=st.session_state.get("spotify_query", ""),
            placeholder="어울리는 노래를 검색하세요.",
            label_visibility="collapsed",
            key="music_search_input",
        )
        st.session_state.spotify_query = query

        search_text = str(query or "").strip()
        if search_text:
            # 검색어가 있으면 현재 추천 리스트를 필터링만 하지 말고 Spotify 검색을 새로 실행한다.
            # 이전 검색어와 같으면 결과를 캐시해서 매 rerun마다 API를 반복 호출하지 않는다.
            if st.session_state.get("music_last_search_query") != search_text:
                st.session_state.music_search_results = spotify_recommendations(search_text)
                st.session_state.music_last_search_query = search_text
                st.session_state.music_show_more = False
            visible_tracks = [normalized_music_track(track) for track in st.session_state.get("music_search_results", [])]
            list_title = "검색 결과"
        else:
            st.session_state.music_last_search_query = ""
            st.session_state.music_search_results = []
            visible_tracks = [normalized_music_track(track) for track in tracks]
            list_title = "AI 추천 음악"

        html(f'<div class="music-list-title">{list_title}</div>')

        if not visible_tracks:
            # 그래도 비어 있으면 화면이 깨지거나 빈칸이 되지 않게 기본 추천으로 fallback
            visible_tracks = [normalized_music_track(track) for track in spotify_recommendations(photo_music_query(memory))]
            if search_text:
                st.warning("검색 결과가 없어 기본 추천 음악을 보여드려요.")

        visible_count = len(visible_tracks) if st.session_state.get("music_show_more") else min(5, len(visible_tracks))
        for index, track in enumerate(visible_tracks[:visible_count]):
            track = normalized_music_track(track)
            is_selected = (
                selected_track
                and selected_track.get("title") == track.get("title")
                and selected_track.get("artist") == track.get("artist")
            )
            row_cols = st.columns([0.075, 0.82, 0.105], gap="small")
            with row_cols[0]:
                html(f'<div class="music-row-spacer">{cover_html(track)}</div>')
            with row_cols[1]:
                html(f"""
<div class="music-row-spacer">
  <div>
    <div class="music-track-title">{escape(track.get("title") or "노래 제목")}</div>
    <div class="music-track-artist">By {escape(track.get("artist") or "가수")}</div>
  </div>
</div>
""")
            with row_cols[2]:
                if st.button("✓" if is_selected else "+", key=f"music_select_track_{index}", use_container_width=True):
                    selected = normalized_music_track(track)
                    found_lyrics = get_lyrics_from_lrclib(
                        selected.get("title", ""),
                        selected.get("artist", ""),
                    )
                    if found_lyrics:
                        selected["lyrics"] = found_lyrics
                    st.session_state.selected_track = selected
                    st.session_state.music_notice = ""
                    st.rerun()
            if index < visible_count - 1:
                html('<div class="music-row-line"></div>')

        if len(visible_tracks) > 5:
            more_label = "접기" if st.session_state.get("music_show_more") else "더 많은 음악 보기  ⌄"
            if st.button(more_label, key="music_more_button", use_container_width=True):
                st.session_state.music_show_more = not st.session_state.get("music_show_more")
                st.rerun()
        else:
            html('<div style="height:48px"></div>')

        notice = st.session_state.get("music_notice", "")
        html(f'<div class="music-notice">{escape(notice)}</div>')

        if st.button("음악 추가하기", key="music_add_done", use_container_width=True):
            if not st.session_state.get("selected_track"):
                st.session_state.music_notice = "음악을 먼저 선택해주세요."
                st.rerun()
            st.session_state.selected_track = normalized_music_track(st.session_state.selected_track)
            if not st.session_state.selected_track.get("lyrics") or st.session_state.selected_track.get("lyrics") == DEFAULT_TRACK_LYRICS:
                found_lyrics = get_lyrics_from_lrclib(
                    st.session_state.selected_track.get("title", ""),
                    st.session_state.selected_track.get("artist", ""),
                )
                if found_lyrics:
                    st.session_state.selected_track["lyrics"] = found_lyrics
            edit_memory_id = st.session_state.get("editing_memory_id")
            if edit_memory_id and st.session_state.get("edit_mode") == "music_edit":
                if update_memory_track(edit_memory_id, st.session_state.selected_track):
                    finish_existing_music_edit()
                    st.rerun()
                st.session_state.music_notice = "음악을 저장할 기존 기록을 찾지 못했어요."
                st.rerun()
            if save_selected_track_to_current_memory():
                st.session_state.page = "music_done"
                st.rerun()
            st.rerun()



elif page == "play_fullscreen":
    # Flicker-free slideshow:
    # 이전 버전은 0.4초마다 st.rerun()을 해서 화면이 검게 깜빡였음.
    # 여기서는 한 번만 렌더링하고, 사진/자막 전환은 브라우저 JS가 처리한다.
    active_nfc = current_nfc_category()
    playback_memories = st.session_state.get("playback_memories") or memories_for_current_nfc(load_memories(), active_nfc)
    playback_memories = [memory for memory in playback_memories if media_reference_available(photo_path_for_memory(memory))]
    if not playback_memories:
        st.session_state.page = "home"
        st.rerun()

    def playback_text_from_memory(memory):
        memory = memory or {}
        candidates = [
            memory_record_text(memory, include_questions=False),
            memory.get("text_record"),
            memory.get("text"),
            memory.get("transcript"),
            memory.get("voice_text"),
            memory.get("audio_text"),
            memory.get("record_text"),
            memory.get("note"),
            memory.get("memo"),
            memory.get("description"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
        answers = memory.get("answers") or memory.get("question_answers")
        if isinstance(answers, dict):
            joined = " ".join(str(v).strip() for v in answers.values() if str(v).strip())
            if joined.strip():
                return joined.strip()
        if isinstance(answers, list):
            joined = " ".join(str(v).strip() for v in answers if str(v).strip())
            if joined.strip():
                return joined.strip()
        return ""

    def split_caption_sentences(text_value):
        text_value = re.sub(r"\s+", " ", str(text_value or "").strip())
        if not text_value:
            return []
        pieces = re.split(r"(?<=[.!?。！？])\s+|(?<=[다요죠까])\s+", text_value)
        cleaned = []
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if len(piece) > 54:
                subpieces = re.split(r"(?<=[,，、])\s*", piece)
                buffer = ""
                for sub in subpieces:
                    sub = sub.strip()
                    if not sub:
                        continue
                    if len(buffer + " " + sub) <= 48:
                        buffer = (buffer + " " + sub).strip()
                    else:
                        if buffer:
                            cleaned.append(buffer)
                        buffer = sub
                if buffer:
                    cleaned.append(buffer)
            else:
                cleaned.append(piece)
        return cleaned[:12]

    def caption_duration(sentence):
        length = len(str(sentence or "").replace(" ", ""))
        return min(7.0, max(4.0, length * 0.18))

    # 사진 1장당 여러 자막 문장이 있을 수 있으므로 JS용 step으로 펼친다.
    steps = []
    for photo_index, memory in enumerate(playback_memories):
        photo_src = image_src(photo_path_for_memory(memory))
        captions = split_caption_sentences(playback_text_from_memory(memory))
        if captions:
            for caption in captions:
                steps.append({
                    "photo": photo_src,
                    "caption": caption,
                    "duration": int(caption_duration(caption) * 1000),
                    "counter": f"{photo_index + 1} / {len(playback_memories)}",
                })
        else:
            steps.append({
                "photo": photo_src,
                "caption": "",
                "duration": 4500,
                "counter": f"{photo_index + 1} / {len(playback_memories)}",
            })

    # 대표 노래: 현재 NFC 카테고리 안의 첫 selected_track.
    tracks = []
    seen_tracks = set()
    for memory in playback_memories:
        track = memory.get("selected_track") or {}
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        key = (title, artist)
        if title and key not in seen_tracks:
            seen_tracks.add(key)
            tracks.append(track)
    current_track = tracks[0] if tracks else {}
    play_audio_html = render_track_audio_html(current_track, autoplay=True, compact=True)

    html("""
<style>
html, body, .stApp {
    margin:0 !important;
    width:100vw !important;
    height:100vh !important;
    overflow:hidden !important;
    background:#000 !important;
}
.stApp {
    background:#000 !important;
    color:#fff !important;
}
.stApp .block-container {
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:0 !important;
    overflow:hidden !important;
    background:#000 !important;
    position:relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
    display:none !important;
}
div[data-testid="stVerticalBlock"] {
    gap:0 !important;
}
iframe {
    display:block !important;
    border:0 !important;
}
.parent-play-exit-link {
    position:fixed !important;
    left:50% !important;
    top:645px !important;
    transform:translateX(-50%) !important;
    width:116px !important;
    height:42px !important;
    border-radius:999px !important;
    background:rgba(255,255,255,.94) !important;
    color:#111 !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
    text-decoration:none !important;
    font-size:13px !important;
    line-height:1 !important;
    font-weight:1000 !important;
    box-shadow:0 10px 24px rgba(0,0,0,.22) !important;
    z-index:2147483647 !important;
    cursor:pointer !important;
}
</style>
<a class="parent-play-exit-link" href="?action=playback_exit" target="_self">종료</a>
""")

    slides_json = json.dumps(steps, ensure_ascii=False)
    audio_json = json.dumps(play_audio_html, ensure_ascii=False)

    component_markup = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
html, body {
  margin:0;
  width:1180px;
  height:760px;
  overflow:hidden;
  background:#000;
  font-family:Arial, 'Noto Sans KR', sans-serif;
}
.play-fullscreen {
  width:1180px;
  height:760px;
  background:#000;
  position:relative;
  overflow:hidden;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
}
.play-photo-wrap {
  position:relative;
  width:960px;
  height:560px;
  display:flex;
  align-items:center;
  justify-content:center;
  overflow:hidden;
  background:#000;
  flex:0 0 auto;
}
.play-full-photo {
  width:100%;
  height:100%;
  object-fit:contain;
  display:block;
  opacity:1;
  transition:opacity .18s ease;
}
.play-full-counter {
  position:absolute;
  right:16px;
  top:14px;
  z-index:6;
  padding:5px 9px;
  border-radius:999px;
  background:rgba(0,0,0,.42);
  color:#fff;
  font-size:12px;
  line-height:1;
  font-weight:900;
}
.play-caption {
  position:absolute;
  left:50%;
  bottom:30px;
  transform:translateX(-50%);
  z-index:7;
  max-width:82%;
  padding:10px 18px 11px;
  border-radius:10px;
  background:rgba(0,0,0,.55);
  color:#fff;
  font-size:25px;
  line-height:1.35;
  font-weight:900;
  letter-spacing:-.2px;
  text-align:center;
  text-shadow:0 2px 8px rgba(0,0,0,.55);
  word-break:keep-all;
  display:none;
}
.play-exit-placeholder {
  width:116px;
  height:42px;
  margin-top:38px;
  flex:0 0 auto;
}
.native-audio.compact {
  position:absolute;
  left:30px;
  bottom:24px;
  width:360px;
  height:34px;
  z-index:20;
  opacity:.78;
}
.native-audio.compact audio {
  width:360px;
  height:34px;
}
</style>
</head>
<body>
<div class="play-fullscreen">
  <div class="play-photo-wrap">
    <img id="slidePhoto" class="play-full-photo" src="" alt="">
    <div id="slideCounter" class="play-full-counter"></div>
    <div id="slideCaption" class="play-caption"></div>
  </div>
  <div class="play-exit-placeholder"></div>
  <div id="audioMount"></div>
</div>
<script>
const slides = __SLIDES_JSON__;
const audioHtml = __AUDIO_HTML__;
const photo = document.getElementById('slidePhoto');
const counter = document.getElementById('slideCounter');
const caption = document.getElementById('slideCaption');
const audioMount = document.getElementById('audioMount');

audioMount.innerHTML = audioHtml || "";

// 사진을 미리 로드해서 전환 순간 검게 깜빡이는 걸 줄임
const preloaded = {};
slides.forEach((slide) => {
  if (slide.photo && !preloaded[slide.photo]) {
    const img = new Image();
    img.src = slide.photo;
    preloaded[slide.photo] = img;
  }
});

let stepIndex = 0;
let timer = null;

function renderStep() {
  if (!slides.length) {
    window.parent.location.href = '?action=playback_exit';
    return;
  }

  if (stepIndex >= slides.length) {
    window.parent.location.href = '?action=playback_exit';
    return;
  }

  const slide = slides[stepIndex];

  // 같은 사진에서 자막만 바뀌는 경우 img src를 다시 넣지 않아서 깜빡임 방지
  if (photo.getAttribute('src') !== slide.photo) {
    photo.style.opacity = '0.98';
    photo.src = slide.photo || "";
    requestAnimationFrame(() => { photo.style.opacity = '1'; });
  }

  counter.textContent = slide.counter || "";
  if (slide.caption) {
    caption.textContent = slide.caption;
    caption.style.display = "block";
  } else {
    caption.textContent = "";
    caption.style.display = "none";
  }

  const duration = Math.max(2500, Number(slide.duration || 4500));
  timer = setTimeout(() => {
    stepIndex += 1;
    renderStep();
  }, duration);
}

renderStep();
</script>
</body>
</html>
"""
    component_markup = component_markup.replace("__SLIDES_JSON__", slides_json)
    component_markup = component_markup.replace("__AUDIO_HTML__", audio_json)

    components.html(component_markup, height=760, scrolling=False)


elif page == "music_done":
    html("""
<style>
html, body, .stApp {
    margin:0 !important;
    width:100vw !important;
    height:100vh !important;
    overflow:hidden !important;
}
.stApp {
    background:#777 !important;
    color:#111 !important;
}
.stApp .block-container {
    width:1180px !important;
    min-width:1180px !important;
    max-width:1180px !important;
    height:760px !important;
    min-height:760px !important;
    max-height:760px !important;
    margin:0 auto !important;
    padding:0 !important;
    overflow:hidden !important;
    background:
        radial-gradient(circle at 108% 88%, rgba(255,104,35,.25), transparent 30%),
        linear-gradient(135deg, #fbfbfb 0%, #faf9f7 58%, #fff3ef 100%) !important;
    position:relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
    display:none !important;
}
div[data-testid="stVerticalBlock"] {
    gap:0 !important;
}
.music-done-title {
    width:100%;
    text-align:center;
    color:#111;
    font-size:22px;
    line-height:1.2;
    font-weight:1000;
    letter-spacing:-.2px;
    margin-top:337px;
    margin-bottom:74px;
}
.st-key-music_done_home button,
.st-key-music_done_continue button {
    height:62px !important;
    min-height:62px !important;
    border-radius:7px !important;
    border:0 !important;
    box-shadow:0 10px 22px rgba(0,0,0,.10) !important;
    font-size:15px !important;
    font-weight:1000 !important;
}
.st-key-music_done_home button {
    background:#fff !important;
    color:#111 !important;
}
.st-key-music_done_continue button {
    background:#050505 !important;
    color:#fff !important;
}
</style>
<div class="music-done-title">음악이 추가되었습니다!</div>
""")
    spacer_l, home_col, spacer_mid, continue_col, spacer_r = st.columns([1.55, 1.05, 0.28, 1.05, 1.55], gap="small")
    with home_col:
        if st.button("홈으로", key="music_done_home", use_container_width=True):
            st.session_state.page = "home"
            st.session_state.home_tab = "album"
            st.rerun()
    with continue_col:
        if st.button("이어서 기록하기", key="music_done_continue", use_container_width=True):
            reset_flow()
            st.session_state.page = "scan_upload"
            st.rerun()


elif page in ("category_loading", "category_edit", "album_done", "nfc_scan", "nfc_done", "video", "player", "player_legacy"):
    # 현재 프로토타입에서는 사용하지 않는 옛 화면들:
    # - AI 추억 카테고리 생성
    # - 카테고리 수정
    # - 앨범 생성 완료
    # - 옛 NFC 후속 화면
    # - 영상/재생 화면
    # 필요하면 나중에 이 파일의 이전 버전에서 복구 가능.
    st.session_state.page = "home"
    st.rerun()
