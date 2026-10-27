# 매칭용 모델 서비스 배치

Rust 게임 서버와 별도 CPU 추론 컨테이너를 실행합니다. 기본 compose에는 연결하지 않았습니다. 이 설정은 학습 작업을 실행하지 않으며, 검증한 v9 bundle을 읽기 전용으로 로드합니다.

`compose.yml`은 포트를 publish하지 않는 전용 `internal: true` 네트워크를 만듭니다. 서비스는 `model-bot` hostname으로 해석되는 자기 내부 인터페이스에만 바인딩합니다. 게임 서버만 이 네트워크에 추가하며 토큰으로 요청을 인증합니다. dev와 운영은 network name·토큰·모델 디렉터리·Compose project name을 모두 분리하세요. 인터넷에 직접 노출하는 구성이 아닙니다.

## dev에서 활성화

1. Bot 저장소의 수동 `inference-image` workflow나 `Dockerfile.inference`로 이미지를 빌드하고 digest를 고정합니다. 모델 bundle은 검증 후 새 디렉터리에 복사합니다. `game-config.json`의 assignments에 기존 활성 봇 ID 한 개부터 지정합니다.
2. `runtime.env.example`을 `runtime.env`로 복사해 이미지와 절대 경로를 채웁니다. 32자 이상의 무작위 토큰을 파일로 준비하고, UID 10001인 추론 컨테이너와 게임 컨테이너만 읽을 수 있게 권한을 설정합니다. 실제 토큰·env·체크포인트는 커밋하지 않습니다.
3. 저장소 루트에서 설정을 확인한 뒤 모델 서비스부터 시작합니다.

```sh
docker compose --env-file learned-bot/runtime.env -p dev-model-bot -f learned-bot/compose.yml config --quiet
docker compose --env-file learned-bot/runtime.env -p dev-model-bot -f learned-bot/compose.yml up -d --wait
docker compose --env-file learned-bot/runtime.env -f slots/dev-game-rust.yml -f slots/dev-game-rust.learned.yml config --quiet
```

진행 중인 dev 경기가 끝나도록 서버를 먼저 drain한 뒤 다음 명령으로 Rust 서버를 재생성합니다. 이 명령은 실제 서비스를 바꾸므로 배포 시점에 실행합니다.

```sh
docker compose --env-file learned-bot/runtime.env -f slots/dev-game-rust.yml -f slots/dev-game-rust.learned.yml up -d
```

로비에도 동일한 `game-config.json` 파일만 읽기 전용으로 mount하고 `LEARNED_BOT_CONFIG=/learned-bot/game-config.json`을 설정합니다. 로비에는 checkpoint나 추론 토큰, inference network가 필요 없습니다. 로비의 모델 가용성 변경을 먼저 배포하고 assignments를 설정한 뒤 게임 서버에서 모델을 활성화하세요. 준비 상태 API가 없는 Java·구버전 서버에는 모델 봇을 배치하지 않으며 일반 봇과 사람 매칭은 유지합니다. `BOT_AUTO_MATCH_ENABLED=false`는 유지하며 자동 봇 경기 생성까지 켜지 않습니다.

추론 서비스의 `GET /models`는 인증 후 실제 로드된 descriptor를 반환합니다. 게임 서버가 계약을 대조하고 로비에는 준비된 봇 ID와 버전만 공개합니다. 서비스가 꺼져 있거나 모델이 누락되면 신규 모델 봇 매칭을 제외합니다. 이때 로비와 게임 서버의 assignments는 지우지 않습니다. 모델이 없는 상태를 일반 봇으로 잘못 해석하지 않도록 식별 목록을 유지해야 합니다.

## 자원과 운영 전환

초기 제한은 CPU 1개, 메모리 768MiB, PID 32개입니다. 용량 보장이 아니라 측정을 시작할 상한입니다. PyTorch와 모델의 RSS를 포함하므로 1GiB small-host의 기존 서비스와 함께 켜지 마세요. 여유 메모리를 확보하거나 별도 호스트에 두고 인증된 사설 연결을 설계해야 합니다. 이 PR은 원격 공개 endpoint를 만들지 않습니다.

게임 서버 요청 기본값은 300ms timeout, 500ms 간격, HTTP worker 2개입니다. 서비스는 한 번에 추론 하나를 실행하고 초과 요청에는 503을 반환합니다. 모든 경기 요청을 무제한 대기열에 쌓지 않습니다. dev에서 동시 봇 수를 늘려 응답 시간, RULE 전환 비율, 게임 loop frame rate와 RSS를 측정한 뒤 제한을 조정하세요.

현재 운영 슬롯은 Java 이미지여서 이 overlay를 그대로 적용하지 않습니다. Rust 운영 전환 때 두 슬롯 모두 운영 전용 inference network·descriptor·토큰 mount와 같은 환경 변수를 추가합니다. 최신 `swap.sh`는 컨테이너의 mounts/env/networks를 복제하므로 deployer 스크립트 수정은 필요 없습니다. stopped 슬롯에도 설정을 넣어야 다음 swap에서 유지됩니다.

모델 서비스의 Watchtower는 꺼져 있습니다. 서비스 교체 전 새 registry에 이전 버전과 새 버전을 함께 넣고, 새 descriptor를 읽는 게임 슬롯을 배포합니다. 이전 게임 슬롯의 drain 완료 뒤 이전 모델을 제거합니다. registry의 artifact 경로는 각 bundle 하위 디렉터리를 가리킬 수 있습니다. 같은 버전의 내용·hash를 덮어쓰지 마세요.

모델 서비스를 내리면 신규 모델 봇 매칭은 차단되고 진행 중 경기는 RULE로 이어갑니다. 일반 봇으로 되돌릴 때는 로비와 게임 서버 양쪽의 assignments를 제거하고 게임 서버를 drain합니다. 게임 서버의 health는 추론 서비스 장애와 분리되어 있습니다.
