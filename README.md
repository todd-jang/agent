# ReAct Agent 서버 배포 가이드

FastAPI, LangGraph, Ollama를 사용하는 한국어 AI 에이전트 서비스입니다. Nginx가 API와 함께 간단한 브라우저 채팅 화면을 제공합니다.

## 1. 구성과 요청 흐름

```text
브라우저 → Nginx (:80) → 정적 채팅 화면
                     └→ FastAPI 앱 (:80, 내부 네트워크) → Ollama (내부 네트워크)
                                                        └→ SQLite (Docker 볼륨)
```

기본 Compose 파일은 한 대의 Linux 서버에서 Nginx, 앱, Ollama를 실행합니다. Ollama 모델은 첫 실행 때 다운로드되고 Docker 볼륨에 보관됩니다. `ollama-init`은 모델 다운로드를 끝내면 종료되는 일회성 작업이므로 `docker compose ps`에서 `Exited (0)`으로 보이는 것이 정상입니다.

## 2. 서버 준비

- Docker Engine과 Docker Compose 플러그인 설치
- 외부 접속을 허용할 TCP 포트 준비
- 모델 파일 저장 공간과 모델 실행 메모리 확보

`qwen2.5:7b`는 모델 파일만 약 4.7 GB이며, 실행 시에는 여기에 컨텍스트와 운영체제의 메모리가 추가로 필요합니다. 메모리가 빠듯한 서버에서는 처음부터 작은 모델과 컨텍스트를 사용하세요.

```env
MODEL_NAME=qwen2.5:3b
NUM_CTX=4096
```

NVIDIA GPU를 사용하려면 서버에 NVIDIA 드라이버와 NVIDIA Container Toolkit을 설치하고, Compose의 Ollama 서비스에 GPU 장치 설정을 추가해야 합니다. 현재 기본 `docker-compose.yml`은 GPU를 명시적으로 할당하지 않으므로, GPU가 반드시 사용된다고 가정하지 마세요.

## 3. 설정 및 시작

프로젝트 파일을 서버에 배치한 뒤 프로젝트 디렉터리에서 실행합니다.

```bash
cp .env.example .env
```

`.env`에서 `API_KEY`를 충분히 길고 예측 불가능한 값으로 변경합니다. 예를 들어 서버의 `openssl`을 사용할 수 있습니다.

```bash
openssl rand -hex 32
```

생성한 값으로 `.env`의 `API_KEY`를 교체합니다. 실제 키와 `.env` 파일은 저장소에 커밋하거나 공개하지 마세요. 키가 노출된 적이 있다면 새 키로 교체하세요.

필요한 경우 모델/컨텍스트를 정하고, 웹 검색을 쓸 때만 `TAVILY_API_KEY`를 설정합니다. 비회원 채팅을 쓰려면 서비스 도메인을 등록한 Cloudflare Turnstile 사이트 키와 비밀 키를 `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`에 설정합니다. 키가 비어 있으면 게스트 채팅은 비활성화됩니다. 기본 `DATABASE_URL`은 비어 있어 노드의 SQLite 샘플 데이터베이스를 사용합니다.

```bash
docker compose config --quiet
docker compose up -d --build
```

최초 실행에서는 Ollama 이미지와 모델 다운로드가 진행됩니다. 로그와 서비스 상태를 확인합니다.

```bash
docker compose ps -a
docker compose logs -f ollama-init
docker compose logs --tail=100 app ollama nginx
```

`app`과 `nginx`가 `healthy`인지 확인합니다. 모델 다운로드가 끝난 `ollama-init`의 정상 종료는 오류가 아닙니다.

## 4. 배포 확인 및 API 사용

서버 내부에서 상태를 확인합니다.

```bash
curl -i http://localhost/health
curl -i http://localhost/ready
```

`/health`는 앱 프로세스 상태, `/ready`는 Ollama 모델과 데이터베이스 준비 상태를 확인합니다. 정상 응답은 HTTP `200`입니다.

채팅 요청은 API 키가 필요합니다.

