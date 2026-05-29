import base64
import json
import os
import time
import uuid
from datetime import datetime
from html import escape
from urllib.parse import quote

import requests
import streamlit as st
from dotenv import load_dotenv
from PIL import Image
from streamlit_drawable_canvas import st_canvas


BASE = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(BASE, "photos")
MEMORY_DIR = os.path.join(BASE, "memories")
HANDWRITING_DIR = os.path.join(BASE, "handwriting")
os.makedirs(PHOTO_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(HANDWRITING_DIR, exist_ok=True)
load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")

st.set_page_config(page_title="Re:Play", layout="centered", initial_sidebar_state="collapsed")

DEFAULTS = {
    "page": "splash",
    "photo_path": None,
    "memory_id": None,
    "selected_track": None,
    "spotify_tracks": [],
    "spotify_query": "",
    "music_show_more": False,
    "play_memory": None,
    "selected_memory_id": None,
    "upload_signature": None,
    "memory_text": "",
    "handwriting_path": None,
    "handwriting_json": None,
    "write_mode": "chat",
    "analysis_done": False,
    "keywords": ["추억사진", "인물사진", "옛날 분위기"],
    "question": "이 사진을 찍던 날은 어떤 날이었나요?",
    "questions": [
        "이 사진을 찍던 날은 어떤 날이었나요?",
        "사진 속 사람들과 어떤 추억이 있나요?",
        "이 장면을 보면 가장 먼저 떠오르는 감정은 무엇인가요?",
    ],
    "selected_category": "",
    "selected_categories": [],
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


def go(page):
    st.session_state["page"] = page
    st.rerun()


def reset_flow():
    st.session_state.photo_path = None
    st.session_state.memory_id = None
    st.session_state.selected_track = None
    st.session_state.spotify_tracks = []
    st.session_state.spotify_query = ""
    st.session_state.music_show_more = False
    st.session_state.play_memory = None
    st.session_state.upload_signature = None
    st.session_state.memory_text = ""
    st.session_state.handwriting_path = None
    st.session_state.handwriting_json = None
    st.session_state.write_mode = "chat"
    st.session_state.analysis_done = False
    st.session_state.keywords = ["추억사진", "인물사진", "옛날 분위기"]
    st.session_state.question = "이 사진을 찍던 날은 어떤 날이었나요?"
    st.session_state.questions = [
        "이 사진을 찍던 날은 어떤 날이었나요?",
        "사진 속 사람들과 어떤 추억이 있나요?",
        "이 장면을 보면 가장 먼저 떠오르는 감정은 무엇인가요?",
    ]
    st.session_state.selected_category = ""
    st.session_state.selected_categories = []


def html(markup):
    st.markdown(markup.strip(), unsafe_allow_html=True)


def image_to_base64(path):
    with open(path, "rb") as file:
        return base64.b64encode(file.read()).decode("utf-8")


def image_src(path):
    if not path or not os.path.exists(path):
        return ""
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{image_to_base64(path)}"


def save_photo(file):
    memory_id = str(uuid.uuid4())[:8]
    path = os.path.join(PHOTO_DIR, f"{memory_id}.jpg")
    image = Image.open(file).convert("RGB")
    image.save(path, quality=92)
    st.session_state.memory_id = memory_id
    st.session_state.photo_path = path


def restore_photo(memory_id):
    if not memory_id:
        return
    path = os.path.join(PHOTO_DIR, f"{memory_id}.jpg")
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
    if isinstance(canvas_state, dict) and canvas_state.get("raw"):
        st.session_state.handwriting_json = canvas_state["raw"]


def set_pen_color(hex_color, canvas_key):
    remember_canvas_state(canvas_key)
    st.session_state.pen_color = hex_color


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
    st.session_state.write_mode = mode


def ensure_selected_categories():
    categories = st.session_state.get("selected_categories")
    if not isinstance(categories, list):
        current = st.session_state.get("selected_category", "")
        categories = [current] if current else []
        st.session_state.selected_categories = categories
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
        "question": questions[0],
        "questions": questions,
    }


def analyze_photo_with_ai(path):
    if not OPENAI_API_KEY or not path or not os.path.exists(path):
        return fallback_photo_analysis()

    prompt = """
업로드된 사진을 보고 Re:Play 서비스에 쓸 짧은 한국어 키워드 3개와 회상 질문 3개를 만들어줘.

규칙:
- 사진에 실제로 보이는 단서만 기반으로 해.
- 시대나 계절은 확실하지 않으면 단정하지 말고 "옛날 사진", "야외", "가족 모임"처럼 조심스럽게 표현해.
- 키워드는 각각 2~8글자 정도로 짧게.
- 질문은 사진 속 인물, 장소, 상황, 분위기 같은 시각 단서에 맞춰 서로 다르게 만들어.
- 질문은 사용자가 사진 속 기억을 떠올릴 수 있는 자연스러운 한 문장.
- 반드시 JSON만 반환해. 형식: {"keywords":["키워드1","키워드2","키워드3"],"questions":["질문1","질문2","질문3"]}
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
        "max_tokens": 420,
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
        keywords = [str(item).strip() for item in data.get("keywords", []) if str(item).strip()]
        raw_questions = data.get("questions") or [data.get("question", "")]
        questions = [str(item).strip() for item in raw_questions if str(item).strip()]
        if len(keywords) < 3 or not questions:
            return fallback_photo_analysis()
        if len(questions) < 3:
            questions = (questions + fallback_photo_analysis()["questions"])[:3]
        return {"keywords": keywords[:3], "question": questions[0], "questions": questions[:3]}
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
    st.session_state.question = result["question"]
    st.session_state.questions = result["questions"]
    st.session_state.selected_category = ""
    st.session_state.selected_categories = []
    st.session_state.analysis_done = True


def save_memory():
    if not st.session_state.memory_id:
        st.session_state.memory_id = str(uuid.uuid4())[:8]
    handwriting = st.session_state.handwriting_path or handwriting_path_for(st.session_state.memory_id)
    data = {
        "id": st.session_state.memory_id,
        "photo": st.session_state.photo_path,
        "keywords": st.session_state.keywords,
        "question": st.session_state.question,
        "questions": st.session_state.questions,
        "note": st.session_state.memory_text,
        "handwriting": handwriting,
        "selected_track": st.session_state.selected_track,
        "category": st.session_state.selected_category,
        "categories": ensure_selected_categories(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(MEMORY_DIR, f"{data['id']}.json"), "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    st.session_state.play_memory = data
    st.session_state.selected_memory_id = data["id"]


def load_memories():
    memories = []
    for name in os.listdir(MEMORY_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(MEMORY_DIR, name), "r", encoding="utf-8") as file:
                memory = json.load(file)
        except Exception:
            continue
        memory_id = memory.get("id") or os.path.splitext(name)[0]
        memory["id"] = memory_id
        handwriting = memory.get("handwriting") or handwriting_path_for(memory_id)
        if handwriting:
            memory["handwriting"] = handwriting
        if memory.get("photo") and os.path.exists(memory["photo"]):
            memories.append(memory)
    memories.sort(key=lambda item: item.get("time", ""), reverse=True)
    return memories


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
                st.session_state.selected_category = item.get("category") or st.session_state.get("selected_category", "")
                st.session_state.selected_categories = item.get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
                handwriting = item.get("handwriting") or handwriting_path_for(memory_id)
                if handwriting:
                    st.session_state.handwriting_path = handwriting
                return item
    if (not memory.get("photo") or not os.path.exists(memory.get("photo", ""))) and all_memories:
        item = all_memories[0]
        st.session_state.play_memory = item
        st.session_state.selected_memory_id = item.get("id")
        st.session_state.memory_id = item.get("id") or st.session_state.get("memory_id")
        st.session_state.photo_path = item.get("photo") or st.session_state.get("photo_path")
        st.session_state.memory_text = item.get("note", st.session_state.get("memory_text", ""))
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
            candidates.append(os.path.join(PHOTO_DIR, f"{memory_id}.jpg"))
    for path in candidates:
        if path and os.path.exists(path):
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
        if path and os.path.exists(path):
            st.session_state.handwriting_path = path
            return path
    return None


def delete_memory(memory_id):
    if not memory_id:
        return
    paths = [
        os.path.join(MEMORY_DIR, f"{memory_id}.json"),
        os.path.join(PHOTO_DIR, f"{memory_id}.jpg"),
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


def spotify_recommendations(query="korean old pop memories"):
    fallback = [
        {"title": "Love Smile Peace", "artist": "Memory Tape", "image": "", "preview_url": ""},
        {"title": "Don't Worry", "artist": "Vintage Radio", "image": "", "preview_url": ""},
        {"title": "봄날의 기억", "artist": "Re:Play", "image": "", "preview_url": ""},
        {"title": "Last Scene", "artist": "Soft Vinyl", "image": "", "preview_url": ""},
    ]
    try:
        token = get_spotify_token()
        if not token:
            return fallback
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
        return tracks or fallback
    except Exception:
        return fallback


def category_candidates():
    keywords = [word for word in st.session_state.get("keywords", []) if word]
    question_text = " ".join(st.session_state.get("questions", []))
    scene_text = " ".join(keywords + [question_text])
    labels = []
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
    keywords = memory.get("keywords") or st.session_state.get("keywords", [])
    questions = memory.get("questions") or st.session_state.get("questions", [])
    category = memory.get("category") or st.session_state.get("selected_category", "추억")
    lead = ", ".join(keywords[:2]) if keywords else category
    prompt = questions[0] if questions else st.session_state.get("question", "")
    return f"{category} 분위기가 담긴 사진이에요. {lead}의 단서가 보여요. {prompt}"


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
if memory_id:
    restore_photo(memory_id)
if action:
    if action == "home":
        st.session_state.page = "home"
    elif action == "record":
        reset_flow()
        st.session_state.page = "scan_upload"
    elif action == "select" and memory_id:
        picked = next((m for m in memories if m.get("id") == memory_id), None)
        if picked:
            st.session_state.selected_memory_id = memory_id
            st.session_state.play_memory = picked
            st.session_state.memory_text = picked.get("note", "")
            st.session_state.handwriting_path = picked.get("handwriting") or handwriting_path_for(memory_id)
            st.session_state.keywords = picked.get("keywords") or st.session_state.keywords
            st.session_state.question = picked.get("question") or st.session_state.question
            st.session_state.questions = picked.get("questions") or [st.session_state.question]
            st.session_state.selected_category = picked.get("category") or st.session_state.selected_category
            st.session_state.selected_categories = picked.get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
        st.session_state.page = "video"
    elif action == "playback":
        picked = next((m for m in memories if m.get("id") == st.session_state.selected_memory_id), None)
        if picked:
            st.session_state.play_memory = picked
            st.session_state.selected_category = picked.get("category") or st.session_state.selected_category
            st.session_state.selected_categories = picked.get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
            st.session_state.page = "video"
        elif memories:
            st.session_state.play_memory = memories[0]
            st.session_state.selected_category = memories[0].get("category") or st.session_state.selected_category
            st.session_state.selected_categories = memories[0].get("categories") or ([st.session_state.selected_category] if st.session_state.selected_category else [])
            st.session_state.page = "video"
        else:
            reset_flow()
            st.session_state.page = "scan_upload"
    elif action == "scan_start":
        st.session_state.page = "scan_running"
    elif action == "write":
        if mode in ("voice", "chat", "handwriting"):
            st.session_state.write_mode = mode
        st.session_state.page = "write"
    elif action == "music":
        if note_text is not None:
            st.session_state.memory_text = note_text
        st.session_state.page = "music"
    elif action == "category_loading":
        st.session_state.page = "category_loading"
    elif action == "category_edit":
        st.session_state.page = "category_edit"
    elif action == "category_pick":
        picked_category = st.query_params.get("category")
        if picked_category:
            st.session_state.selected_categories = [picked_category]
            st.session_state.selected_category = picked_category
            st.session_state.category_direct_text = picked_category
            st.session_state.category_direct_input = picked_category
        st.session_state.page = "category_edit"
    elif action == "album_done":
        st.session_state.page = "album_done"
    elif action == "nfc_scan":
        st.session_state.page = "nfc_scan"
    elif action == "nfc_done":
        st.session_state.page = "nfc_done"
    elif action == "video":
        st.session_state.page = "video"
    elif action == "player":
        st.session_state.page = "video"
    elif action == "delete" and memory_id:
        delete_memory(memory_id)
        st.session_state.play_memory = None
        st.session_state.page = "home"
    elif action == "back":
        current = st.session_state.page
        if current in ("scan_upload", "player", "category_loading"):
            st.session_state.page = "home"
        elif current in ("scan_running", "analyzing", "scan_done"):
            st.session_state.page = "scan_upload"
        elif current == "write":
            st.session_state.page = "scan_done"
        elif current == "music":
            st.session_state.page = "write"
        elif current == "category_edit":
            st.session_state.page = "music"
        elif current == "album_done":
            st.session_state.page = "category_edit"
        elif current == "nfc_scan":
            st.session_state.page = "album_done"
        elif current == "nfc_done":
            st.session_state.page = "nfc_scan"
        elif current == "video":
            st.session_state.page = "album_done"
        else:
            st.session_state.page = "home"
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
.brand span, .splash-title span { color:#f26a2e; }

.splash { height:410px; position:relative; display:flex; justify-content:center; align-items:center; overflow:hidden; }
.splash-hit { position:absolute; inset:0; z-index:5; }
.splash-title { font-size:30px; font-weight:900; z-index:2; transform:translateY(-36px); }
.orb-main { position:absolute; width:140px; height:140px; border-radius:50%; background:#ff6a24; filter:blur(8px); left:48%; top:52%; transform:translate(-50%, -50%); }
.orb-soft { position:absolute; width:230px; height:230px; border-radius:50%; background:rgba(255,255,255,.72); left:7%; bottom:-70px; }
.orb-small { position:absolute; width:78px; height:78px; border-radius:50%; background:rgba(255,255,255,.72); border:1px solid rgba(255,255,255,.8); left:55%; top:45%; }
.dot { position:absolute; width:26px; height:26px; border-radius:50%; background:rgba(255,106,36,.18); left:39%; top:33%; }

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

.write-screen { min-height:520px; background:radial-gradient(circle at 8% 55%, rgba(255,105,33,.65), transparent 22%), radial-gradient(circle at 102% 96%, rgba(255,105,33,.65), transparent 24%), #fff; padding:24px 44px 34px; }
.write-screen.handwriting { min-height:620px; }
.write-photo { width:350px; height:200px; margin:0 auto 28px; border-radius:6px; background:#dde7f1; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#55728a; }
.write-photo img { width:100%; height:100%; object-fit:cover; }
.note-box { min-height:210px; border-radius:20px; background:#fff; box-shadow:0 14px 34px rgba(0,0,0,.08); padding:18px; }
.note-box.handwriting-note { min-height:322px; }
.method-row { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }
.method-pill { height:34px; border-radius:999px; background:#f8f8f8; box-shadow:0 6px 14px rgba(0,0,0,.08); display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:900; }
.method-pill.active { background:#050505; color:#fff; }
.memory-input { width:100%; min-height:126px; border:0; outline:0; resize:none; font-family:inherit; font-size:15px; line-height:1.6; background:transparent; color:#111; }
.memory-input::placeholder { color:#b6b6b6; }
.write-actions { display:flex; justify-content:flex-end; margin-top:18px; }
.next-button { width:132px; height:42px; border:0; border-radius:999px; background:#050505; color:#fff; box-shadow:0 10px 22px rgba(0,0,0,.2); display:flex; align-items:center; justify-content:center; font-family:inherit; font-size:13px; font-weight:900; cursor:pointer; }
.canvas-slot { height:230px; border-radius:14px; background:#fff; border:1px solid #eee; display:flex; align-items:center; justify-content:center; color:#c8c8c8; font-size:13px; font-weight:800; }
iframe[title*="streamlit_drawable_canvas"] { display:block; border-radius:14px; }
.handwriting-controls { width:660px; max-width:calc(100% - 88px); margin:10px auto 0; position:relative; z-index:21; }
.handwriting-controls .stButton > button { background:#050505; color:#fff; box-shadow:0 10px 22px rgba(0,0,0,.18); }
.handwriting-shell { width:760px; max-width:100%; margin:0 auto; padding:0 44px 0; }
.handwriting-title { font-size:13px; font-weight:900; margin-bottom:8px; color:#111; }
.handwriting-preview { width:100%; margin-top:12px; border-radius:14px; background:#fff; border:1px solid #eee; overflow:hidden; }
.handwriting-preview img { width:100%; display:block; }

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
.review-note { min-height:70px; font-size:14px; line-height:1.55; color:#111; white-space:pre-wrap; }
.music-chip { margin-top:14px; min-height:42px; border-radius:999px; background:#f5f5f5; display:flex; align-items:center; justify-content:center; gap:10px; padding:0 18px; font-size:13px; font-weight:900; }
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
.music-page-shell div[data-testid="stTextInput"] input { height:44px !important; border:0 !important; border-radius:4px !important; background:#f4f4f4 !important; padding-left:20px !important; font-size:15px !important; box-shadow:none !important; }
.music-page-shell div[data-testid="stButton"] button { height:44px !important; min-height:44px !important; border-radius:999px !important; border:0 !important; background:#050505 !important; color:#fff !important; box-shadow:0 10px 22px rgba(0,0,0,.12) !important; font-weight:900 !important; }
.music-page-shell [data-testid="stImage"] img { border-radius:0 !important; }
.music-page-shell [data-testid="stVerticalBlock"] { gap:.35rem !important; }
@media (max-width:820px) { .music-page-shell { padding:24px 24px 32px; } }

</style>
""")


