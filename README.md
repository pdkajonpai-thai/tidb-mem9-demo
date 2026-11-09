# TiDB + mem9 AI 투자 추천 데모

TiDB Cloud(벡터·관계형 데이터, LangGraph 체크포인트), mem9(장기 기억), Claude(LangGraph 에이전트)로 만든 투자 추천 E2E 데모입니다. 화면은 Streamlit이고, Streamlit Community Cloud에 배포합니다. 데이터는 모두 합성 데이터입니다.

설계와 결정 사항은 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)에 있습니다. 이 문서는 **처음부터 내 계정으로 배포하는 순서**만 다룹니다.

## 전체 순서

| 단계 | 할 일 | 소요 |
|---|---|---|
| 1 | 저장소 fork 또는 clone | 1분 |
| 2 | TiDB Cloud Starter 클러스터 만들기 | 5분 |
| 3 | mem9 API 키, Claude API 키 발급 | 5분 |
| 4 | 로컬에서 연결 확인과 시드 데이터 적재 | 10분 |
| 5 | Google 로그인(OAuth 클라이언트) 만들기 | 10분 |
| 6 | Streamlit Community Cloud에 배포 | 10분 |
| 7 | 배포 확인 | 5분 |

준비물: GitHub 계정, Google 계정, Python 3.12, 결제 수단(Claude API 크레딧용, $5면 충분).

---

## 1. 저장소 가져오기

Streamlit Community Cloud는 **내 GitHub 계정에서 접근할 수 있는 저장소**만 배포할 수 있습니다. GitHub에서 이 저장소를 **Fork**한 뒤 내려받습니다.

```bash
git clone https://github.com/<내 GitHub 계정>/tidb-mem9-demo.git
cd tidb-mem9-demo
```

## 2. TiDB Cloud Starter 만들기

1. https://tidbcloud.com 에 가입합니다.
2. **Create Cluster** 에서 **Starter**를 고릅니다.
   - Cloud Provider: **AWS** (Auto Embedding, 즉 SQL 안의 `EMBED_TEXT`가 AWS에서만 지원됩니다)
   - Region: **Tokyo (ap-northeast-1)** 권장
3. 클러스터 화면에서 **Connect** 를 누릅니다.
   - Connect With: `General`
   - **Generate Password** 로 비밀번호를 만들고 따로 저장합니다. 다시 볼 수 없습니다.
   - Host, Port(4000), User(`xxxxxxxx.root` 형태)를 적어 둡니다.
4. 왼쪽 **SQL Editor** 에서 데이터베이스를 만듭니다.

   ```sql
   CREATE DATABASE invest_demo;
   ```

   테이블은 4단계의 `seed.py`가 만듭니다.

## 3. API 키 발급

**mem9**: 가입 없이 아래 명령의 응답에 있는 `id` 값이 API 키입니다.

```bash
curl -sX POST https://api.mem9.ai/v1alpha1/mem9s
# 키 확인: "active"가 나오면 정상
curl -s https://api.mem9.ai/v1alpha2/status -H "X-API-Key: <발급받은 키>"
```

**Claude**: https://platform.claude.com 에 가입하고 **Billing** 에서 크레딧을 충전한 뒤 **API Keys** 에서 키를 만듭니다.

## 4. 로컬 준비 (연결 확인과 시드 적재)

Cloud는 시드 스크립트를 실행하지 않으므로, **처음 한 번은 로컬에서 테이블과 시드 데이터를 넣어야 합니다.**

