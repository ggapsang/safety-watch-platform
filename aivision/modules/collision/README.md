# 충돌 위험 모듈 (`collision`)

AMR 과 사람의 **접근 위험**과 **실제 충돌**을 판정하는 규칙형 플러그인입니다.
기획은 [`collision_module_plan_v2.md`](collision_module_plan_v2.md), 계약은
[`docs/플러그인-만들기.md`](../../docs/플러그인-만들기.md) 입니다.

## 무엇이 다른가

이 모듈은 **영상을 열지 않습니다.** 다른 모듈이 이미 낸 박스(`aivision/live/{camera_id}`)를
받아 판정만 합니다(계약 3장). 그래서 이미지에 모델도 디코더도 없고, 런타임은 numpy 하나입니다.

받은 박스에는 `track_id` 가 없으므로(계약 2장) **트래킹이 이 모듈의 몫**입니다.

```
aivision/live 구독 ─▶ 검증·발행자 필터 ─▶ 자체 트래킹(IoU) ─▶ 호모그래피(정규화→바닥 m)
   ─▶ 칼만(위치·속도) ─▶ 특징량(거리·상대속도·종횡비) ─▶ 위험/충돌 판정
   ─▶ Debouncer(전이) ─▶ aivision/detect 발행
```

## 파일

| 파일 | 하는 일 |
|---|---|
| `main.py` | 기동. 판정은 배경 스레드, 화면은 메인 스레드 |
| `service.py` | 계약의 네 가지 — 등록·일감·구독·heartbeat. 판정 스레드 |
| `pipeline.py` | 카메라 한 대. 버퍼 → 트래킹 → 판정 → 전이 → 발행 |
| `judge.py` | 특징량·위험 알람·충돌 확정 (MQTT 를 모른다) |
| `geometry.py` | 호모그래피(DLT)·전방 스윕·통로 판정 (순수 함수) |
| `tracking.py` | IoU 트래커 + 등속 칼만 |
| `settings.py` | `/config/collision.json` — 보정 4점·라벨·항목·임계값 |
| `config.py` | env(처음 값) + 설정 파일(사람이 고친 값) |
| `api.py`, `web/` | 자기 화면 — 보정·라벨·항목·임계값·최근 판정 |
| `tests.py` | 브로커·플랫폼·카메라 없이 도는 자체 검증 (99개) |

## 붙이는 법

`deploy/docker-compose.yml` 에 서비스를 하나 추가해야 합니다. **이 저장소의 다른 부분은
이 작업에서 건드리지 않았으므로, 아래 조각은 직접 붙여 주세요.**

```yaml
  mod-collision:
    build:
      # 컨텍스트가 modules/ 인 이유: 모듈들이 공유하는 _sdk 를 같이 담아야 한다.
      context: ../modules
      dockerfile: collision/Dockerfile
    ports:
      - "${COLLISION_UI_PORT:-12010}:8000"
    restart: unless-stopped
    depends_on:
      base-app: { condition: service_started }
      base-broker: { condition: service_started }
    volumes:
      - collision-config:/config
    environment:
      TZ: ${TZ:-Asia/Seoul}
      PLATFORM_URL: http://base-app:8000
      MQTT_HOST: base-broker
      MQTT_PORT: "1883"
      MODULE_ID: ${COLLISION_MODULE_ID:-collision}
      MODULE_NAME: ${COLLISION_MODULE_NAME:-충돌 위험}
      # 브라우저가 닿는 주소. 플랫폼이 이 주소를 탭으로 감싼다.
      PUBLIC_URL: ${COLLISION_PUBLIC_URL:-http://localhost:12010}
      # 구독할 발행자. 비우면 전부 받는다(자기 출력은 언제나 뺀다).
      SRC_MODULES: ${COLLISION_SRC_MODULES:-yolo-server,camera-meta}
      AMR_LABEL: ${COLLISION_AMR_LABEL:-자율주행로봇}
      PERSON_LABEL: ${COLLISION_PERSON_LABEL:-Human}
      RISK_ITEM: ${COLLISION_RISK_ITEM:-COLLISION_RISK}
      COLLISION_ITEM: ${COLLISION_COLLISION_ITEM:-COLLISION}
```

