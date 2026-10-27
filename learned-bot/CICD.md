# 모델 봇 CI/CD

Bot의 `inference-image` workflow는 PR에서 테스트와 amd64·ARM64 이미지 빌드를 실행합니다. 실제 체크포인트를 생성하는 작은 테스트를 각 이미지 안에서도 실행한 후, `main` push에서만 `sha-<commit>-<arch>` 태그를 게시합니다. workflow 요약의 digest를 release 파일에 사용합니다. 모델 학습·체크포인트 공개·운영 배포는 자동 실행하지 않습니다.

## 최초 dev 설정

이 경로는 기존 `dev-game-blue`·`dev-game-green`과 `dev-game-deployer`를 사용합니다. `slots/dev-game.yml`로 실행 중인 슬롯을 재생성하지 않습니다. 로비의 서버별 모델 가용성 검사와 로컬 경기 제외 기능을 먼저 배포해야 합니다.

1. 새 deployer 이미지를 빌드하고 digest로 고정합니다. 루트 compose와 `learned-bot/deployer.overlay.yml`을 합쳐 **dev-game-deployer만** 갱신합니다. 기존 `CICD_TOKEN` 설정은 유지합니다.
2. 서버에서 다음 변수를 준비합니다. 실제 값은 Git에 넣지 않습니다.

```dotenv
LEARNED_BOT_BUNDLE_ROOT=/srv/arcane/dev/model-releases
LEARNED_BOT_TOKEN_FILE=/srv/arcane/dev/secrets/bot.token
LEARNED_BOT_SERVER_TOKEN_FILE=/srv/arcane/dev/secrets/server.token
LEARNED_BOT_STATE_DIR=/srv/arcane/dev/model-state
```

bundle root와 토큰은 호스트와 deployer 안에서 같은 절대 경로로 보입니다. Docker daemon이 bind mount를 해석하는 위치는 호스트이기 때문입니다. 모델 컨테이너 UID 10001이 bundle과 추론 토큰을 읽을 수 있게 합니다. `server.token`은 game의 `/api/server/learned-bots`를 조회할 **WORDONLINE_SERVER** JWT입니다. drain용 WORDONLINE_CICD JWT와 구분합니다. 기존 컨테이너에서 토큰을 추출하지 말고 배포 관리자가 발급·설정합니다.

```sh
# 기존 deployer용 env와 위 변수를 함께 지정한 환경에서 실행합니다.
docker compose -f docker-compose.yml -f learned-bot/deployer.overlay.yml config --quiet
docker compose -f docker-compose.yml -f learned-bot/deployer.overlay.yml up -d --no-deps dev-game-deployer
```

계획이 아직 없는 첫 실행에서는 자동 polling이 실패하고 기존 game 슬롯은 유지됩니다. 첫 모델 배포가 계획을 생성합니다. 이후 game 이미지도 계획의 digest를 따르므로 일반 game 업데이트도 새 release 파일로 요청합니다.

3. GitHub Environment `learned-bot-dev`를 만들고 배포 branch를 `dev`로 제한합니다. `DEV_DEPLOY_HOST`, `DEV_DEPLOY_USER`, `DEV_DEPLOY_SSH_KEY`, `DEV_DEPLOY_KNOWN_HOSTS` secrets를 등록합니다. SSH 사용자는 dev deployer에 `docker exec`할 수 있어야 합니다. Docker 접근은 호스트 관리자 권한이므로 배포 전용 계정을 사용합니다. known_hosts는 신뢰한 host key를 사전에 등록하며 workflow에서 자동 수집하지 않습니다.

## 모델 준비와 배포