page = st.session_state["page"]

if page == "splash":
    html("""
<div class="app-card splash">
  <a class="splash-hit" href="?action=home" target="_self"></a>
  <div class="dot"></div>
  <div class="orb-soft"></div>
  <div class="orb-main"></div>
  <div class="orb-small"></div>
  <div class="splash-title">Re<span>:</span>Play</div>
</div>
""")

elif page == "home":
    tiles = "".join(
        tile_html(memory, memory.get("id") == st.session_state.selected_memory_id)
        for memory in memories
    )
    tiles += '<a class="tile" href="?action=record" target="_self">+</a>'
    html(f"""
<div class="app-card home">
  <div class="brand">Re<span>:</span>Play</div>
  <div class="memory-panel">
    <div class="tile-grid">{tiles}</div>
  </div>
  <div class="bottom-actions">
    <a class="left" href="?action=record" target="_self">기억 기록하기</a>
    <a class="right" href="?action=playback" target="_self">추억 재생하기</a>
  </div>
</div>
""")

elif page == "scan_upload":
    photo = photo_markup()
    memory_query = f"&memory={st.session_state.memory_id}" if st.session_state.memory_id else ""
    html(f"""
<div class="app-card flow">
  <div class="topbar"><a class="back" href="?action=back" target="_self">‹</a><div class="center"></div><div class="guide">가이드</div></div>
  <div class="scan-card"><div class="scan-frame">{photo if st.session_state.photo_path else '사진을 스캔 공간에 넣어주세요'}</div></div>
  <div class="down-mark">⌄</div>
  <a class="black-pill" href="?action=scan_start{memory_query}" target="_self">스캔 시작하기</a>
</div>
""")
    file = st.file_uploader("사진 선택", type=["jpg", "jpeg", "png"], label_visibility="collapsed")
    if file:
        signature = f"{file.name}:{file.size}"
        if signature == st.session_state.upload_signature:
            st.stop()
        st.session_state.upload_signature = signature
        save_photo(file)
        st.rerun()