그리고 `volumes:` 에 `collision-config:` 를 한 줄 더합니다.

### 배포 전 선결 조건

`COLLISION_RISK` · `COLLISION` **탐지 항목을 플랫폼에 사람이 먼저 만들어야** 합니다.
`capabilities` 는 선언일 뿐 요구가 아닙니다(계약 7장). 항목이 없으면 발행은 나가지만
바인딩이 없어 이벤트가 쌓이지 않습니다.

## 설정

env 는 '처음 값', 모듈 화면에서 고친 값(`/config/collision.json`)이 이깁니다.
화면에 한 번도 들어가지 않은 현장도 env 만으로 돕니다.

| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `SRC_MODULES` | (전부) | 구독할 발행자 `module_id` 목록 |
| `AMR_LABEL` / `PERSON_LABEL` | (없음) | AMR·사람으로 볼 라벨(쉼표로 여럿) |
| `RISK_ITEM` / `COLLISION_ITEM` | `COLLISION_RISK` / `COLLISION` | 발행할 탐지 항목 코드. 비우면 이벤트를 만들지 않음 |
| `PUBLISH_LIVE` | `false` | 화면용 오버레이를 `aivision/live` 로도 낼지 |
| `TICK_HZ` | `10` | 판정 루프 주기 |
| `EVIDENCE_SNAPSHOT` / `EVIDENCE_KEEP` | `true` / `200` | 확정 순간 스냅샷 한 장을 `/config/evidence` 에 |
| `CONFIG_DIR` | `/config` | 설정·증거를 두는 볼륨 |

임계값(ε, Δt, T, v_h,max …)은 전부 화면에서 조정하며, env 이름은 대문자로 같습니다
(`eps_contact` → `EPS_CONTACT`). 목록과 범위는 `settings.py` 의 `TUNING` 한 곳에 있습니다.

## 사람이 할 일 — 모듈 화면 (기본 12010)

1. **바닥 4점 보정.** 스냅샷 위에서 치수를 아는 사각형(바닥 표시선 등)의 네 모서리를
   찍고 실제 좌표(m)를 적습니다. 보정이 없으면 거리를 미터로 잴 수 없어 **그 카메라는
   판정하지 않습니다**(박스는 계속 받습니다 — 화면에 '미보정' 으로 보입니다).
   저장하면 평균 재투영 오차가 미터로 나옵니다. 30cm 를 넘으면 화면이 경고합니다.
2. **라벨 연결.** 어떤 라벨이 AMR 이고 사람인지. 라벨은 발행하는 쪽 화면에서 바뀔 수
   있어 코드에 박지 않습니다(계약 3장).
3. **항목 연결.** 위험·충돌을 어느 탐지 항목으로 올릴지.
4. **임계값 조정.** 바꾸면 곧바로 반영됩니다(컨테이너 재시작 불필요).

## 판정

**위험 알람은 비대칭입니다.** AMR 은 관측된 속도로 앞을 쓸고(`sweep_tau`), 사람은 어느
방향으로든 `v_h,max` 로 움직일 수 있다고 봅니다. 사람의 관측 속도를 믿고 '안전' 이라고
말하면 틀렸을 때 사람이 다칩니다.

| 수준 | 조건 | 발행 |
|---|---|---|
| 주의 | 사람이 AMR 통로 안 | `COLLISION_RISK` active, `level=warn` |
| 임박 | 전방 스윕이 사람 도달 영역에 닿음 | `COLLISION_RISK` active, `level=imminent` |

전이 순간만 발행합니다. 한 에피소드 안에서 주의 → 임박으로 **올라갈 때 한 번** 더
냅니다(내려갈 때는 내지 않습니다 — 그것이 곧 깜빡임입니다). 해제 이벤트에는 끝난
알람의 수준이 실립니다.

