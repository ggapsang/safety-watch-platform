/** 이벤트 상세 모달 — 캡쳐와 발생 정보를 확인한다.
 *
 * 처리 상태(확인·조치완료) 흐름은 아직 없다. 무엇을 어떤 기준으로 처리로 볼지 정해지면
 * 그때 붙인다. 지금은 '무엇이 언제 어디서 잡혔는지'만 보여 준다.
 */
import { api } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import type { SafetyEvent } from "../lib/types";
import { Button, Modal } from "./ui";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-hairline/60 py-[10px] last:border-b-0">
      <span className="shrink-0 text-[12.5px] text-muted">{label}</span>
      <span className="text-right text-[13px] text-body">{value}</span>
    </div>
  );
}

export function EventDetail({
  event,
  onClose,
}: {
  event: SafetyEvent | null;
  onClose: () => void;
}) {
  if (!event) return null;

  return (
    <Modal
      open
      onClose={onClose}
      title={`${event.id} · ${event.type}`}
      width={700}
      footer={
        <>
          {event.has_clip && (
            <a href={api.clipUrl(event.id)} download>
              <Button>영상 클립 내려받기</Button>
            </a>
          )}
          <div className="flex-1" />
          <Button variant="primary" onClick={onClose}>
            닫기
          </Button>
        </>
      }
    >
      <div className="mb-5 overflow-hidden rounded-lg border border-video-line bg-video-bg">
        {event.has_snapshot ? (
          <div className="relative">
            <img src={api.snapshotUrl(event.id)} alt={`${event.id} 캡쳐`} className="block w-full" />
            {event.boxes.length > 0 && (
              <svg
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
                className="absolute inset-0 h-full w-full"
              >
                {event.boxes.map((b, i) => (
                  <rect
                    key={i}
                    x={b.x1}
                    y={b.y1}
                    width={Math.max(0, b.x2 - b.x1)}
                    height={Math.max(0, b.y2 - b.y1)}
                    fill="none"
                    stroke="#e8703a"
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
              </svg>
            )}
          </div>
        ) : (
          <div className="flex aspect-video items-center justify-center text-[12.5px] text-[#8e8b82]">
            캡쳐 없음
          </div>
        )}
      </div>

      <Row label="발생 시간" value={<span className="tnum">{fmtDateTime(event.ts)}</span>} />
      <Row label="카메라" value={`${event.cam_name} · ${event.cam_location}`} />
      <Row label="이벤트" value={event.type} />
      <Row
        label="탐지 소스"
        value={event.source === "mqtt" ? "카메라 엣지 (ONVIF/MQTT)" : event.source}
      />
      <Row
        label="영상 클립"
        value={
          event.has_clip ? (
            <a
              href={api.clipUrl(event.id)}
              download
              className="text-primary hover:text-primary-active"
            >
              내려받기
            </a>
          ) : (
            <span className="text-muted-soft">없음 (상시 녹화 꺼짐 또는 해당 구간 녹화 없음)</span>
          )
        }
      />
      {event.confidence != null && (
        <Row
          label="신뢰도"
          value={<span className="tnum">{(event.confidence * 100).toFixed(0)}%</span>}
        />
      )}
    </Modal>
  );
}