elif page == "scan_running":
    progress_area = st.empty()
    for percent in range(0, 101, 4):
        photo = photo_markup("photo-fill scan-blur")
        progress_area.markdown(f"""
<div class="app-card flow">
  <div class="topbar"><a class="back" href="?action=back" target="_self">‹</a><div class="center"></div><div class="guide">가이드</div></div>
  <div class="scan-card"><div class="scan-frame">{photo}<div class="scan-line live" style="--scan-y:{percent}%"></div><div class="scan-label">스캔중...</div></div></div>
  <div class="scan-progress-row"><div class="scan-progress-track"><div class="scan-progress-fill" style="width:{percent}%"></div></div><div>{percent}%</div></div>
  <div class="down-mark">⌄</div>
  <div class="black-pill">스캔 시작하기</div>
</div>
""", unsafe_allow_html=True)
        time.sleep(0.09)
    update_photo_analysis()
    go("analyzing")

elif page == "analyzing":
    src = image_src(st.session_state.photo_path)
    img = f'<img src="{src}">' if src else ""
    k1, k2, k3 = st.session_state.keywords
    html(f"""
<div class="app-card flow">
  <div class="topbar"><a class="back" href="?action=back" target="_self">‹</a><div class="center"></div><div class="guide">가이드</div></div>
  <div class="analysis-wrap">
    <div class="photo-paper">
      {img}
      <div class="keyword kw-left">{k1}</div>
      <div class="keyword kw-top">{k2}</div>
      <div class="keyword kw-right">{k3}</div>
    </div>
    <div class="analysis-title">AI가 추억을 분석하고 있어요</div>
    <div class="analysis-sub">잠시만 기다려주세요</div>
    <div class="progress-row"><div class="progress-track"><div class="progress-fill"></div><div class="progress-dot"></div></div><div>100%</div></div>
  </div>
</div>
""")
    time.sleep(2.4)
    go("scan_done")

elif page == "scan_done":
    src = image_src(st.session_state.photo_path)
    img = f'<img src="{src}">' if src else ""
    memory_query = f"&memory={st.session_state.memory_id}" if st.session_state.memory_id else ""
    questions = st.session_state.get("questions") or [st.session_state.question]
    question_cards = "".join(
        f'<div class="question-card">{escape(question)}</div>'
        for question in questions[:3]
    )
    html(f"""
<div class="app-card flow">
  <div class="topbar"><a class="back" href="?action=back" target="_self">‹</a><div class="center"></div><div class="guide">가이드</div></div>
  <div class="done-grid">
    <div>
      <div class="done-title">사진 스캔이 완료되었어요!</div>
      <div class="done-sub">사진을 분석했어요. 이제 진짜 추억을 이야기해볼까요?</div>
      <div class="done-panel"><div class="photo-paper">{img}</div></div>
    </div>
    <div>
      <div class="done-title">AI가 추억을 되살릴 질문을 준비했어요</div>
      <div class="done-sub">이제 질문을 보며 기억을 떠올려 보세요</div>
      <div class="done-panel">{question_cards}</div>
    </div>
  </div>
  <a class="wide-black" href="?action=write{memory_query}" target="_self">기록 시작하기<small>이제 추억을 기록해볼까요?</small></a>
</div>
""")