> 주의 구역이 임박 구역을 품어야 주의가 먼저 뜹니다. 느린 AMR(0.3m/s 이하)에서는
> 사람의 최악 도달 반경이 통로보다 커서 **임박이 먼저 뜰 수 있습니다.** 오알람이 잦으면
> `t_imminent` 와 `v_h_max` 부터 줄이세요.

**충돌 확정은 보수적입니다.** 접촉만으로 확정하지 않습니다 — 박스가 겹치는 일은 하루에도
수없이 있습니다.

```
접촉 = 바닥 투영 거리 ≤ ε
충격 = AMR 급정지 ∨ 사람 속도 스파이크 ∨ 전도(종횡비 반전)
확정 = 접촉 ∧ 충격,  단 도착 시각 기준 Δt 창 안에서 동시
```

payload 에 `grade`(graze / knockdown / severe), `v_rel`, `distance`, `signals` 가 실립니다.
확정 순간 플랫폼 스냅샷 한 장을 `/config/evidence` 에 남깁니다(영상은 열지 않습니다).

## 검증

```
python tests.py         # 브로커·플랫폼·카메라 없이 돈다 (99개)
```

이 파일이 특히 중요한 이유: 이 모듈은 사고가 나야 결과가 보입니다. 현장에서 확인하려면
진짜로 부딪쳐야 하므로, 시나리오(접근·급정지·전도)를 코드로 만들어 돌립니다.
플랫폼 소스가 옆에 있으면 우리 payload 를 코어의 `mapping` 으로 직접 읽어 봅니다.

## 붙이기 전 점검 (기획 11장)

`tests.py` 로 확인되는 것과 통합에서 확인한 것을 나눠 적습니다.

```
[v] 등록·heartbeat                      통합 확인 (가짜 플랫폼)
[v] work 로 카메라 할당 반영, 빼면 멈춘다   reconcile + close 로 해제 발행
[v] live 구독 시 발행자 필터, 자기 출력 안 먹는다
[v] 잘못된 박스는 버린다                  tests: 이상한 입력
[v] 박스 좌표 0~1, 라벨 사람이 읽을 이름    tests: 전이 발행
[v] detect 는 전이 순간만                 tests: 전이 발행 / 통합 시나리오
[v] Δt 짝짓기에 도착 시각 사용             pipeline._take (pair_window)
[v] 콜백은 버퍼링만, 판정은 자기 스레드     pipeline.offer / service._judge_loop
[v] 설정을 자기 볼륨에 보관                /config/collision.json
[v] 플랫폼·브로커 내려도 안 죽는다          통합 확인 (둘 다 정지 후 복귀)
[ ] COLLISION_RISK·COLLISION 항목 존재      플랫폼에 사람이 먼저 만들어야 한다
[v] endpoint 를 브라우저에서 열면 화면이 뜬다
```

## 한계 (기획 10장)

- **가림**: 독립 채널(텔레메트리)이 없어 'AMR 근접 소실 + 급정지' 는 확정이 아닌 정황입니다.
- **단안 깊이**: 전도 순간 발 접지점이 무너져 그 순간의 거리·속도를 믿을 수 없습니다.
  그래서 순간값이 아니라 접근 → 충격 → 정지 시퀀스로 판정합니다.
- **초 단위 `ts`**: 100ms 충격의 정밀 시각 정합은 불가능합니다. 도착 시각과 Δt 창으로
  대신합니다.
- **보정 의존**: 호모그래피가 틀리면 모든 거리가 틀립니다. 화면의 재투영 오차를 보세요.
- **범위 밖**: 다중 카메라 3D, FMS 텔레메트리, VLM 경위 판독, 확정 시 전후 클립 녹화
  (기획 7장의 '선택' — 스냅샷만 구현했습니다. 녹화를 하려면 이미지에 ffmpeg 이 들어갑니다).