```bash
curl -i -X POST http://localhost/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"message":"안녕하세요"}'
```

성공 응답 예시:

```json
{
  "answer": "안녕하세요. 무엇을 도와드릴까요?",
  "tools_used": []
}
```

`YOUR_API_KEY`를 `.env`에 설정한 실제 키로 바꾸세요. 외부 컴퓨터에서는 `localhost` 대신 서버의 도메인 또는 IP를 사용합니다.

`http://localhost/`에서 비회원 채팅 화면을, `http://localhost/docs`에서 FastAPI Swagger UI를 엽니다. 게스트 요청은 Turnstile 확인을 거치며 Nginx에서 IP당 분당 3회로 제한됩니다. 관리자용 Swagger `POST /chat`은 `X-API-Key` 헤더가 필요합니다. 이 비밀 키는 게스트 화면에 포함되지 않습니다. 기본 Nginx 설정은 `/`, `/chat`, `/guest/chat`, `/guest/config`, `/health`, `/ready`, `/lb-health`, Swagger UI(`/docs`), OpenAPI 스키마(`/openapi.json`), ReDoc(`/redoc`)을 처리하고 그 외 경로는 404입니다. 요청 본문 크기와 메시지 길이에도 제한이 있습니다.

## 5. 운영 환경 보안

기본 Compose 설정은 HTTP 포트 `80`만 공개합니다. 인터넷에 직접 공개하기 전에 반드시 HTTPS를 구성하세요. 예를 들어 TLS 인증서를 관리하는 외부 로드밸런서 또는 리버스 프록시 뒤에 이 서비스를 두고, 외부에서는 HTTPS만 허용하도록 설정합니다. 평문 HTTP에서는 `X-API-Key`가 암호화되지 않습니다.

- 비회원 채팅은 Cloudflare Turnstile 검증과 Nginx IP당 분당 3회 제한을 적용합니다. 이는 계정별 사용량 한도나 비용 상한이 아니며, 단일 Nginx 인스턴스 기준입니다. 앱 포트를 직접 공개하지 말고 Nginx를 통해서만 접근하게 하세요.
- 관리자용 `/chat` API 키는 브라우저 코드에 넣거나 일반 사용자에게 배포하지 마세요. Turnstile은 봇 방어 보조 수단일 뿐 사용자 인증이나 비용 상한을 보장하지 않습니다. 대규모 공개 전에 비용 모니터링과 추가 남용 방지책을 마련하세요.
- 외부 배포에는 도메인의 DNS, 서버 방화벽, TLS 인증서와 HTTPS 리다이렉트 설정이 필요합니다. 이 저장소의 Compose는 HTTP만 구성하므로 HTTPS가 필요한 공개 설정은 서버/도메인 정보에 맞춰 별도로 구성하세요.
- 방화벽에서는 필요한 웹 포트만 허용하고 SSH는 신뢰할 수 있는 관리 IP로 제한합니다.
- Ollama 포트 `11434`와 앱 컨테이너 포트는 인터넷에 공개하지 마세요. 기본 단일 서버 Compose에서는 두 포트가 호스트에 게시되지 않습니다.
- `.env`는 안전하게 보관하고, 키를 정기적으로 교체하세요.
- 운영 모델 이미지는 변경을 통제할 수 있도록 검증 후 버전을 고정하는 것을 고려하세요. 현재 Compose는 `ollama/ollama:latest`를 사용합니다.
- 운영 데이터가 필요하다면 샘플 SQLite/샘플 스키마를 실제 데이터베이스 구성으로 교체하고 백업·복구 절차를 마련하세요.

## 6. 업데이트 및 관리

새 코드로 이미지를 다시 빌드하고 서비스를 갱신합니다.

```bash
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=100 app nginx ollama
```

서비스를 중지할 때는 다음을 사용합니다.

```bash
docker compose down
```

일반적인 `down`은 모델과 앱 데이터 볼륨을 보존합니다. `docker compose down -v`는 볼륨과 저장 데이터를 삭제하므로, 데이터 삭제를 의도하고 백업을 확인한 경우가 아니면 사용하지 마세요.