elif page == "write":
    # 기록 화면: 왼쪽 사진/AI 요약 + 오른쪽 손글씨 패드
    if not st.session_state.photo_path and st.session_state.memory_id:
        restore_photo(st.session_state.memory_id)

    if "pen_color" not in st.session_state:
        st.session_state.pen_color = "#000000"
    if st.session_state.write_mode not in ("voice", "chat", "handwriting"):
        st.session_state.write_mode = "handwriting"
    selected_pen_name = {
        "#000000": "black",
        "#1e88e5": "blue",
        "#44c767": "green",
        "#f8c51b": "yellow",
        "#f34b3f": "red",
    }.get(st.session_state.get("pen_color", "#000000").lower(), "black")

    st.markdown("""
<style>
.stApp { background:#efefef !important; }
.block-container {
    max-width: 800px !important;
    min-height: 544px !important;
    margin: 0 auto !important;
    padding: 30px 28px 28px !important;
    background:
      radial-gradient(circle at 0% 88%, rgba(239,90,40,.22), transparent 34%),
      radial-gradient(circle at 100% 50%, rgba(239,90,40,.14), transparent 38%),
      #fffaf7 !important;
    box-shadow: 0 18px 42px rgba(0,0,0,.045) !important;
    overflow: hidden !important;
    position: relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
div[data-testid="stVerticalBlock"] { gap:.34rem !important; }
div[data-testid="stHorizontalBlock"] { gap:1.0rem !important; }
.write-topbar { display:grid; grid-template-columns:38px 1fr 300px; align-items:center; margin-bottom:8px; }
.write-back { width:38px; height:38px; border-radius:50%; background:#f1f1f1; display:flex; align-items:center; justify-content:center; color:#0b67b2; font-size:28px; font-weight:900; text-decoration:none; }
.write-handle { width:24px; height:3px; border-radius:999px; background:#cfc7c2; justify-self:center; }
.write-title-dot { display:flex; align-items:center; gap:7px; font-size:11px; font-weight:900; color:#111; margin:0 0 20px 0; }
.write-title-dot span { width:6px; height:6px; border-radius:50%; background:#ef5a28; display:inline-block; }
.era-chip { display:inline-flex; align-items:center; gap:6px; height:28px; padding:0 14px; border-radius:999px; background:#fff; box-shadow:0 8px 18px rgba(0,0,0,.07); color:#222; font-size:9px; font-weight:900; margin-bottom:25px; }
.record-photo-card { width:100%; height:286px; background:#fff; border-radius:24px; display:flex; align-items:center; justify-content:center; overflow:hidden; box-shadow:0 14px 30px rgba(0,0,0,.06); padding:15px; }
.record-photo-card img { width:100%; height:100%; object-fit:contain; display:block; filter:saturate(.72) contrast(.94); }
.ai-summary-card { margin-top:28px; width:170px; min-height:70px; border-radius:12px; background:rgba(255,255,255,.82); box-shadow:0 10px 24px rgba(0,0,0,.045); padding:11px 13px; font-size:8px; line-height:1.55; color:#222; }
.ai-summary-title { font-size:10px; font-weight:900; margin-bottom:7px; color:#111; }
.ai-summary-title span { color:#ef5a28; }
.mode-link-row { min-height:33px; }
.st-key-write_back_button { position:absolute !important; left:28px !important; top:30px !important; width:38px !important; z-index:20 !important; }
.st-key-write_back_button button { width:38px !important; height:38px !important; padding:0 !important; border:0 !important; border-radius:50% !important; background:#f1f1f1 !important; color:#0b67b2 !important; box-shadow:none !important; font-size:28px !important; font-weight:900 !important; line-height:1 !important; }
.st-key-write_mode_voice_button,
.st-key-write_mode_chat_button,
.st-key-write_mode_handwriting_button { position:absolute !important; top:30px !important; z-index:20 !important; width:84px !important; }
.st-key-write_mode_voice_button { right:212px !important; }
.st-key-write_mode_chat_button { right:120px !important; }
.st-key-write_mode_handwriting_button { right:28px !important; }
.st-key-write_mode_voice_button button,
.st-key-write_mode_chat_button button,
.st-key-write_mode_handwriting_button button { width:84px !important; min-width:84px !important; height:33px !important; padding:0 10px !important; border:0 !important; border-radius:999px !important; background:#fff !important; color:#111 !important; box-shadow:0 8px 20px rgba(0,0,0,.075) !important; font-size:11px !important; font-weight:900 !important; }
.pad-card { height:418px; border-radius:14px; background:#fff; box-shadow:0 14px 32px rgba(0,0,0,.14); border:1px solid #e9e9e9; padding:14px; overflow:hidden; }
div[data-testid="stCustomComponentV1"]:has(iframe[title*="streamlit_drawable_canvas"]),
.element-container:has(iframe[title*="streamlit_drawable_canvas"]) {
    height:412px !important;
    max-height:412px !important;
    overflow:hidden !important;
}
iframe[title*="streamlit_drawable_canvas"] {
    width:376px !important;
    max-width:100% !important;
    height:412px !important;
    margin:0 !important;
    position:static !important;
    z-index:auto !important;
    border-radius:14px !important;
    border:1px solid #e5e5e5 !important;
    box-shadow:0 14px 32px rgba(0,0,0,.14) !important;
    background:#fff !important;
}
.color-toolbar-title {
    width:34px;
    height:44px;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:13px;
    margin:0 0 82px 0;
    color:#111;
    border-bottom:1px solid #e9e9e9;
    position:relative;
    z-index:4;
}
div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) {
    flex:0 0 34px !important;
    width:34px !important;
    min-width:34px !important;
    max-width:34px !important;
    margin-left:-40px !important;
    position:relative !important;
    z-index:8 !important;
}
div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) div[data-testid="stVerticalBlock"] {
    width:34px !important;
    min-width:34px !important;
    max-width:34px !important;
    height:300px !important;
    min-height:300px !important;
    border-radius:999px !important;
    background:#fff !important;
    border:1px solid #ededed !important;
    box-shadow:0 8px 16px rgba(0,0,0,.12) !important;
    display:flex !important;
    flex-direction:column !important;
    align-items:center !important;
    justify-content:flex-start !important;
    gap:0 !important;
    padding:9px 0 14px !important;
    margin-top:66px !important;
    overflow:hidden !important;
}
.st-key-pen_color_black,
.st-key-pen_color_blue,
.st-key-pen_color_green,
.st-key-pen_color_yellow,
.st-key-pen_color_red {
    width:34px !important;
    height:22px !important;
    min-height:22px !important;
    margin:0 auto !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
    position:relative !important;
    z-index:5 !important;
    padding:0 !important;
}
.st-key-pen_color_black > div,
.st-key-pen_color_blue > div,
.st-key-pen_color_green > div,
.st-key-pen_color_yellow > div,
.st-key-pen_color_red > div,
.st-key-pen_color_black div[data-testid="stButton"],
.st-key-pen_color_blue div[data-testid="stButton"],
.st-key-pen_color_green div[data-testid="stButton"],
.st-key-pen_color_yellow div[data-testid="stButton"],
.st-key-pen_color_red div[data-testid="stButton"] {
    width:12px !important;
    height:12px !important;
    min-width:12px !important;
    min-height:12px !important;
    display:flex !important;
    align-items:center !important;
    justify-content:center !important;
    margin:0 auto !important;
    padding:0 !important;
}
.st-key-pen_color_black button,
.st-key-pen_color_blue button,
.st-key-pen_color_green button,
.st-key-pen_color_yellow button,
.st-key-pen_color_red button {
    width:12px !important;
    height:12px !important;
    min-width:12px !important;
    min-height:12px !important;
    padding:0 !important;
    border:0 !important;
    border-radius:50% !important;
    box-shadow:none !important;
    color:transparent !important;
    overflow:hidden !important;
    line-height:1 !important;
    margin:0 !important;
    display:block !important;
}
.st-key-pen_color_black button > div,
.st-key-pen_color_blue button > div,
.st-key-pen_color_green button > div,
.st-key-pen_color_yellow button > div,
.st-key-pen_color_red button > div {
    width:12px !important;
    height:12px !important;
    min-width:12px !important;
    min-height:12px !important;
    margin:0 !important;
    padding:0 !important;
}
.st-key-pen_color_black button { background:#000000 !important; }
.st-key-pen_color_blue button { background:#1e88e5 !important; }
.st-key-pen_color_green button { background:#44c767 !important; }
.st-key-pen_color_yellow button { background:#f8c51b !important; }
.st-key-pen_color_red button { background:#f34b3f !important; }
.st-key-pen_color_black button p,
.st-key-pen_color_blue button p,
.st-key-pen_color_green button p,
.st-key-pen_color_yellow button p,
.st-key-pen_color_red button p {
    font-size:0 !important;
    line-height:0 !important;
    width:0 !important;
    height:0 !important;
    overflow:hidden !important;
}
.record-next { width:104px; margin:9px 0 0 auto; }
.record-next button { height:34px !important; border-radius:999px !important; border:0 !important; background:#fff !important; color:#111 !important; box-shadow:0 10px 22px rgba(0,0,0,.12) !important; font-size:14px !important; font-weight:800 !important; }
.record-save { width:156px; margin:8px 0 0 auto; }
.record-save button { height:32px !important; border-radius:999px !important; border:0 !important; background:#fff !important; color:#111 !important; box-shadow:0 8px 18px rgba(0,0,0,.10) !important; font-size:12px !important; font-weight:900 !important; }
.text-record-card textarea { min-height:356px !important; border:0 !important; box-shadow:none !important; background:#fff !important; font-size:15px !important; line-height:1.6 !important; }
.voice-record-box { height:372px; border-radius:13px; background:#fff; box-shadow:0 14px 32px rgba(0,0,0,.12); display:flex; flex-direction:column; align-items:center; justify-content:center; color:#555; text-align:center; gap:10px; }
.voice-record-icon { width:56px; height:56px; border-radius:50%; background:#fff3ec; display:flex; align-items:center; justify-content:center; font-size:25px; color:#ef5a28; }
@media (max-width: 820px) {
  .block-container { max-width: 790px !important; min-height:544px !important; padding:24px 28px 24px !important; }
  .write-topbar { grid-template-columns:42px 1fr 300px; }
  .record-photo-card { height:250px; }
  .pad-card { height:350px; }
  div[data-testid="stCustomComponentV1"]:has(iframe[title*="streamlit_drawable_canvas"]),
  .element-container:has(iframe[title*="streamlit_drawable_canvas"]) { height:392px !important; max-height:392px !important; }
  iframe[title*="streamlit_drawable_canvas"] { width:360px !important; height:392px !important; }
  div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) { margin-left:-36px !important; }
  div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) div[data-testid="stVerticalBlock"] { height:284px !important; min-height:284px !important; }
  .st-key-pen_color_black,
  .st-key-pen_color_blue,
  .st-key-pen_color_green,
  .st-key-pen_color_yellow,
  .st-key-pen_color_red { margin-left:auto !important; margin-right:auto !important; }
}
</style>
""", unsafe_allow_html=True)
    st.markdown(f"""
<style>
.st-key-pen_color_{selected_pen_name} button {{
    outline:1.5px solid rgba(0,0,0,.32) !important;
    outline-offset:1px !important;
}}
.st-key-write_mode_{st.session_state.write_mode}_button button {{
    background:#050505 !important;
    color:#fff !important;
}}
</style>
""", unsafe_allow_html=True)

    write_mode = st.session_state.write_mode

    html('''
<div class="write-topbar">
  <div></div>
  <div class="write-handle"></div>
  <div class="mode-link-row"></div>
</div>
''')
    if st.button("‹", key="write_back_button"):
        preserve_write_inputs()
        go("scan_done")
    st.button("음성 기록", key="write_mode_voice_button", on_click=switch_write_mode, args=("voice",))
    st.button("텍스트", key="write_mode_chat_button", on_click=switch_write_mode, args=("chat",))
    st.button("손글씨", key="write_mode_handwriting_button", on_click=switch_write_mode, args=("handwriting",))

    left_col, right_col = st.columns([0.48, 0.52])

    src = image_src(st.session_state.photo_path)
    img = f'<img src="{src}">' if src else '<div style="color:#9aa; font-size:34px;">▧</div>'
    keywords = st.session_state.get("keywords") or ["추억사진", "인물사진", "옛날 분위기"]
    questions = st.session_state.get("questions") or [st.session_state.get("question", "이 사진을 찍던 날은 어떤 날이었나요?")]
    era_text = "1980년대 후반(추정)"
    summary_text = f"{', '.join(keywords[:2])} 분위기가 느껴지는 사진입니다. {questions[0]}"

    with left_col:
        html(f'''
<div class="write-title-dot"><span></span>기록 중</div>
<div class="era-chip">▣ {era_text}</div>
<div class="record-photo-card">{img}</div>
<div class="ai-summary-card">
  <div class="ai-summary-title"><span>✦</span> AI 분석 요약</div>
  {escape(summary_text)}
</div>
''')

    canvas_result = None
    with right_col:
        if write_mode == "handwriting":
            canvas_key = current_canvas_key()
            canvas_state = st.session_state.get(canvas_key)
            if isinstance(canvas_state, dict) and canvas_state.get("raw"):
                st.session_state.handwriting_json = canvas_state["raw"]
            canvas_col, tool_col = st.columns([0.92, 0.08])
            with canvas_col:
                canvas_result = st_canvas(
                    fill_color="rgba(255, 255, 255, 0)",
                    stroke_width=3,
                    stroke_color=st.session_state.get("pen_color", "#000000"),
                    background_color="#ffffff",
                    height=412,
                    width=376,
                    drawing_mode="freedraw",
                    initial_drawing=st.session_state.get("handwriting_json"),
                    update_streamlit=True,
                    display_toolbar=False,
                    key=canvas_key,
                )
                if canvas_result is not None:
                    if canvas_result.json_data:
                        st.session_state.handwriting_json = canvas_result.json_data
                    if canvas_has_ink(canvas_result.image_data):
                        save_handwriting(canvas_result.image_data)
            with tool_col:
                colors = [
                    (" ", "#000000", "black"),
                    (" ", "#1e88e5", "blue"),
                    (" ", "#44c767", "green"),
                    (" ", "#f8c51b", "yellow"),
                    (" ", "#f34b3f", "red"),
                ]
                st.markdown('<div class="color-toolbar-title">✎</div>', unsafe_allow_html=True)
                for label, hex_color, name in colors:
                    st.button(
                        label,
                        key=f"pen_color_{name}",
                        on_click=set_pen_color,
                        args=(hex_color, canvas_key),
                    )
        elif write_mode == "voice":
            html('''
<div class="voice-record-box">
  <div class="voice-record-icon">●</div>
  <div style="font-weight:900; font-size:16px;">음성 기록</div>
  <div style="font-size:12px; line-height:1.55; color:#777;">녹음한 목소리를 텍스트 기록으로 변환해요.</div>
</div>
''')
            voice_file = st.audio_input("음성 기록", label_visibility="collapsed")
            if voice_file:
                st.audio(voice_file)
                if st.button("텍스트로 변환하기", key="transcribe_voice_record", use_container_width=True):
                    text = transcribe_audio(voice_file)
                    if text:
                        st.session_state.memory_text = text
                        st.success("음성 기록이 텍스트로 변환됐어요.")
                    else:
                        st.warning("음성을 텍스트로 변환하지 못했어요.")
            if st.session_state.memory_text:
                st.session_state.memory_text = st.text_area(
                    "변환된 텍스트",
                    value=st.session_state.memory_text,
                    key="voice_transcript_preview",
                    label_visibility="collapsed",
                    height=96,
                )
        else:
            st.markdown('<div class="pad-card text-record-card">', unsafe_allow_html=True)
            st.session_state.memory_text = st.text_area(
                "텍스트 입력",
                value=st.session_state.memory_text,
                placeholder="이 사진에 대한 기억을 적어주세요.",
                label_visibility="collapsed",
                key="memory_text_area_record",
            )
            st.markdown('</div>', unsafe_allow_html=True)

        _, next_col = st.columns([0.72, 0.28])
        with next_col:
            if st.button("다음으로", key="write_next_record", use_container_width=True):
                preserve_write_inputs()
                if write_mode == "handwriting" and canvas_result is not None:
                    if canvas_has_ink(canvas_result.image_data):
                        save_handwriting(canvas_result.image_data)
                    if canvas_result.json_data:
                        st.session_state.handwriting_json = canvas_result.json_data
                go("music")

