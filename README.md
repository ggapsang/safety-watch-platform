# safety-watch-platform

**AI Safety Watch Platform** — 네트워크 카메라 영상을 웹으로 스트리밍하고, 카메라가 발행하는
ONVIF 이벤트를 안전 이벤트로 적재·조회·통계하는 관제 시스템입니다.

```
aivision/    ← 실제 코드 (서버 · 프론트엔드 · 배포)
refs/        ← 참조 전용. 실행하지 않습니다.
```

## 시작하기

[`aivision/README.md`](aivision/README.md) 를 보십시오. 요약하면 다음과 같습니다.

```bash
cd aivision/deploy
cp .env.example .env      # SECRET_KEY 를 채웁니다
docker compose up -d --build
# http://localhost:11880
```

## refs/ 에 있는 것

| 경로 | 내용 |
|---|---|
| `refs/demo/` | 디자인 시안. 실서버 없이 도는 단일 HTML 데모와 그 목업 계층(`shim.js`)입니다. 현재 UI 는 이 시안을 기준으로 구현했습니다. `shim.js` 에는 실장비에서 관측된 MQTT 토픽·페이로드 형태가 남아 있어 탐지 규칙의 근거가 됩니다. |
| `refs/legacy-iseco-pc/` | 이전 프로젝트(아이에스에코솔루션 컨베이어 부적합 탐지 PC 추론 앱) 전체입니다. RTSP 수신 루프와 YOLO 추론 코드가 들어 있어, 서버사이드 YOLO 를 구현할 때 참고 자산입니다. |

`refs/legacy-iseco-pc/app/detector.py` 에서 재사용할 수 있는 것과 없는 것:

- **재사용 가능** — `letterbox`, `xywh2xyxy`, `nms`, `_scale_back`(좌표 역변환),
  ONNX Runtime / TorchScript 백엔드 로더(CUDA→CPU 자동 폴백 포함)
- **재사용 불가** — 후처리 전체. 그 코드는 4클래스를 그래프 안 `ReduceMax` 로 뭉개 단일 클래스로
  내보내는 특정 export 에 맞춰져 있고 yolov7 앵커가 하드코딩돼 있습니다. 새 모델은 다중 클래스
  헤드로 다시 써야 합니다.