Python 3.12가 필요합니다(최소 3.10). macOS 기본 `python3`는 3.9라서 쓸 수 없습니다.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 아래 값을 채웁니다
```

`.env`에 채울 값:

| 키 | 값 |
|---|---|
| `TIDB_HOST`, `TIDB_PORT`, `TIDB_USER`, `TIDB_PASSWORD` | 2단계에서 적어 둔 값 |
| `TIDB_DATABASE` | `invest_demo` |
| `MEM9_API_KEY` | 3단계의 mem9 키 |
| `ANTHROPIC_API_KEY` | 3단계의 Claude 키 |
| `LLM_MODEL` | 기본 `claude-haiku-5-5` (그대로 두면 됩니다) |
| `ADMIN_EMAILS` | 관리자 화면을 볼 Google 이메일 (쉼표로 구분) |

```bash
python scripts/smoke_test.py   # TiDB, Auto Embedding, mem9, Claude 연결 확인. 모두 PASS여야 합니다
python scripts/seed.py         # 테이블 생성과 시드 데이터 적재
```

로컬에서 화면을 먼저 보려면 `.env`에 `DEMO_AUTH=off`를 잠시 넣고 `streamlit run app.py`를 실행합니다. 이 값은 Google 로그인을 건너뛰는 **로컬 전용** 설정이라 배포 Secrets에는 절대 넣지 않습니다.

## 5. Google 로그인 만들기

배포한 앱은 Google 로그인(`st.login`)을 거쳐야 열립니다. 로그인 설정이 없으면 앱이 오류를 보여 주고 열리지 않습니다(보호 없이 공개되는 것을 막기 위해서입니다).

1. https://console.cloud.google.com 에서 프로젝트를 만들거나 고릅니다.
2. **API 및 서비스 > OAuth 동의 화면**
   - 사용자 유형: **외부(External)**
   - 범위: 기본(openid, email, profile)만 둡니다.
   - 게시 상태를 **프로덕션**으로 바꿉니다. '테스트'로 두면 등록한 테스트 사용자만 로그인할 수 있습니다. 기본 범위만 쓰면 Google 앱 검증은 필요 없습니다.
3. **사용자 인증 정보 > OAuth 클라이언트 ID 만들기 > 웹 애플리케이션**
   - 승인된 리디렉션 URI: `http://localhost:8501/oauth2callback`
   - 배포 주소는 6단계에서 정해지면 추가합니다.
4. 발급된 **클라이언트 ID**와 **클라이언트 보안 비밀번호**를 적어 둡니다.
5. 쿠키 암호화용 무작위 문자열을 만듭니다.

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

## 6. Streamlit Community Cloud에 배포

1. https://share.streamlit.io 에 GitHub 계정으로 로그인하고 **Create app** 을 누릅니다.
2. 저장소는 1단계에서 fork한 저장소, 브랜치는 `main`, Main file path는 `app.py`로 고릅니다.
3. **App URL** 에 원하는 주소를 정합니다. 예: `my-mem9-demo` 이면 `https://my-mem9-demo.streamlit.app`
4. **Advanced settings**
   - Python version: **3.12**
   - Secrets: [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example)을 복사해 값을 채워 붙여넣습니다.

   ```toml
   TIDB_HOST = "gateway01.ap-northeast-1.prod.aws.tidbcloud.com"
   TIDB_PORT = "4000"
   TIDB_USER = "xxxxxxxx.root"
   TIDB_PASSWORD = "..."
   TIDB_DATABASE = "invest_demo"

   MEM9_API_URL = "https://api.mem9.ai"
   MEM9_API_KEY = "..."

   ANTHROPIC_API_KEY = "sk-ant-..."
   LLM_MODEL = "claude-haiku-5-5"

   ADMIN_EMAILS = "you@example.com"
   LANGSMITH_TRACING = "false"

   [auth]
   redirect_uri = "https://my-mem9-demo.streamlit.app/oauth2callback"
   cookie_secret = "5단계에서 만든 무작위 문자열"
   client_id = "....apps.googleusercontent.com"
   client_secret = "..."
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```

   - `.env`와 같은 값을 **TOML 형식**으로 넣습니다. 값은 모두 따옴표로 감쌉니다.
   - 로그인 설정은 반드시 맨 아래 **`[auth]` 섹션** 안에 둡니다. 그 위의 최상위 키만 환경변수로 옮겨 읽기 때문에, 순서가 바뀌면 키를 못 찾습니다.
   - `DEMO_AUTH`는 넣지 않습니다.
