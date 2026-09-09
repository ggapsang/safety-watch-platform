/** 설정 파일 편집 — 왼쪽에 설명, 오른쪽에 JSON.
 *
 * 왜 표가 아니라 JSON 편집기인가. 바인딩은 필드가 스무 개가 넘고 그중 대부분이
 * '표현식' 이라 입력칸으로 늘어놓으면 한 화면에 안 들어온다. 그리고 이 화면이 고치는
 * 것은 **저장소에 커밋할 파일 그 자체**다. 화면에서 본 것과 커밋할 것이 글자 그대로
 * 같아야 무엇을 커밋하는지 알 수 있다.
 *
 * 저장하면 DB 와 파일을 한 번에 맞춘다. 그 파일(deploy/config/platform.json)을
 * 커밋하면 다른 사람이 클론했을 때 같은 배선으로 뜬다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { Button, Card, Section } from "../components/ui";
import { api } from "../lib/api";
import type { PlatformConfig } from "../lib/types";

const pretty = (value: unknown) => JSON.stringify(value, null, 2) + "\n";

/* ─────────────────────────────────────────────────────────── 왼쪽 설명 */

interface Doc {
  key: string;
  desc: string;
  /** 고를 수 있는 값이 정해져 있으면 적는다 */
  values?: string;
}

const SOLUTION_DOCS: Doc[] = [
  {
    key: "code",
    desc: "항목 코드. 이벤트·통계·차트가 전부 이 코드로 묶이고, 바인딩이 이것을 가리킵니다",
  },
  { key: "name", desc: "화면에 쓰는 이름" },
  { key: "short_name", desc: "좁은 자리에 쓰는 짧은 이름" },
  { key: "description", desc: "설명. 화면에 보조 문구로 나옵니다" },
  { key: "event_type", desc: "이벤트 목록에 찍히는 표시명" },
  { key: "color", desc: "차트 색", values: "#rrggbb" },
  { key: "sort_order", desc: "정렬 순서. 작을수록 앞" },
  { key: "enabled", desc: "끔/켬" },
];

const MATCH_DOCS: Doc[] = [
  { key: "name", desc: "바인딩 이름. 이 이름으로 찾아 고치므로 겹치면 안 됩니다" },
  { key: "enabled", desc: "끔/켬" },
  { key: "transport", desc: "어느 통로로 들어오는가", values: "mqtt | http" },
  { key: "topic_pattern", desc: "MQTT 와일드카드. + 는 한 조각, # 는 나머지 전부" },
  {
    key: "payload_filter",
    desc: '내용 조건. 예: {"$.Data.Type": "fire"}. null 이면 내용을 보지 않습니다',
  },
  {
    key: "payload_profile",
    desc: "onvif 면 ONVIF 양식을 평면 dict 로 먼저 폅니다",
    values: "raw | onvif",
  },
  { key: "priority", desc: "여럿이 동시에 걸릴 때 순서. 작을수록 먼저" },
  {
    key: "live_only",
    desc: "켜면 이벤트로 쌓지 않고 화면 박스로만 흘립니다. 사람이 서 있는 동안 초당 여러 번 나오는 판독을 쌓으면 DB 가 무너집니다",
  },
];

const CAMERA_DOCS: Doc[] = [
  {
    key: "camera_from",
    desc: "어느 카메라의 일인지 어떻게 아는가",
    values: "topic_mac | topic_segment | payload | fixed",
  },
  {
    key: "camera_expr",
    desc: "위가 topic_segment / payload 일 때 꺼낼 자리. 예: $topic[2], $.camera_id",
  },
  {
    key: "camera_id",
    desc: "camera_from=fixed 일 때 못 박는 카메라 번호. 환경을 옮기면 번호가 안 맞을 수 있는데, 없는 번호면 비운 채 꺼집니다",
  },
];