elif page == "music":
    # 패드 시연용 음악 선택 화면: 한 카드 안에서 사진/LP + 검색/추천/선택/저장을 모두 처리
    if not st.session_state.photo_path and st.session_state.memory_id:
        restore_photo(st.session_state.memory_id)

    if not st.session_state.spotify_tracks:
        base_query = " ".join(st.session_state.get("keywords", [])) or "korean old pop memories"
        st.session_state.spotify_tracks = spotify_recommendations(base_query)

    st.markdown("""
<style>
/* 음악 선택 화면 전용: 기존 앱처럼 회색 바깥 + 흰 네모 카드 안에 압축 배치 */
.stApp { background:#efefef !important; }
.block-container {
    max-width: 980px !important;
    min-height: 610px !important;
    margin: 18px auto 0 !important;
    padding: 22px 36px 34px !important;
    background: radial-gradient(circle at 92% 104%, rgba(239,90,40,.18), transparent 35%), #ffffff !important;
    box-shadow: 0 18px 42px rgba(0,0,0,.055) !important;
    overflow: visible !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
div[data-testid="stVerticalBlock"] { gap: 0.24rem !important; }
div[data-testid="stHorizontalBlock"] { gap: 1.25rem !important; }
.music-back {
    width:30px; height:30px; border-radius:50%; background:#f1f1f1;
    display:flex; align-items:center; justify-content:center;
    color:#0b67b2; font-size:23px; font-weight:900; text-decoration:none;
    margin: 0 0 12px 0;
}
.music-photo-box {
    width:100%; height:188px; background:#ffffff; overflow:hidden;
    display:flex; align-items:center; justify-content:center; color:#6a8396; font-size:24px;
}
.music-photo-box img { width:100%; height:100%; object-fit:contain; display:block; background:#ffffff; }
.music-turntable {
    height:230px; margin-top:14px; background:#e8e8e8; position:relative; overflow:hidden;
    display:flex; align-items:center; justify-content:center;
}
.music-record {
    width:178px; height:178px; border-radius:50%; position:relative;
    background: radial-gradient(circle, #151519 0 48%, #060607 73%, #151515 100%);
    box-shadow: inset -10px -14px 24px rgba(255,255,255,.04), inset 10px 10px 20px rgba(0,0,0,.28);
}
.music-record::after {
    content:""; position:absolute; left:50%; top:50%; width:56px; height:56px;
    margin:-28px; border-radius:50%; background:#f35f27; border:3px solid #111;
}
.music-caption {
    color:#747474; font-size:14px; margin: 16px 0 18px; line-height:1.25;
}
.music-row-title {
    font-size:14px; font-weight:900; line-height:1.18;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-top:6px;
}
.music-row-title.selected { color:#ef5a28; }
.music-row-artist {
    font-size:11px; color:#777; margin-top:5px; line-height:1.1;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.music-cover-empty { width:42px; height:42px; background:#eef3f7; border-radius:6px; }
.music-line { height:1px; background:#eeeeee; margin: 8px 0 10px; }
.music-selected {
    margin: 12px 0 12px;
    padding: 10px 13px;
    border-radius:13px;
    background:rgba(239,90,40,.10);
    border-left:4px solid #ef5a28;
    font-size:13px;
    font-weight:900;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
    display:block;
}
.music-hint { margin-top:12px; color:#777; font-size:13px; }
/* 검색창/버튼 */
div[data-testid="stTextInput"] { margin-bottom: 0 !important; }
div[data-testid="stTextInput"] input {
    height:46px !important; min-height:46px !important;
    border:0 !important; border-radius:8px !important; background:#f5f5f5 !important;
    padding-left:18px !important; font-size:17px !important; box-shadow:none !important;
}
.stButton > button {
    height:46px !important; min-height:46px !important; padding:0 12px !important;
    border-radius:999px !important; border:0 !important;
    background:#fff !important; color:#111 !important;
    box-shadow:0 8px 18px rgba(0,0,0,.09) !important;
    font-size:16px !important; font-weight:900 !important;
}
.stButton > button:hover, .stButton > button:focus { border:0 !important; background:#fff !important; color:#111 !important; }
.track-plus { width:48px; margin-left:auto; }
.track-plus .stButton > button {
    width:48px !important;
    height:48px !important;
    min-height:48px !important;
    padding:0 !important;
    border-radius:999px !important;
    font-size:21px !important;
    line-height:1 !important;
}
div[data-testid="stAudio"] { margin: 6px 0 10px !important; }
/* 앨범 커버 */
[data-testid="stImage"] img { border-radius:5px !important; object-fit:cover !important; }
/* 작은 화면에서도 카드 안에서 유지 */
@media (max-width: 820px) {
    .block-container { max-width: 760px !important; min-height: 520px !important; padding: 16px 24px 22px !important; }
    div[data-testid="stHorizontalBlock"] { gap: .85rem !important; }
    .music-photo-box { height:142px; }
    .music-turntable { height:176px; }
    .music-record { width:138px; height:138px; }
    .music-row-title { font-size:12px; }
    .music-row-artist { font-size:10px; }
    div[data-testid="stTextInput"] input, .stButton > button { height:38px !important; min-height:38px !important; font-size:13px !important; }
}
</style>
""", unsafe_allow_html=True)

    st.markdown('<a class="music-back" href="?action=back" target="_self">‹</a>', unsafe_allow_html=True)

    left_col, right_col = st.columns([0.45, 0.55], gap="large")

    with left_col:
        if st.session_state.photo_path and os.path.exists(st.session_state.photo_path):
            src = image_src(st.session_state.photo_path)
            st.markdown(f'<div class="music-photo-box"><img src="{src}"></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="music-photo-box">▧</div>', unsafe_allow_html=True)
        st.markdown('<div class="music-turntable"><div class="music-record"></div></div>', unsafe_allow_html=True)

    with right_col:
        search_cols = st.columns([0.76, 0.24], gap="small")
        with search_cols[0]:
            query = st.text_input(
                "노래 검색",
                value=st.session_state.spotify_query,
                placeholder="검색",
                label_visibility="collapsed",
                key="music_search_input_tight_card",
            )
        with search_cols[1]:
            search_clicked = st.button("검색", key="music_search_button_tight_card", use_container_width=True)

        if search_clicked:
            st.session_state.spotify_query = query.strip()
            search_term = st.session_state.spotify_query or "korean old pop memories"
            st.session_state.spotify_tracks = spotify_recommendations(search_term)
            st.session_state.selected_track = None
            st.session_state.music_show_more = False
            st.rerun()

        caption = (
            f'검색 결과: <b>{escape(st.session_state.spotify_query)}</b>'
            if st.session_state.spotify_query
            else 'AI가 사진 분위기에 맞춰 추천한 노래예요.'
        )
        st.markdown(f'<div class="music-caption">{caption}</div>', unsafe_allow_html=True)

        tracks = st.session_state.spotify_tracks or []
        # 카드 안에서 안 겹치도록 4개만 표시
        display_count = len(tracks) if st.session_state.music_show_more else 4
        visible_tracks = tracks[:display_count]
        for index, track in enumerate(visible_tracks):
            title = track.get("title", "")
            artist = track.get("artist", "")
            cover_url = track.get("image", "")
            selected = (
                st.session_state.selected_track
                and st.session_state.selected_track.get("title") == title
                and st.session_state.selected_track.get("artist") == artist
            )

            row_cols = st.columns([0.13, 0.72, 0.15], gap="small")
            with row_cols[0]:
                if cover_url:
                    st.image(cover_url, width=42)
                else:
                    st.markdown('<div class="music-cover-empty"></div>', unsafe_allow_html=True)
            with row_cols[1]:
                selected_cls = " selected" if selected else ""
                st.markdown(
                    f'<div class="music-row-title{selected_cls}">{escape(title)}</div>'
                    f'<div class="music-row-artist">{escape(artist)}</div>',
                    unsafe_allow_html=True,
                )
            with row_cols[2]:
                st.markdown('<div class="track-plus">', unsafe_allow_html=True)
                if st.button("✓" if selected else "+", key=f"choose_track_tight_card_{index}", use_container_width=True):
                    st.session_state.selected_track = track
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
            st.markdown('<div class="music-line"></div>', unsafe_allow_html=True)

        if len(tracks) > 4:
            more_label = "접기" if st.session_state.music_show_more else "더 많은 음악 보기"
            if st.button(more_label, key="toggle_more_music", use_container_width=True):
                st.session_state.music_show_more = not st.session_state.music_show_more
                st.rerun()

        selected_track = st.session_state.selected_track
        if selected_track:
            st.markdown(
                f'<div class="music-selected">선택된 노래: '
                f'{escape(selected_track.get("title", ""))} - {escape(selected_track.get("artist", ""))}</div>',
                unsafe_allow_html=True,
            )
            preview_url = selected_track.get("preview_url")
            if preview_url:
                st.audio(preview_url, format="audio/mp3")
            else:
                st.markdown('<div class="music-hint">이 곡은 Spotify 미리듣기를 제공하지 않아요.</div>', unsafe_allow_html=True)
            st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
            if st.button("이 노래로 저장하기", key="save_selected_music_tight_card", use_container_width=True):
                save_memory()
                go("category_loading")
        else:
            st.markdown('<div class="music-hint">노래 오른쪽 +를 누르면 저장할 노래로 선택돼요.</div>', unsafe_allow_html=True)

