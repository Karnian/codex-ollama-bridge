# codex2ollama bridge

<a id="top"></a>

## Language

- [English](#english)
- [한국어](#korean)

---

<a id="english"></a>
## English

### Quick Navigation

- [Overview](#en-overview)
- [Endpoints](#en-endpoints)
- [Requirements](#en-requirements)
- [Run](#en-run)
- [Example Calls](#en-example-calls)
- [Notes](#en-notes)
- [Windows Packaging (PyInstaller)](#en-windows-packaging)

<a id="en-overview"></a>
### Overview

This project exposes Ollama-like HTTP endpoints and routes requests to local `codex`.

It now supports model routing by request model name:

- `codex` (or values starting with `codex`) -> calls Codex CLI
- `gemini` (or values starting with `gemini`) -> calls Gemini provider selected at startup
  - `google` mode (default): Gemini CLI (Google auth)
  - `api` mode: direct Gemini API call (API key)

<a id="en-endpoints"></a>
### Endpoints

- `POST /api/chat`
- `POST /api/generate`
- `GET /api/tags`
- `GET /healthz`

Default port is `11435` (customizable with `BRIDGE_PORT`).

By default, the bridge uses your Codex CLI default model/profile.
If needed, set `CODEX_MODEL` to force a specific model.

Optional detail controls:

- `CODEX_MODEL_VERBOSITY=low|medium|high` (default: `high`)
- `DETAIL_MODE=off|high` (default: `high`)
- `DETAIL_SYSTEM_INSTRUCTION="..."` to customize internal guidance
  (default favors natural conversational style, without forced numbering)
- `GEMINI_BIN=gemini` to set Gemini CLI binary path
- `GEMINI_MODEL=...` to set default Gemini model when request model is just `gemini`
- `GEMINI_AUTH_MODE=google|api` to override Gemini auth mode (`google` default)
- `GEMINI_API_BASE_URL=...` to override Gemini API base URL (default: `https://generativelanguage.googleapis.com/v1beta`)
- `STARTUP_CHECK_TIMEOUT_SECONDS=15` startup readiness check timeout
- `STARTUP_CHECK_STRICT=1` abort server start when any startup check fails

<a id="en-requirements"></a>
### Requirements

- Python 3.10+
- `codex` CLI installed and logged in
- `gemini` CLI installed and logged in (only needed when using `gemini` model)
- If Gemini CLI is configured to use Gemini API auth mode, set `GEMINI_API_KEY`

<a id="en-run"></a>
### Run

```bash
python3 bridge_server.py
```

Or with custom port:

```bash
BRIDGE_PORT=18080 python3 bridge_server.py
```

<a id="en-example-calls"></a>
### Example Calls

#### Chat

```bash
curl -s http://localhost:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "codex",
    "messages": [
      {"role": "user", "content": "한 문장으로 인사해줘"}
    ],
    "stream": false
  }'
```

#### Chat (Gemini)

```bash
curl -s http://localhost:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemini",
    "messages": [
      {"role": "user", "content": "Say hello in one sentence."}
    ],
    "stream": false
  }'
```

#### Generate

```bash
curl -s http://localhost:11435/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "codex",
    "prompt": "hello in one short sentence",
    "stream": false
  }'
```

#### Tags

```bash
curl -s http://localhost:11435/api/tags
```

<a id="en-notes"></a>
### Notes

- `stream: true` is supported as Ollama-style NDJSON framing.
- Current streaming is simulated from final response text produced by `codex exec`.
- Codex prompts are passed through stdin (`codex exec -`) to avoid OS argv length limits.
- On startup, the server probes both `codex` and `gemini` and prints `[READY]`/`[FAIL ]` with reason.
- Full logs are written to `logs/bridge_server-YYYYMMDD-HHMMSS.log` on each start.
- Log timestamps use Korea Standard Time (`Asia/Seoul`, `+09:00`).
- On first launch, Gemini auth mode can be selected (`google` default, or `api`).
- The selected mode is saved to `.bridge_settings.json`.
- If `api` mode is selected and API key env is missing, the server prompts for `GEMINI_API_KEY` in terminal.
- Entered API key is saved to `.bridge_secrets.json` and reused on next starts.
- In `api` mode, Gemini requests are sent directly to Gemini API (no Gemini CLI process for requests).
- In `api` mode, TLS certificate verification is disabled for Gemini API requests.
- Unsupported Ollama options are ignored by design.

<a id="en-windows-packaging"></a>
### Windows Packaging (PyInstaller)

Build on Windows (cmd.exe at repository root):

```bat
windows\build_windows_exe.bat
```

Output:

- `dist\codex2ollama-bridge.exe`

Run options:

1. Double-click `dist\codex2ollama-bridge.exe` (console window stays open while running).
2. Or use launcher script:

```bat
windows\start_bridge.bat
```

Optional environment variables in cmd before launch:

```bat
set BRIDGE_PORT=11435
set CODEX_MODEL=gpt-5
set CODEX_MODEL_VERBOSITY=high
```

Prerequisites on Windows:

- `codex` CLI must be installed and logged in.
- Allow firewall prompt if Windows asks on first run.

FAQ:

- Q: Do Windows end users need to install PyInstaller?
  - A: No. Only the person building the `.exe` needs PyInstaller.
- Q: Can I build `codex2ollama-bridge.exe` on macOS?
  - A: Usually no. Build Windows `.exe` on Windows (local PC, VM, or CI runner).
- Q: If I update `bridge_server.py`, do I need to edit `.bat` files?
  - A: Usually no. Rebuild the `.exe`. Update `.bat` only if file names, exe name, or paths change.

[Back to top](#top)

---

<a id="korean"></a>
## 한국어

### 빠른 이동

- [개요](#ko-overview)
- [엔드포인트](#ko-endpoints)
- [요구 사항](#ko-requirements)
- [실행](#ko-run)
- [호출 예시](#ko-example-calls)
- [참고 사항](#ko-notes)
- [Windows 패키징 (PyInstaller)](#ko-windows-packaging)

<a id="ko-overview"></a>
### 개요

이 프로젝트는 Ollama 스타일의 HTTP 엔드포인트를 제공하고 요청을 로컬 `codex`로 전달합니다.

요청 `model` 값에 따라 CLI를 분기 호출합니다:

- `codex` (또는 `codex`로 시작하는 값) -> Codex CLI 호출
- `gemini` (또는 `gemini`로 시작하는 값) -> 시작 시 선택한 Gemini 공급자 호출
  - `google` 모드(기본): Gemini CLI (Google 인증)
  - `api` 모드: Gemini API 직접 호출 (API 키)

<a id="ko-endpoints"></a>
### 엔드포인트

- `POST /api/chat`
- `POST /api/generate`
- `GET /api/tags`
- `GET /healthz`

기본 포트는 `11435`이며 `BRIDGE_PORT`로 변경할 수 있습니다.

기본적으로 브리지는 Codex CLI의 기본 모델/프로필을 사용합니다.
필요하면 `CODEX_MODEL`을 설정해 특정 모델을 강제로 사용하게 할 수 있습니다.

선택 가능한 상세 제어 옵션:

- `CODEX_MODEL_VERBOSITY=low|medium|high` (기본값: `high`)
- `DETAIL_MODE=off|high` (기본값: `high`)
- `DETAIL_SYSTEM_INSTRUCTION="..."` 내부 지침 문구를 사용자화
  (기본값은 번호 강제를 피한 자연스러운 대화 스타일)
- `GEMINI_BIN=gemini` Gemini CLI 실행 파일 경로 설정
- `GEMINI_MODEL=...` 요청 모델이 `gemini`일 때 기본 Gemini 모델 설정
- `GEMINI_AUTH_MODE=google|api` Gemini 인증 모드 강제 지정 (`google` 기본)
- `GEMINI_API_BASE_URL=...` Gemini API 기본 URL 지정 (기본값: `https://generativelanguage.googleapis.com/v1beta`)
- `STARTUP_CHECK_TIMEOUT_SECONDS=15` 시작 시 준비상태 점검 타임아웃
- `STARTUP_CHECK_STRICT=1` 시작 점검 하나라도 실패하면 서버 시작 중단

<a id="ko-requirements"></a>
### 요구 사항

- Python 3.10+
- `codex` CLI 설치 및 로그인 완료
- `gemini` 모델 사용 시 `gemini` CLI 설치 및 로그인 완료
- Gemini CLI 인증 모드가 Gemini API 방식이면 `GEMINI_API_KEY` 환경변수 설정 필요

<a id="ko-run"></a>
### 실행

```bash
python3 bridge_server.py
```

또는 사용자 지정 포트로 실행:

```bash
BRIDGE_PORT=18080 python3 bridge_server.py
```

<a id="ko-example-calls"></a>
### 호출 예시

#### Chat

```bash
curl -s http://localhost:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "codex",
    "messages": [
      {"role": "user", "content": "한 문장으로 인사해줘"}
    ],
    "stream": false
  }'
```

#### Chat (Gemini)

```bash
curl -s http://localhost:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemini",
    "messages": [
      {"role": "user", "content": "한 문장으로 인사해줘"}
    ],
    "stream": false
  }'
```

#### Generate

```bash
curl -s http://localhost:11435/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "codex",
    "prompt": "hello in one short sentence",
    "stream": false
  }'
```

#### Tags

```bash
curl -s http://localhost:11435/api/tags
```

<a id="ko-notes"></a>
### 참고 사항

- `stream: true`는 Ollama 스타일 NDJSON 프레이밍으로 지원됩니다.
- 현재 스트리밍은 `codex exec` 최종 응답 텍스트를 기반으로 시뮬레이션됩니다.
- Codex 프롬프트는 OS 인자 길이 제한을 피하기 위해 stdin(`codex exec -`)으로 전달됩니다.
- 서버 시작 시 `codex`/`gemini` 호출 준비상태를 점검하고 `[READY]`/`[FAIL ]` 이유를 출력합니다.
- 전체 로그는 매번 시작 시간 기준 새 파일 `logs/bridge_server-YYYYMMDD-HHMMSS.log`에 저장됩니다.
- 로그 시간대는 한국시간(`Asia/Seoul`, `+09:00`) 기준입니다.
- 최초 실행 시 Gemini 인증 모드를 선택할 수 있습니다 (`google` 기본, `api` 선택 가능).
- 선택된 모드는 `.bridge_settings.json`에 저장됩니다.
- `api` 모드 선택 시 API 키 환경변수가 없으면 서버 시작 중 터미널에서 `GEMINI_API_KEY` 입력을 요청합니다.
- 입력된 API 키는 `.bridge_secrets.json`에 저장되어 다음 실행부터 재사용됩니다.
- `api` 모드에서는 Gemini 요청을 Gemini API로 직접 보냅니다(요청 시 Gemini CLI 프로세스 미사용).
- `api` 모드에서는 Gemini API 요청 시 TLS 인증서 검증을 비활성화합니다.
- 지원하지 않는 Ollama 옵션은 설계상 무시됩니다.

<a id="ko-windows-packaging"></a>
### Windows 패키징 (PyInstaller)

Windows에서 빌드 (저장소 루트의 cmd.exe):

```bat
windows\build_windows_exe.bat
```

출력 파일:

- `dist\codex2ollama-bridge.exe`

실행 방법:

1. `dist\codex2ollama-bridge.exe` 더블클릭 실행 (실행 중 콘솔 창 유지)
2. 또는 런처 스크립트 사용:

```bat
windows\start_bridge.bat
```

실행 전 선택 환경변수(cmd):

```bat
set BRIDGE_PORT=11435
set CODEX_MODEL=gpt-5
set CODEX_MODEL_VERBOSITY=high
```

Windows 사전 조건:

- `codex` CLI 설치 및 로그인 완료
- 첫 실행 시 Windows 방화벽 허용 팝업이 뜨면 허용

FAQ:

- Q: Windows 최종 사용자가 PyInstaller를 설치해야 하나요?
  - A: 아니요. `.exe`를 빌드하는 사람만 PyInstaller가 필요합니다.
- Q: macOS에서 Windows용 `codex2ollama-bridge.exe`를 빌드할 수 있나요?
  - A: 보통 어렵습니다. Windows(실PC, VM, CI 러너)에서 빌드하세요.
- Q: `bridge_server.py`를 수정하면 `.bat` 파일도 수정해야 하나요?
  - A: 대부분 아닙니다. `.exe`만 다시 빌드하면 됩니다. 파일명/경로/실행파일명이 바뀔 때만 `.bat` 수정이 필요합니다.

[맨 위로](#top)