const ITEM_DOCS: Doc[] = [
  { key: "item_from", desc: "탐지 항목을 고정할지 페이로드에서 꺼낼지", values: "fixed | payload" },
  {
    key: "solution_code",
    desc: "item_from=fixed 일 때 쓸 항목 코드. live_only 면 null 이어도 됩니다",
  },
  { key: "item_expr", desc: "item_from=payload 일 때 꺼낼 자리. 예: $.item" },
];

const STATE_DOCS: Doc[] = [
  {
    key: "state_expr",
    desc: "비우면 메시지 수신 자체가 발생입니다. 채우면 그 값으로 발생과 해제를 가릅니다",
  },
  { key: "state_active", desc: "이 값이면 발생" },
  { key: "state_inactive", desc: "이 값이면 해제" },
];

const EXTRA_DOCS: Doc[] = [
  {
    key: "module_expr",
    desc: "무엇이 판정했나. 페이로드에 모듈 식별자가 실려 오면 여기로 뽑습니다",
  },
  { key: "confidence_expr", desc: "신뢰도를 꺼낼 자리" },
  { key: "ts_expr", desc: "발생 시각을 꺼낼 자리. 비우면 수신 시각을 씁니다" },
  { key: "boxes_expr", desc: "박스 배열을 꺼낼 자리. 예: $.boxes" },
  {
    key: "boxes_format",
    desc: "박스 좌표 형식",
    values: "xyxy_norm | xyxy_px | xywh_px | cxcywh_norm",
  },
];

const RUNTIME_DOCS: Doc[] = [
  {
    key: "event_dedup_sec",
    desc: "같은 카메라·항목 이벤트를 이 창 안에서 한 번만 남깁니다. 의미 수준이고 DB 로 판단합니다",
  },
  {
    key: "inbound_min_interval_sec",
    desc: "완충장치. 글자 그대로 같은 메시지를 이 간격 안에서는 문 앞에서 버립니다. 전송 수준이고 메모리로 판단합니다. 0 이면 끕니다",
  },
  {
    key: "mqtt_log_mode",
    desc: "원문 적재 정책. 기본은 쌓지 않습니다",
    values: "off | unmatched | all",
  },
  { key: "mqtt_log_topics", desc: "위 정책과 무관하게 이 토픽만 쌓습니다. 예: vendorZ/#" },
  { key: "mqtt_log_retention_days", desc: "원문 로그 보존 일수" },
  {
    key: "record_max_gb",
    desc: "상시 녹화 총 용량 상한(GB). 넘으면 오래된 것부터 버립니다. 0 이면 제한 없음",
  },
  { key: "snapshot_on_event", desc: "이벤트가 생길 때 스냅샷을 찍을지" },
];

const LIST_DOCS: Doc[] = [
  {
    key: "solutions / bindings",
    desc: "JSON 배열입니다. 여러 개면 { } 를 쉼표로 나열합니다 — [ {...}, {...} ]. 하나만 있어도 배열이고, 없으면 빈 배열 [] 입니다",
  },
  {
    key: "mqtt_log_topics",
    desc: "배열이 아니라 문자열 하나입니다. 여러 채널은 쉼표로 구분합니다 — \"vendorZ/#, cam/+/alarm\". 앞뒤 공백은 알아서 지웁니다",
  },
  {
    key: "payload_filter",
    desc: "조건을 여러 개 넣으면 전부 만족해야 걸립니다(AND). 예: {\"$.Type\": \"fire\", \"$.State\": \"true\"}",
  },
  {
    key: "boxes_expr",
    desc: "여기는 표현식 하나만 적습니다. 그 자리에 박스가 배열로 들어 있어야 합니다 — 표현식을 여러 개 나열할 수는 없습니다",
  },
];