elif page == "category_loading":
    st.markdown("""
<style>
.stApp { background:#efefef !important; }
.block-container { max-width:760px !important; min-height:430px !important; margin:24px auto 0 !important; padding:0 !important; background:#fff !important; overflow:hidden !important; }
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
.category-loading { height:430px; position:relative; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; background:#fff; overflow:hidden; }
.category-loading::after { content:""; position:absolute; width:210px; height:210px; left:50%; bottom:-70px; transform:translateX(-50%); border-radius:50%; background:radial-gradient(circle, rgba(255,104,32,.85), rgba(255,104,32,.25) 32%, rgba(255,104,32,0) 72%); filter:blur(3px); }
.ai-chip { height:28px; padding:0 18px; border-radius:999px; background:#fff; box-shadow:0 8px 18px rgba(0,0,0,.08); display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:900; margin-bottom:24px; position:relative; z-index:2; }
.category-loading h2 { margin:0; font-size:18px; line-height:1.55; position:relative; z-index:2; }
</style>
""", unsafe_allow_html=True)
    html("""
<div class="category-loading">
  <div class="ai-chip">AI 생성</div>
  <h2>AI가 당신의 추억을 분석해<br>카테고리를 추천했어요</h2>
</div>
""")
    time.sleep(2.4)
    go("category_edit")

elif page == "category_edit":
    options = category_candidates()
    selected_categories = ensure_selected_categories()
    if "category_direct_text" not in st.session_state:
        st.session_state.category_direct_text = ""
    chip_classes = ["c1", "c2", "c3", "c4", "c5"]
    selected_categories = ensure_selected_categories()
    selected_tag_html = "".join(
        f'<span class="direct-tag">#{escape(category)}</span>'
        for category in selected_categories
    )
    chip_button_css = ""
    for index, option in enumerate(options):
        selected = option in selected_categories
        cls = chip_classes[index]
        chip_button_css += f"""
.st-key-category_chip_{index} {{
    position:absolute !important;
    z-index:5 !important;
}}
.st-key-category_chip_{index} button {{
    min-width:112px !important;
    height:30px !important;
    padding:0 20px !important;
    border:0 !important;
    border-radius:999px !important;
    background:{'#ffd0a9' if selected else '#fff'} !important;
    color:#111 !important;
    box-shadow:0 9px 20px {'rgba(255,104,32,.22)' if selected else 'rgba(0,0,0,.10)'} !important;
    font-size:12px !important;
    font-weight:900 !important;
}}
"""
        if cls == "c1":
            chip_button_css += f".st-key-category_chip_{index} {{ left:260px !important; top:160px !important; }}\n"
        elif cls == "c2":
            chip_button_css += f".st-key-category_chip_{index} {{ right:165px !important; top:190px !important; }}\n"
        elif cls == "c3":
            chip_button_css += f".st-key-category_chip_{index} {{ left:180px !important; top:238px !important; }}\n"
        elif cls == "c4":
            chip_button_css += f".st-key-category_chip_{index} {{ right:185px !important; top:256px !important; }}\n"
        else:
            chip_button_css += f".st-key-category_chip_{index} {{ left:310px !important; top:290px !important; }}\n"
    st.markdown("""
<style>
.stApp { background:#efefef !important; }
.block-container {
    max-width:760px !important;
    min-height:540px !important;
    margin:24px auto 0 !important;
    padding:0 !important;
    background:radial-gradient(circle at 47% 45%, rgba(255,104,32,.68), rgba(255,104,32,.22) 16%, transparent 32%), #fffaf7 !important;
    overflow:hidden !important;
    position:relative !important;
}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
.category-edit-shell { position:relative; height:540px; padding:32px 54px 34px; }
.post-handle { width:24px; height:3px; border-radius:999px; background:#cfc7c2; margin:0 auto 54px; }
.category-title { text-align:center; font-size:20px; font-weight:900; margin-bottom:18px; }
.category-orb { position:absolute; left:50%; top:180px; width:170px; height:170px; transform:translateX(-50%); border-radius:50%; background:radial-gradient(circle, rgba(255,104,32,.82), rgba(255,104,32,.24) 42%, rgba(255,104,32,0) 72%); filter:blur(2px); }
.direct-category-card { position:absolute; left:54px; right:54px; bottom:100px; min-height:92px; border-radius:9px; background:#fff; box-shadow:0 9px 18px rgba(0,0,0,.12); padding:16px 18px; font-size:11px; }
.direct-title { font-size:12px; font-weight:900; margin-bottom:6px; }
.direct-tag-row { display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; min-height:28px; max-width:360px; }
.direct-tag { height:28px; min-width:92px; padding:0 18px; border-radius:999px; background:#fff; box-shadow:0 8px 16px rgba(0,0,0,.08); display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:900; }
.category-input-wrap { display:contents !important; }
.st-key-category_direct_input {
    position:absolute !important;
    left:410px !important;
    top:382px !important;
    width:250px !important;
    max-width:250px !important;
    height:32px !important;
    z-index:7 !important;
}
.st-key-category_direct_input > div,
.st-key-category_direct_input div[data-testid="stTextInput"],
.st-key-category_direct_input div[data-testid="stTextInput"] > div,
.st-key-category_direct_input div[data-testid="stTextInputRootElement"],
.st-key-category_direct_input div[data-testid="stTextInput"],
.st-key-category_direct_input div[data-baseweb="input"] {
    width:100% !important;
    max-width:100% !important;
    height:32px !important;
    min-height:32px !important;
    margin:0 !important;
    background:transparent !important;
    border:0 !important;
    outline:0 !important;
    box-shadow:none !important;
    padding:0 !important;
}
.st-key-category_direct_input div[data-baseweb="input"] > div,
.st-key-category_direct_input div[data-baseweb="input"] * {
    background:transparent !important;
    border:0 !important;
    box-shadow:none !important;
}
.st-key-category_direct_input input {
    width:100% !important;
    height:32px !important;
    min-height:32px !important;
    border:0 !important;
    border-radius:0 !important;
    background:transparent !important;
    padding:0 !important;
    color:#111 !important;
    font-size:12px !important;
    font-weight:900 !important;
    box-shadow:none !important;
    outline:0 !important;
}
.st-key-category_direct_input input::placeholder { color:#999 !important; font-weight:700 !important; }
.category-direct-add { display:contents !important; }
.st-key-category_direct_add_button { display:none !important; }
.category-save { position:absolute; left:54px; right:54px; bottom:28px; }
.category-save button { height:62px !important; border-radius:999px !important; background:#050505 !important; color:#fff !important; border:0 !important; box-shadow:0 12px 24px rgba(0,0,0,.20) !important; font-weight:900 !important; font-size:16px !important; }
.category-save button::after { content:"추억 기록에 어울리는 음악을 추가해보세요."; display:block; margin-top:5px; font-size:10px; font-weight:500; color:#ddd; }
""" + chip_button_css + """
</style>
""", unsafe_allow_html=True)
    html(f"""
<div class="category-edit-shell">
  <div class="post-handle"></div>
  <div class="category-title">AI 추천 카테고리</div>
  <div class="category-orb"></div>
  <div class="direct-category-card">
    <div class="direct-title">직접 추가 및 수정</div>
    원하는 카테고리를 직접 수정하거나 추가할 수 있어요.
    <div class="direct-tag-row">{selected_tag_html}</div>
  </div>
</div>
""")
    for index, option in enumerate(options):
        selected = option in ensure_selected_categories()
        label = f"✓ {option}" if selected else option
        st.button(
            label,
            key=f"category_chip_{index}",
            on_click=toggle_category,
            args=(option,),
        )
    st.markdown('<div class="category-input-wrap">', unsafe_allow_html=True)
    direct_category = st.text_input(
        "카테고리 직접 입력",
        value=st.session_state.category_direct_text,
        label_visibility="collapsed",
        key="category_direct_input",
        placeholder="직접 추가할 카테고리",
        on_change=add_direct_category,
    ).strip()
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div class="category-direct-add">', unsafe_allow_html=True)
    st.button("추가", key="category_direct_add_button", on_click=add_direct_category)
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div class="category-save">', unsafe_allow_html=True)
    if st.button("선택한 카테고리로 저장하기", key="save_category_button", use_container_width=True):
        if direct_category:
            categories = ensure_selected_categories()
            if direct_category not in categories:
                categories.append(direct_category)
            st.session_state.selected_categories = categories
        sync_selected_category()
        save_memory()
        go("album_done")
    st.markdown('</div>', unsafe_allow_html=True)