5. **Deploy** 를 누릅니다. 패키지 설치까지 몇 분 걸립니다.
6. Google Cloud Console의 OAuth 클라이언트(5단계 3번)에 리디렉션 URI `https://<App URL>.streamlit.app/oauth2callback`을 추가합니다. Secrets의 `redirect_uri`와 한 글자도 다르지 않아야 합니다.

TiDB 연결에 필요한 TLS 인증서는 `certifi` 패키지로 처리하므로 따로 올릴 파일은 없습니다.

## 7. 배포 확인

1. 배포 주소로 접속해 Google로 로그인합니다.
2. **Signal 분석** 화면에서 AI 분석을 실행합니다. 원문 시그널을 분석해 투자 기회를 만드는 단계로, 처음 한 번은 꼭 해야 합니다(약 15초).
3. 사이드바에서 데모 사용자를 고르고 **개인화 추천** 화면에서 추천이 생성되는지 봅니다. 추천 한 번에 15~20초쯤 걸립니다.
4. `ADMIN_EMAILS`에 넣은 계정이라면 **접속 기록** 화면이 보이고, 방금 로그인한 기록이 남아 있어야 합니다.
5. **Behind the scenes > LangGraph** 탭에서 노드별 실행 시간을 확인할 수 있습니다.

## 코드를 바꾼 뒤 다시 배포하기

- `main`에 push하면 Cloud가 새 코드를 받아 옵니다.
- 다만 서버는 재시작되지 않아, 이미 불러온 `core/`, `ui/` 모듈은 예전 코드로 남습니다. 그래서 화면에서 `ImportError` 같은 오류가 날 수 있습니다.
- **`core/`나 `ui/`를 바꿨다면 push 뒤 Manage app > ⋮ > Reboot app** 으로 재시작합니다. 재시작하면 SQL·mem9 호출 기록처럼 서버 메모리에 있던 기록은 비워집니다.
- 테이블 구조(`sql/schema.sql`)를 바꿨다면 로컬에서 `python scripts/seed.py --recreate`를 실행합니다.

머지 전 검증 명령은 [CLAUDE.md](CLAUDE.md)에 있습니다.

## 자주 생기는 문제

| 증상 | 원인과 해결 |
|---|---|
| "환경변수가 설정되지 않았습니다: ..." | Secrets에 해당 키가 없거나 `[auth]` 아래에 들어갔습니다. 최상위 키를 `[auth]` 위로 옮깁니다. |
| "로그인 설정([auth])이 없어 앱을 열 수 없습니다" | Secrets에 `[auth]` 섹션이 없습니다. 6단계 4번을 확인합니다. |
| Google 로그인 후 `redirect_uri_mismatch` | OAuth 클라이언트의 리디렉션 URI와 Secrets의 `redirect_uri`가 다릅니다. `https://`, 주소, `/oauth2callback`까지 똑같이 맞춥니다. |
| Google 로그인 화면에서 "액세스 차단됨" | OAuth 동의 화면이 '테스트' 상태입니다. 프로덕션으로 게시합니다. |
| "투자 기회가 없습니다" | Signal 분석 화면에서 AI 분석을 먼저 실행합니다. |
| 화면에 테이블이 없다는 SQL 오류 | 시드를 적재하지 않았습니다. 로컬에서 `python scripts/seed.py`를 실행합니다. |
| `EMBED_TEXT` 관련 오류 | 클러스터가 AWS가 아닙니다. AWS 리전에 클러스터를 다시 만듭니다. |
| push 뒤 `ImportError` | 위의 Reboot app을 실행합니다. |
| Secrets를 고쳤는데 반영되지 않음 | Secrets 저장 뒤에도 Reboot app을 한 번 실행합니다. |

## 보안 주의

- `.env`, `.streamlit/secrets.toml`은 git에 올리지 않습니다(`.gitignore`에 포함). 실제 값은 로컬 파일과 Cloud Secrets 화면에만 둡니다.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 응답이 LangSmith 서버로 전송됩니다. 기본값은 꺼짐입니다.
- 같은 TiDB 클러스터와 mem9 키를 여러 배포가 함께 쓰면 데이터가 섞입니다. 배포마다 따로 만드는 것을 권장합니다.