1. `<bundle root>/<release>/`에 registry, 체크포인트, data, vocabulary, relations, `game-config.json`을 준비합니다. 기존 release 디렉터리는 덮어쓰지 않습니다. registry에 등록한 artifact 해시는 서비스 시작 시 모두 검증합니다.
2. Bot의 `training/probe_inference.py`를 실제 grid bridge와 실행하면 `smoke.json`도 생성됩니다. 검증한 파일을 bundle에 포함합니다. 모델이 여러 개면 각 모델의 요청을 합친 배열을 사용합니다(최대 32개). 배포 probe는 모든 모델에 대해 `/infer`를 호출하고 응답 ID·frame·checkpoint·합법 행동을 확인합니다. 이 검사는 모델의 승률이나 동시 경기 용량을 보장하지 않습니다.
3. 로비가 읽는 assignments를 준비합니다. 새 버전으로 변경할 때 로비에 같은 assignments를 먼저 적용합니다. 구버전 game은 기대 버전을 제공하지 않으므로 신규 모델 매칭에서 제외되고 진행 중 경기는 계속됩니다. 이 workflow는 로비의 실행 설정을 수정하지 않습니다.
4. `release.example.json`을 `learned-bot/releases/<name>.json`으로 복사합니다. 모델 이미지와 호환되는 Rust game 이미지의 digest, registry·game-config·smoke 파일 SHA-256을 넣고 PR로 검토합니다. bundle의 서버 경로와 토큰은 이 파일에 넣지 않습니다. ARM64 서버에는 ARM64 이미지 digest를 사용합니다. 검증용 작은 checkpoint를 플레이 품질이 검증된 모델로 취급하지 않습니다.
5. infra `dev`의 `learned-bot` workflow를 실행하고 release 파일 경로를 지정합니다. workflow는 학습 파일을 업로드하지 않고 서버에 준비된 묶음을 검증합니다.

배포 순서는 다음과 같습니다.

- 기존 game 자동 배포와 같은 잠금 획득
- 고정 이미지 pull, 새 release 전용 내부 네트워크와 추론 프로세스 시작
- artifact strict 로딩, 인증된 `/models` 계약 대조, 모든 모델의 실제 HTTP 추론 검사
- 검증된 계획을 원자적으로 저장한 뒤 대기 game 슬롯에 해당 URL·descriptor·토큰·네트워크 적용
- 새 game의 health와 서버 인증 `/api/server/learned-bots` 응답을 assignments와 대조
- 로비 발견 대기 후 이전 game drain

추론 후보 검사에 실패하면 기존 계획과 game 슬롯은 그대로입니다. game 검증에 실패하면 새 슬롯만 정지합니다. drain 실패·시간 초과에서는 이전 경기를 강제 종료하지 않고 두 슬롯을 유지하며 같은 계획으로 재시도합니다. 토큰 만료 등으로 준비 상태 검사가 실패해도 기존 슬롯을 내리지 않습니다. 배포 중 프로세스 장애가 나면 기존 game/lobby의 신규 매칭 차단과 진행 경기 RULE fallback이 적용됩니다.

## 복구와 정리

이전 추론 컨테이너·네트워크·모델 디렉터리는 자동 삭제하지 않습니다. 이전 release 파일로 workflow를 실행하면 이전 이미지와 모델 조합을 대기 슬롯에 다시 적용합니다. 로비 assignments도 이전 버전으로 맞춰야 합니다. 두 game 슬롯이 실행 중인 drain 단계에서는 다른 release로 교체하지 않습니다. 같은 계획을 재시도하고 drain 완료 후 복구합니다.

release 이름은 내용을 바꿔 재사용할 수 없습니다. 같은 모델 파일을 쓰더라도 game 이미지가 바뀌면 새 release 디렉터리와 이름을 사용합니다. 모델 버전·checkpoint 자체를 변경할 필요는 없습니다.

후보를 포함해 추론 컨테이너를 최대 3개까지 보존합니다. 한 컨테이너당 CPU 0.5, 메모리 768MiB이므로 동시 보존 비용을 고려해야 합니다. 한도에 도달하면 자동 삭제 대신 배포를 거절합니다. 어떤 game 슬롯도 사용하지 않는 release인지, 복구에 필요한 이전 release는 남아 있는지 확인한 뒤 해당 모델 컨테이너와 네트워크만 정리합니다. 모델 파일은 별도 보존 정책을 따릅니다. 운영에는 이 workflow를 연결하지 않습니다.

## 로컬 검증

```sh
python3 -m unittest discover -s learned-bot/tests -v
bash -n swap.sh learned-bot/deploy.sh
docker build -t ac-deployer:check .
docker run --rm --network none --entrypoint python3 \
  -v "$PWD/learned-bot/tests:/tests:ro" \
  ac-deployer:check -m unittest discover -s /tests -v
```

이미지 안의 swap 테스트는 실제 `swap.sh`를 실행하되 Docker·HTTP 명령을 모의 처리합니다. 실제 dev 접속·매칭·부하 검증과 구분합니다.