elif page == "album_done":
    memory = active_memory()
    photo_path = active_photo_path(memory)
    photo_src = image_src(photo_path)
    photo = f'<img src="{photo_src}">' if photo_src else "▧"
    track = memory.get("selected_track") or st.session_state.selected_track or {}
    track_title = escape(track.get("title") or "노래 제목")
    track_artist = escape(track.get("artist") or "가수")
    preview_url = track.get("preview_url") or ""
    song_audio = (
        f'<audio class="mini-audio" controls src="{escape(preview_url, quote=True)}"></audio>'
        if preview_url
        else '<div class="fake-player"><div class="fake-line"><span></span></div><div class="fake-controls">× ‹ ● › ↻</div></div>'
    )
    handwriting_src = image_src(active_handwriting_path(memory))
    handwriting = f'<img src="{handwriting_src}">' if handwriting_src else escape(memory.get("note") or st.session_state.memory_text or "")
    category_pills = "".join(
        f'<span class="category-pill">{escape(category)}</span>'
        for category in ensure_selected_categories()
    )
    summary = escape(memory_summary_text(memory))
    html(f"""
<style>
.stApp {{ background:#efefef !important; }}
.block-container {{ max-width:780px !important; min-height:500px !important; margin:18px auto 0 !important; padding:28px 48px 30px !important; background:#fffaf7 !important; overflow:hidden !important; }}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
.post-handle {{ width:24px; height:3px; border-radius:999px; background:#cfc7c2; margin:0 auto 24px; }}
.album-title {{ font-size:20px; font-weight:900; margin-bottom:8px; }}
.album-sub {{ font-size:12px; color:#555; margin-bottom:18px; }}
.album-grid {{ display:grid; grid-template-columns:1.38fr 1.35fr .95fr; gap:12px; }}
.album-card {{ border-radius:11px; background:#fff; box-shadow:0 9px 20px rgba(0,0,0,.06); padding:14px; min-height:112px; overflow:hidden; }}
.album-photo img {{ width:100%; height:150px; object-fit:contain; display:block; }}
.album-card-title {{ font-size:11px; font-weight:900; margin-bottom:10px; }}
.album-note {{ height:150px; display:flex; align-items:center; justify-content:center; font-size:27px; font-weight:500; color:#333; }}
.album-note img {{ max-width:100%; max-height:100%; object-fit:contain; }}
.song-box {{ background:#ffe5d3; min-height:150px; }}
.song-heart {{ float:right; font-size:18px; }}
.mini-audio {{ width:100%; height:34px; margin-top:28px; }}
.fake-player {{ margin-top:28px; }}
.fake-line {{ height:2px; background:rgba(0,0,0,.18); position:relative; margin-bottom:10px; }}
.fake-line span {{ position:absolute; left:0; top:0; bottom:0; width:62%; background:#ef7d34; }}
.fake-controls {{ display:flex; justify-content:center; gap:14px; align-items:center; font-size:14px; font-weight:900; }}
.category-box {{ background:#f2fff0; min-height:132px; }}
.category-pill-row {{ display:flex; gap:12px; margin-top:12px; flex-wrap:wrap; }}
.category-pill {{ min-width:82px; height:24px; padding:0 14px; border-radius:999px; background:#fff; display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:900; box-shadow:0 6px 14px rgba(0,0,0,.06); }}
.album-video {{ height:82px; border-radius:7px; background:#dbe7f2; display:flex; align-items:center; justify-content:center; color:#57738b; overflow:hidden; position:relative; }}
.album-video-link {{ display:block; color:inherit; }}
.album-video img {{ width:100%; height:100%; object-fit:cover; filter:saturate(.82); }}
.album-video span {{ position:absolute; width:34px; height:34px; border-radius:50%; background:#fff; display:flex; align-items:center; justify-content:center; box-shadow:0 4px 12px rgba(0,0,0,.18); }}
.summary-box {{ background:#ffe4eb; font-size:12px; line-height:1.55; min-height:132px; }}
.st-key-album_edit_button,
.st-key-album_nfc_button {{ margin-top:16px !important; }}
.st-key-album_edit_button button,
.st-key-album_nfc_button button {{ height:58px !important; border-radius:999px !important; border:0 !important; font-size:17px !important; font-weight:900 !important; box-shadow:0 10px 22px rgba(0,0,0,.10) !important; }}
.st-key-album_edit_button button {{ background:#fff !important; color:#111 !important; }}
.st-key-album_nfc_button button {{ background:#050505 !important; color:#fff !important; }}
</style>
<div class="post-handle"></div>
<div class="album-title">추억 앨범이 만들어졌어요!</div>
<div class="album-sub">음악과 기록을 모아 하나의 앨범으로 정리했어요.</div>
<div class="album-grid">
  <div class="album-card album-photo">{photo}</div>
  <div class="album-card album-note"><div style="width:100%;"><div class="album-card-title">손글씨 기록</div>{handwriting}</div></div>
  <div class="album-card song-box"><div style="color:#ef6b2e; font-size:10px; font-weight:900;">현재 재생 중</div><span class="song-heart">♥</span><b>{track_title}</b><br><span style="color:#777; font-size:11px;">{track_artist}</span>{song_audio}</div>
  <div class="album-card category-box"><div class="album-card-title">카테고리</div><div class="category-pill-row">{category_pills}</div></div>
  <div class="album-card"><div class="album-card-title">추억 영상</div><a class="album-video-link" href="?action=video" target="_self"><div class="album-video">{photo}<span>▶</span></div></a></div>
  <div class="album-card summary-box"><div class="album-card-title">AI 요약</div>{summary}</div>
</div>
""")
    album_left, album_right = st.columns(2)
    with album_left:
        if st.button("수정하기", key="album_edit_button", use_container_width=True):
            go("category_edit")
    with album_right:
        if st.button("카세트 앨범 생성", key="album_nfc_button", use_container_width=True):
            go("nfc_scan")

elif page == "nfc_scan":
    html("""
<style>
.stApp { background:#efefef !important; }
.block-container { max-width:760px !important; min-height:430px !important; margin:24px auto 0 !important; padding:0 !important; background:#fffaf7 !important; overflow:hidden !important; }
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
.nfc-screen { height:430px; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; background:radial-gradient(circle at 70% 92%, rgba(255,104,32,.22), transparent 34%), #fffaf7; }
.post-handle { width:24px; height:3px; border-radius:999px; background:#cfc7c2; position:absolute; top:22px; left:50%; transform:translateX(-50%); }
.nfc-title { font-size:15px; font-weight:900; margin-bottom:28px; }
.nfc-arrow { color:#ff8b45; font-size:34px; line-height:1; margin-bottom:12px; }
.nfc-ring { width:150px; height:82px; border-radius:50%; border:1px solid rgba(255,139,69,.25); display:flex; align-items:center; justify-content:center; box-shadow:0 0 0 18px rgba(255,139,69,.04), 0 0 0 38px rgba(255,139,69,.03); background:radial-gradient(circle, rgba(255,139,69,.28), transparent 45%); margin-bottom:24px; }
.nfc-hit { display:block; color:#111; text-decoration:none; }
.nfc-label { font-size:20px; font-weight:900; margin-top:12px; }
.nfc-sub { font-size:10px; color:#777; margin-top:10px; }
</style>
<div class="nfc-screen">
  <div class="post-handle"></div>
  <div class="nfc-title">카세트를 올려주세요</div>
  <div class="nfc-arrow">▾</div>
  <a class="nfc-hit" href="?action=nfc_done" target="_self">
    <div class="nfc-ring"></div>
    <div class="nfc-label">NFC 인식 영역</div>
    <div class="nfc-sub">이 영역에 카세트를 올려주세요</div>
  </a>
</div>
""")

