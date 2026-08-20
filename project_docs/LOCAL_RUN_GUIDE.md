# macro-dashboard 로컬 실행 가이드 (매번 사용 시)

> 이 문서는 컴퓨터를 새로 켠 상태에서, 로컬 PC의 macro-dashboard를 다시 실행할 때
> 참고하는 "매번 반복하는 절차" 문서입니다.
> (최초 1회만 하는 설치 과정은 `LOCAL_SETUP_LOG.md` 참고)

---

## 0. 전체 구조 한눈에 보기

- **실행되는 코드의 원본**은 전부 **GitHub 저장소**(`hanslee9/macro-dashboard`)에 있습니다.
- **VS Code + 로컬 PC**는 그 코드를 미리 복제(clone)해서 내 컴퓨터 안에 똑같이
  가지고 있는 "실행 장소"입니다. 코드를 수정하면 로컬 폴더가 먼저 바뀌고,
  `git push`를 해야 GitHub에도 반영됩니다.
- **가상환경(venv)**은 이 프로젝트에서만 쓰는 파이썬 패키지들을 따로 격리해둔
  독립 공간입니다. 컴퓨터에 원래 깔려있던 Anaconda 등 다른 파이썬 환경과
  섞이지 않도록, macro-dashboard 폴더 안(`venv` 폴더)에 따로 만들어 두었습니다.
  **실행할 때마다 이 가상환경을 "켜줘야"** 올바른 패키지들을 사용합니다.

---

## 1. 컴퓨터 부팅 후 VS Code 실행

1. 윈도우 시작 메뉴 또는 바탕화면에서 **VS Code** 아이콘 클릭해 실행
2. VS Code가 열리면, 왼쪽 상단 **탐색기(Explorer) 아이콘**(맨 위 📄 모양)을 클릭
3. `macro-dashboard` 폴더가 이미 열려 있다면 이 항목은 건너뛰어도 됩니다.
   폴더가 안 열려 있다면(예: "No Folder Opened" 표시):
   - 상단 메뉴 `File` → `Open Folder...` 클릭
   - `C:\Users\hslee\Documents\macro-dashboard` 선택 → `Select Folder`

---

## 2. 터미널 열기

- 단축키: `` Ctrl + ` `` (Ctrl과 물결표 키 동시에)
- 또는 상단 메뉴 `Terminal` → `New Terminal`

터미널이 화면 하단에 나타납니다.

---

## 3. 프로젝트 폴더로 이동 (터미널 경로가 다를 경우)

터미널을 새로 열면 보통 자동으로 프로젝트 폴더 경로에서 시작하지만,
혹시 다른 경로라면 아래 명령어로 이동합니다.

```
cd C:\Users\hslee\Documents\macro-dashboard
```

---

## 4. 가상환경(venv) 활성화 — 매번 필수

**컴퓨터를 새로 켤 때마다, 터미널을 새로 열 때마다 이 단계가 반드시 필요합니다.**
가상환경은 컴퓨터를 껐다 켜도 자동으로 켜지지 않으며, 매번 수동으로 켜줘야 합니다.

```
venv\Scripts\activate
```

**성공 확인**: 프롬프트 맨 앞에 `(venv)`가 붙어야 합니다.

```
(venv) C:\Users\hslee\Documents\macro-dashboard>
```

이 표시가 없다면 이후 명령어(`streamlit run` 등)가 제대로 동작하지 않거나,
엉뚱한(가상환경 밖의) 파이썬 패키지를 사용하게 될 수 있으니 꼭 확인하세요.

---

## 5. Streamlit 앱 실행

```
streamlit run app.py
```

**정상 결과 예시:**
```
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
```

이 메시지가 뜨면서 브라우저가 자동으로 열리고 대시보드 화면이 나타납니다.
자동으로 안 열리면 브라우저 주소창에 직접 `http://localhost:8501` 입력.

---

## 6. 사용 종료 시

- 대시보드 사용이 끝나면, 터미널을 클릭한 상태에서 `Ctrl + C`를 눌러 서버를 중지합니다.
- 그냥 VS Code나 터미널 창을 닫아도 서버는 함께 종료됩니다.
- 브라우저 탭만 닫고 터미널(서버)은 계속 켜두면, 나중에 다시
  `http://localhost:8501`로 접속 가능합니다(같은 세션 동안).

---

## 요약: 매번 반복하는 3줄

컴퓨터를 켜고 VS Code로 macro-dashboard 폴더를 연 다음, 터미널에 아래 3줄만
순서대로 입력하면 됩니다.

```
cd C:\Users\hslee\Documents\macro-dashboard
venv\Scripts\activate
streamlit run app.py
```

(3번째 줄까지 입력하면 자동으로 브라우저가 열립니다. 종료할 때는 `Ctrl+C`)

---

## 참고: secrets.toml은 다시 설정할 필요 없음

`.streamlit\secrets.toml`(FRED/ECOS/KOSIS API 키)은 로컬 폴더에 이미 저장되어
있고, 컴퓨터를 껐다 켜도 파일은 그대로 남아있습니다. **매번 다시 만들 필요
없습니다.** (단, 이 파일은 GitHub에는 올라가지 않는 로컬 전용 파일입니다 —
`.gitignore`로 보호되어 있습니다.)

---

## 참고: GitHub 코드가 업데이트된 경우

만약 다른 PC나 웹에서 GitHub 저장소 코드를 직접 수정한 적이 있다면, 로컬
폴더를 최신 상태로 맞추기 위해 아래 명령어를 실행 전에 한 번 넣어주면
안전합니다 (venv 활성화 여부와 무관, 아무 때나 실행 가능).

```
git pull
```

---

## 자주 겪을 수 있는 문제

| 증상 | 원인/해결 |
|---|---|
| `(venv)`가 안 붙음 | 4단계(`venv\Scripts\activate`)를 건너뛴 것. 다시 실행 |
| `streamlit run app.py` 실행 시 KeyError (secrets 관련) | `.streamlit\secrets.toml` 파일이 삭제/이동되지 않았는지 확인 |
| 브라우저에 "사이트에 연결할 수 없음" | 서버가 아직 안 켜졌거나 꺼진 상태. 터미널에 "You can now view..." 메시지가 떴는지 재확인 |
| 포트(8501)가 이미 사용 중이라는 메시지 | 이미 다른 터미널에서 Streamlit이 실행 중일 수 있음. 기존 터미널 확인 또는 다른 포트로 자동 실행되는 안내를 따르면 됨 |