const EXPR_DOCS: Doc[] = [
  { key: "$.a.b.c", desc: "페이로드에서 꺼냅니다" },
  { key: "$topic[2]", desc: "토픽을 / 로 자른 뒤 n 번째 조각 (0 부터)" },
  { key: "$mac", desc: "토픽 앞머리의 MAC 주소" },
  { key: "$topic", desc: "토픽 전체" },
  { key: "그 밖의 값", desc: "리터럴 문자열로 봅니다" },
];

function DocList({ title, note, docs }: { title: string; note?: string; docs: Doc[] }) {
  return (
    <div className="mb-6 last:mb-0">
      <div className="mb-[6px] text-[11px] font-semibold uppercase tracking-[.11em] text-muted-soft">
        {title}
      </div>
      {note && <p className="mb-[10px] text-[12px] leading-[1.6] text-muted">{note}</p>}
      <dl className="space-y-[10px]">
        {docs.map((d) => (
          <div key={d.key}>
            <dt className="font-mono text-[12px] font-semibold text-ink">{d.key}</dt>
            <dd className="mt-[2px] text-[12px] leading-[1.6] text-muted">
              {d.desc}
              {d.values && (
                <span className="ml-1 font-mono text-[11.5px] text-muted-soft">({d.values})</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────── 화면 */

export function ConfigFile() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["config"], queryFn: api.config });

  const [text, setText] = useState("");
  const [saved, setSaved] = useState<{ path: string; ok: boolean; note: string } | null>(null);
  const [failed, setFailed] = useState("");

  // 서버에서 받은 것으로 편집기를 채운다. 이미 글자가 있으면 덮어쓰지 않는다 —
  // 다시 조회한 결과가 사람이 치고 있던 것을 지우면 안 된다.
  const original = useMemo(() => (data ? pretty(data) : ""), [data]);
  useEffect(() => {
    if (original && !text) setText(original);
  }, [original, text]);

  const dirty = Boolean(text) && text !== original;

  // JSON 이 깨졌는지 타이핑하는 동안 알려 준다. 저장을 눌러야 아는 것보다 낫다.
  const parseError = useMemo(() => {
    if (!text.trim()) return "비어 있습니다";
    try {
      JSON.parse(text);
      return "";
    } catch (e) {
      return e instanceof Error ? e.message : "JSON 형식이 아닙니다";
    }
  }, [text]);

  const save = useMutation({
    mutationFn: () => api.saveConfig(JSON.parse(text) as PlatformConfig),
    onSuccess: (res) => {
      setFailed("");
      setSaved({ path: res.path, ok: res.saved, note: res.note });
      // 서버가 정리한 결과(정렬·기본값 채움)를 그대로 되받아 편집기에 넣는다.
      setText(pretty(res.config));
      qc.setQueryData(["config"], res.config);
      // 바인딩·항목이 바뀌었으니 그것을 보는 화면들도 다시 읽게 한다.
      void qc.invalidateQueries({ queryKey: ["bindings"] });
      void qc.invalidateQueries({ queryKey: ["solutions"] });
      void qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e: Error) => {
      setSaved(null);
      setFailed(e.message);
    },
  });

  const canSave = dirty && !parseError && !save.isPending;

  const format = () => {
    if (parseError) return;
    setText(pretty(JSON.parse(text)));
  };

  const revert = () => {
    setText(original);
    setFailed("");
    setSaved(null);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (canSave) save.mutate();
      return;
    }
    // Tab 이 편집기 밖으로 나가 버리면 JSON 을 손으로 들여쓸 수 없다.
    if (e.key === "Tab") {
      e.preventDefault();
      const el = e.currentTarget;
      const { selectionStart: a, selectionEnd: b } = el;
      setText(text.slice(0, a) + "  " + text.slice(b));
      requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
    }
  };

  const lines = text ? text.split("\n").length : 0;

  return (
    <Section
      title="설정 파일"
      desc="탐지 항목 · 인바운드 바인딩 · 운영 설정. 저장하면 DB 와 파일에 함께 반영됩니다"
      actions={
        <>
          <Button size="sm" onClick={format} disabled={!!parseError}>
            서식 정리
          </Button>
          <Button size="sm" onClick={revert} disabled={!dirty}>
            되돌리기
          </Button>
          <Button size="sm" variant="primary" onClick={() => save.mutate()} disabled={!canSave}>
            {save.isPending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
        {/* 왼쪽 — 무엇을 적는 자리인가 */}
        <Card className="max-h-[calc(100vh-220px)] overflow-y-auto">
          <p className="mb-5 text-[12.5px] leading-[1.7] text-muted">
            여기서 고치는 것은 저장소의{" "}
            <code className="font-mono text-[11.5px] text-ink">
              aivision/deploy/config/platform.json
            </code>{" "}
            입니다. 저장한 뒤 그 파일을 커밋하면 다른 사람이 클론했을 때 같은 배선으로 뜹니다.
            이벤트나 원문 로그처럼 쌓이는 데이터는 DB 에 남고 이 파일에 들어가지 않습니다.
          </p>

          <DocList
            title="solutions — 탐지 항목"
            note="무엇을 이벤트로 볼 것인가의 목록입니다. 여기서 지울 수는 없습니다 — 항목을 지우면 그 항목의 이벤트가 함께 지워지므로 관리자 화면에서 지웁니다."
            docs={SOLUTION_DOCS}
          />
          <DocList
            title="bindings — 무엇이 걸리나"
            note="들어온 메시지를 내부 신호로 옮기는 규칙입니다. 여기서 지운 줄은 지워집니다."
            docs={MATCH_DOCS}
          />
          <DocList title="bindings — 어느 카메라인가" docs={CAMERA_DOCS} />
          <DocList title="bindings — 어떤 항목인가" docs={ITEM_DOCS} />
          <DocList title="bindings — 발생인가 해제인가" docs={STATE_DOCS} />
          <DocList title="bindings — 부가 추출" docs={EXTRA_DOCS} />
          <DocList title="표현식 문법" note="넷뿐입니다." docs={EXPR_DOCS} />
          <DocList
            title="여러 개를 적을 때"
            note="자리마다 표기가 다릅니다. 배열인 곳과 쉼표로 구분하는 문자열인 곳이 섞여 있어 헷갈리기 쉽습니다."
            docs={LIST_DOCS}
          />
          <DocList
            title="runtime — 운영 설정"
            note="비우면 배포가 정한 기본값(compose 환경변수)을 씁니다."
            docs={RUNTIME_DOCS}
          />
        </Card>

        {/* 오른쪽 — 파일 그 자체 */}
        <Card padded={false} className="flex min-h-[520px] flex-col overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-[10px]">
            <span className="font-mono text-[11.5px] text-muted">deploy/config/platform.json</span>
            <span className="text-[11.5px] text-muted-soft">
              {lines}줄{dirty && " · 저장 안 됨"}
            </span>
          </div>

          {isLoading ? (
            <div className="flex-1 p-6 text-[13px] text-muted-soft">불러오는 중…</div>
          ) : error ? (
            <div className="flex-1 p-6 text-[13px] text-error">{(error as Error).message}</div>
          ) : (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              className="flex-1 resize-none bg-canvas p-4 font-mono text-[12.5px] leading-[1.7] text-ink outline-none"
            />
          )}

          <div className="border-t border-hairline px-4 py-[10px] text-[12px]">
            {parseError ? (
              <span className="text-error">JSON 오류: {parseError}</span>
            ) : failed ? (
              <span className="text-error">{failed}</span>
            ) : saved ? (
              <span className={saved.ok ? "text-muted" : "text-error"}>
                {saved.ok ? `저장했습니다 — ${saved.path} (이 파일을 커밋하세요)` : saved.note}
              </span>
            ) : (
              <span className="text-muted-soft">Ctrl+S 로도 저장됩니다</span>
            )}
          </div>
        </Card>
      </div>
    </Section>
  );
}