elif page == "nfc_done":
    html("""
<style>
.stApp { background:#efefef !important; }
.block-container { max-width:760px !important; min-height:430px !important; margin:24px auto 0 !important; padding:0 !important; background:#fffaf7 !important; overflow:hidden !important; }
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { display:none !important; }
.nfc-screen { height:430px; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; background:radial-gradient(circle at 72% 92%, rgba(255,104,32,.25), transparent 35%), #fffaf7; }
.post-handle { width:24px; height:3px; border-radius:999px; background:#cfc7c2; position:absolute; top:22px; left:50%; transform:translateX(-50%); }
.done-title { font-size:15px; font-weight:900; margin-bottom:8px; }
.done-sub { font-size:10px; color:#777; margin-bottom:18px; }
.cassette { width:118px; height:52px; background:#d8d8d8; display:flex; align-items:center; justify-content:center; font-size:12px; color:#555; margin-bottom:26px; }
.nfc-ring { width:150px; height:82px; border-radius:50%; border:1px solid rgba(255,139,69,.25); box-shadow:0 0 0 18px rgba(255,139,69,.04), 0 0 0 38px rgba(255,139,69,.03); background:radial-gradient(circle, rgba(255,139,69,.28), transparent 45%); margin-bottom:26px; }
</style>
<div class="nfc-screen">
  <div class="post-handle"></div>
  <div class="done-title">추억이 저장되었습니다</div>
  <div class="done-sub">당신의 소중한 추억이 카세트에 저장되었어요</div>
  <div class="cassette">카세트 이미지</div>
  <div class="nfc-ring"></div>
</div>
""")
    time.sleep(2.4)
    go("video")

elif page == "video":
    memory = active_memory()
    photo_path = active_photo_path(memory)
    photo_src = image_src(photo_path)
    photo = f'<img src="{photo_src}">' if photo_src else "▧"
    handwriting_src = image_src(active_handwriting_path(memory))
    handwriting = f'<img src="{handwriting_src}">' if handwriting_src else escape(memory.get("note") or st.session_state.memory_text or "")
    track = memory.get("selected_track") or st.session_state.selected_track or {}
    preview = track.get("preview_url") or ""
    audio = (
        f'<audio class="video-audio" controls src="{escape(preview, quote=True)}"></audio>'
        if preview
        else '<div class="fake-player"><div class="fake-line"><span></span></div><div class="fake-controls">× ‹ ● › ↻</div></div>'
    )
    summary = escape(memory_summary_text(memory))
    html(f"""
<style>
.stApp {{ background:#efefef !important; }}
.block-container {{ max-width:780px !important; min-height:500px !important; margin:18px auto 0 !important; padding:18px 32px 24px !important; background:#fffaf7 !important; overflow:hidden !important; }}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display:none !important; }}
.post-handle {{ width:24px; height:3px; border-radius:999px; background:#cfc7c2; margin:0 auto 22px; }}
.video-grid {{ display:grid; grid-template-columns:1.48fr .58fr; gap:18px; align-items:start; }}
.video-photo {{ border-radius:10px; background:#fff; box-shadow:0 10px 24px rgba(0,0,0,.08); padding:12px; min-height:392px; }}
.video-photo img {{ width:100%; height:315px; object-fit:contain; display:block; }}
.thumb-row {{ display:grid; grid-template-columns:repeat(5, 1fr); gap:8px; margin-top:10px; }}
.thumb {{ height:56px; border-radius:6px; background:#ddd; overflow:hidden; display:flex; align-items:center; justify-content:center; color:#777; }}
.thumb img {{ width:100%; height:100%; object-fit:cover; }}
.side-card {{ border-radius:10px; background:#fff; box-shadow:0 10px 24px rgba(0,0,0,.08); padding:12px; margin-bottom:10px; }}
.side-card.note {{ min-height:132px; font-size:22px; text-align:center; }}
.note-title {{ font-size:11px; font-weight:900; text-align:left; margin-bottom:8px; }}
.note-body {{ min-height:92px; display:flex; align-items:center; justify-content:center; overflow:hidden; }}
.side-card.note img {{ max-width:100%; max-height:96px; object-fit:contain; display:block; }}
.song-card {{ background:#ffe7dd; }}
.song-title {{ font-size:13px; font-weight:900; margin-bottom:6px; }}
.video-audio {{ width:100%; height:34px; margin-top:8px; }}
.fake-player {{ margin-top:12px; }}
.fake-line {{ height:2px; background:rgba(0,0,0,.18); position:relative; margin-bottom:10px; }}
.fake-line span {{ position:absolute; left:0; top:0; bottom:0; width:62%; background:#ef7d34; }}
.fake-controls {{ display:flex; justify-content:center; gap:12px; align-items:center; font-size:13px; font-weight:900; }}
.st-key-video_prev_button button,
.st-key-video_next_button button {{ height:46px !important; border-radius:999px !important; background:#fff !important; color:#111 !important; border:0 !important; box-shadow:0 8px 18px rgba(0,0,0,.10) !important; font-size:15px !important; font-weight:900 !important; }}
</style>
<div class="post-handle"></div>
<div class="video-grid">
  <div class="video-photo">
    {photo}
    <div class="thumb-row">
      <div class="thumb">{photo}</div><div class="thumb">▧</div><div class="thumb">▧</div><div class="thumb">▧</div><div class="thumb">▧</div>
    </div>
  </div>
  <div>
    <div class="side-card song-card">
      <div class="song-title">{escape(track.get("title") or "노래 제목")}</div>
      <div style="font-size:11px; color:#777; margin-bottom:8px;">{escape(track.get("artist") or "가수")}</div>
      {audio}
    </div>
    <div class="side-card note"><div class="note-title">손글씨 기록</div><div class="note-body">{handwriting}</div></div>
    <div class="side-card" style="font-size:11px; line-height:1.5; background:#ffe4eb;"><b>AI 요약</b><br>{summary}</div>
  </div>
</div>
""")
    video_prev_col, video_gap_col, video_next_col = st.columns([0.28, 0.44, 0.28])
    with video_prev_col:
        if st.button("‹ 이전", key="video_prev_button", use_container_width=True):
            go("album_done")
    with video_next_col:
        if st.button("다음 ›", key="video_next_button", use_container_width=True):
            go("home")

elif page == "player":
    go("video")

elif page == "player_legacy":
    memory = st.session_state.play_memory or (memories[0] if memories else {})
    src = image_src(memory.get("photo") or st.session_state.photo_path)
    image = f'<img src="{src}">' if src else "▧"
    track = memory.get("selected_track") or st.session_state.selected_track or {}
    title = escape(track.get("title") or "19XX년대의 추억")
    artist = escape(track.get("artist") or "Re:Play")
    note = escape(memory.get("note") or st.session_state.memory_text or "아직 기록된 문장이 없어요.")
    handwriting = (
        memory.get("handwriting")
        or st.session_state.handwriting_path
        or handwriting_path_for(memory.get("id") or st.session_state.memory_id)
    )
    handwriting_src = image_src(handwriting)
    handwriting_preview = (
        f'<div class="review-label" style="margin-top:14px;">저장된 손글씨</div><div class="review-handwriting"><img src="{handwriting_src}"></div>'
        if handwriting_src
        else ""
    )
    preview = track.get("preview_url") or ""
    audio = f'<audio class="audio-player" controls src="{preview}"></audio>' if preview else ""
    delete_id = memory.get("id") or st.session_state.memory_id
    delete_link = f'<a class="delete-pill" href="?action=delete&memory={delete_id}" target="_self">삭제</a>' if delete_id else ""
    html(f"""
<div class="app-card player">
  <div class="topbar" style="position:absolute; top:28px; width:calc(100% - 88px);"><a class="back" href="?action=back" target="_self">‹</a><div class="center"></div><div></div></div>
  <div class="record-scene"><div class="play-photo">{image}</div><div class="small-record"></div></div>
  <div class="song-name">&lt;{title}&gt;</div>
  <div class="memory-review">
    <div class="review-label">기록한 기억</div>
    <div class="review-note">{note}</div>
    {handwriting_preview}
    <div class="music-chip">♪ {title} - {artist}</div>
    {audio}
  </div>
  <div class="player-actions">
    <a class="pill" href="?action=home" target="_self">처음으로</a>
    {delete_link}
  </div>
</div>
""")