## 7. 문제 해결

### `/`가 404를 반환함

채팅 화면을 배포했다면 `/`에서 화면이 열려야 합니다. 404가 계속되면 Nginx 컨테이너를 재생성해 `web/` 파일 마운트를 적용하고 로그를 확인하세요.

### `/chat`이 401을 반환함

`X-API-Key` 헤더가 없거나 `.env`의 `API_KEY`와 일치하지 않습니다. `.env`를 확인하고 설정 변경 후 `docker compose up -d`로 앱을 다시 만드세요.

### `/chat`이 502를 반환하거나 Ollama 로그에 `signal: killed`가 나타남

모델 서버 프로세스가 메모리 부족으로 종료됐을 가능성이 큽니다. `docker compose logs --tail=200 ollama app`으로 확인한 뒤 서버 또는 Docker Desktop에 메모리를 더 할당하거나 `.env`에서 더 작은 모델과 컨텍스트(예: `qwen2.5:3b`, `NUM_CTX=4096`)를 설정하세요. 변경 후 아래 명령으로 앱과 모델 초기화 작업을 재생성합니다.

```bash
docker compose up -d --force-recreate ollama-init app
```

### `/ready`가 503을 반환함

모델 다운로드가 끝났는지, Ollama와 데이터베이스에 접근 가능한지 확인합니다.

```bash
docker compose ps -a
docker compose logs --tail=200 ollama ollama-init app
```

## 8. 환경 변수

| 변수 | 용도 | 기본값 |
|---|---|---|
| `API_KEY` | `/chat` API 인증 키 | 필수 |
| `MODEL_NAME` | Ollama 모델 이름 | `qwen2.5:7b` |
| `NUM_CTX` | 모델 컨텍스트 크기 | Compose에서 `8192` |
| `LLM_TIMEOUT` | Ollama 응답을 기다리는 최대 시간(초) | `300` |
| `OLLAMA_NUM_PARALLEL` | Ollama 병렬 처리 수 | `1` |
| `TAVILY_API_KEY` | 웹 검색 기능 키. 비어 있으면 검색 도구 비활성화 | 비어 있음 |
| `DATABASE_URL` | PostgreSQL 연결 문자열. 비어 있으면 SQLite 사용 | 비어 있음 |
| `TURNSTILE_SITE_KEY` | 브라우저에 공개되는 Turnstile 사이트 키 | 비어 있음 |
| `TURNSTILE_SECRET_KEY` | 서버 전용 Turnstile 검증 비밀 키 | 비어 있음 |

애플리케이션에는 `DB_PATH`, `DB_STATEMENT_TIMEOUT_MS`, `RECURSION_LIMIT`, `LLM_TIMEOUT`, `OLLAMA_BASE_URL` 등의 추가 설정이 있습니다. 현재 단일 서버 Compose는 Ollama URL과 주요 설정 일부를 서비스 내부 값으로 지정하므로, Compose 환경에 변수를 추가할 때 `main.py`의 기본값과 함께 확인하세요.

## 9. PostgreSQL 및 여러 서버 구성

`docker-compose.db.yml`은 별도 PostgreSQL 테스트 구성을 위한 파일입니다. DB 초기화 스크립트는 데이터 볼륨이 비어 있을 때 한 번만 실행됩니다. 실서비스 데이터베이스로 사용할 때는 샘플 `db/init/01_schema.sql`을 실제 스키마로 교체하고, 접근 가능한 사설 네트워크와 백업을 구성하세요. 데이터가 들어 있는 볼륨에 초기화 스크립트를 수정해도 기존 DB에는 자동 적용되지 않습니다.

여러 앱 노드, 공유 DB, 로드밸런서를 사용하는 구성은 네트워크·TLS·방화벽 설정이 추가로 필요합니다. 현재 저장소에는 README에서 설명하던 `lb/` 배포 디렉터리가 없으므로, 이 문서의 검증 가능한 배포 범위는 단일 서버 구성입니다. 멀티 노드 설정을 그대로 운영에 적용하지 마세요.
