from pathlib import Path
import re

APP = Path("app.py")

if not APP.exists():
    raise SystemExit("app.py를 찾을 수 없어요. 이 파일을 app.py가 있는 프로젝트 폴더에 넣고 실행해줘.")

text = APP.read_text(encoding="utf-8")

backup = APP.with_suffix(".py.bak_pen_toolbar_v3")
backup.write_text(text, encoding="utf-8")

# 혹시 quote import가 없으면 추가
if "from urllib.parse import quote" not in text:
    text = text.replace(
        "from html import escape\n",
        "from html import escape\nfrom urllib.parse import quote\n",
        1,
    )

# 1) 기존 색상바 CSS를 슬림 툴바 CSS로 교체
new_css_block = r'''
/* 손글씨 패드 오른쪽 펜 툴바 - 슬림 버전 */
.slim-pen-toolbar {
    width: 32px;
    height: 282px;
    border-radius: 999px;
    background: #ffffff;
    border: 1px solid #e7e7e7;
    box-shadow: 0 8px 18px rgba(0,0,0,.14);
    display: flex;
    flex-direction: column;
    align-items: center;
    overflow: hidden;
    margin-top: 74px;
}

.slim-pen-icon {
    width: 100%;
    height: 42px;
    border-bottom: 1px solid #eeeeee;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    line-height: 1;
    color: #111111;
}

.slim-pen-gap {
    width: 100%;
    height: 50px;
    border-bottom: 1px solid #eeeeee;
}

.slim-pen-colors {
    flex: 1;
    width: 100%;
    padding: 12px 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    gap: 9px;
}

.slim-pen-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    display: block;
    text-decoration: none;
    box-shadow: none;
    border: 0;
}

.slim-pen-dot.active {
    outline: 2px solid #111111;
    outline-offset: 2px;
}

/* 툴바가 캔버스 오른쪽에 살짝 겹쳐 붙도록 위치 조정 */
div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) {
    margin-left: -40px !important;
    position: relative !important;
    z-index: 9 !important;
    display: flex !important;
    justify-content: center !important;
}

div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) div[data-testid="stVerticalBlock"] {
    width: 34px !important;
    min-height: 0 !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    padding: 0 !important;
    margin: 0 !important;
    gap: 0 !important;
    align-items: center !important;
}

@media (max-width: 820px) {
  .slim-pen-toolbar {
      height: 270px;
      margin-top: 70px;
  }

  div[data-testid="stHorizontalBlock"]:has(iframe[title*="streamlit_drawable_canvas"]) > div[data-testid="column"]:last-child:not(:has(iframe[title*="streamlit_drawable_canvas"])) {
      margin-left: -38px !important;
  }
}

.record-next {'''

# 기존 color-toolbar-title CSS부터 .record-next { 직전까지 교체
css_pattern_1 = re.compile(
    r"\.color-toolbar-title \{.*?\.record-next \{",
    re.S,
)

text2, css_count = css_pattern_1.subn(new_css_block, text, count=1)

# 이미 슬림 CSS가 들어간 상태라면 기존 슬림 CSS를 다시 교체
if css_count == 0:
    css_pattern_2 = re.compile(
        r"/\* 손글씨 패드 오른쪽 펜 툴바 - 슬림 버전 \*/.*?\.record-next \{",
        re.S,
    )
    text2, css_count = css_pattern_2.subn(new_css_block, text2, count=1)

# 그래도 못 찾으면 write 화면 style 안쪽 끝에 추가
if css_count == 0:
    marker = ".record-next {"
    if marker in text2:
        text2 = text2.replace(marker, new_css_block, 1)
    else:
        raise SystemExit("CSS를 넣을 위치를 찾지 못했어요. app.py 구조가 예상과 달라요.")

# 2) 기존 with tool_col 색상 버튼 블록을 HTML 작은 점 링크 방식으로 교체
new_tool_block = r'''            with tool_col:
                current_pen = st.session_state.get("pen_color", "#000000")

                colors = [
                    ("#000000", "black"),
                    ("#1e88e5", "blue"),
                    ("#44c767", "green"),
                    ("#f8c51b", "yellow"),
                    ("#f34b3f", "red"),
                ]

                dots = ""

                for hex_color, name in colors:
                    active = " active" if current_pen == hex_color else ""
                    dots += (
                        f'<a class="slim-pen-dot{active}" '
                        f'style="background:{hex_color};" '
                        f'href="?action=write{memory_query}&mode=handwriting&pen={quote(hex_color, safe="")}" '
                        f'target="_self" '
                        f'title="{name}"></a>'
                    )

                st.markdown(f"""
<div class="slim-pen-toolbar">
    <div class="slim-pen-icon">✎</div>
    <div class="slim-pen-gap"></div>
    <div class="slim-pen-colors">{dots}</div>
</div>
""", unsafe_allow_html=True)'''

# write 화면 안의 tool_col부터 voice 모드 직전까지 통째로 교체
tool_pattern = re.compile(
    r" {12}with tool_col:\n.*?(?=\n {8}elif write_mode == \"voice\":)",
    re.S,
)

text3, tool_count = tool_pattern.subn(new_tool_block, text2, count=1)

if tool_count == 0:
    raise SystemExit("색상 버튼 코드 블록을 찾지 못했어요. app.py 구조가 예상과 달라서 자동 수정 실패.")

APP.write_text(text3, encoding="utf-8")

print("완료: 손글씨 색상바를 두 번째 이미지처럼 슬림 툴바로 수정했습니다.")
print(f"백업 파일: {backup.name}")