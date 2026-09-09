/** 모듈이 들고 온 화면을 감싸는 탭.
 *
 * 코어는 이 안에 무엇이 있는지 모른다. 등록할 때 `endpoint` 를 준 모듈이면 무엇이든
 * 여기로 들어온다 — 학습 화면이든, 협력사가 만든 설정 화면이든 코어는 구분하지 않는다.
 * 모듈이 무엇을 하는지 코어가 알기 시작하면 매니페스토 2번이 깨진다.
 *
 * iframe 을 쓰는 이유: 모듈 화면은 모듈이 만들고 모듈이 배포한다. 우리 번들에 넣으면
 * 모듈을 고칠 때마다 플랫폼을 다시 빌드해야 하고, 모듈마다 다른 프레임워크를 쓸 수도 없다.
 * 주소만 알면 되는 관계로 두는 편이 맞다.
 *
 * 모듈이 다른 포트에 있으므로(예: 11990) 우리가 그 안을 들여다볼 수는 없다. 그래서
 * '연결이 안 된다' 를 프레임 안의 빈 화면 대신 우리가 직접 안내한다.
 */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { Button, Card, Section } from "../components/ui";
import { useModules } from "../lib/hooks";

export function ModuleFrame() {
  const { moduleId = "" } = useParams();
  const { data: modules = [], isLoading } = useModules();
  const [nonce, setNonce] = useState(0);

  const mod = modules.find((m) => m.id === moduleId);

  // 모듈을 바꾸면 프레임을 새로 띄운다. 안 그러면 이전 모듈 화면이 남는다.
  useEffect(() => setNonce((n) => n + 1), [moduleId]);

  if (isLoading) {
    return (
      <Section title="모듈" desc="불러오는 중…">
        <Card>
          <p className="text-[13px] text-muted">모듈 목록을 가져오고 있습니다.</p>
        </Card>
      </Section>
    );
  }

  if (!mod) {
    return (
      <Section title="모듈" desc="등록되지 않은 모듈입니다.">
        <Card>
          <p className="text-[13px] text-muted">
            <b className="font-semibold text-body-strong">{moduleId}</b> 모듈이 플랫폼에
            등록돼 있지 않습니다. 컨테이너가 내려가 있거나 아직 등록 전일 수 있습니다.
          </p>
        </Card>
      </Section>
    );
  }

  if (!mod.endpoint) {
    return (
      <Section title={mod.name} desc="이 모듈은 자기 화면을 갖고 있지 않습니다.">
        <Card>
          <p className="text-[13px] text-muted">
            화면 없이 도는 모듈입니다. 상태는 관리자 → 시스템에서 볼 수 있습니다.
          </p>
        </Card>
      </Section>
    );
  }

  return (
    <Section
      title={mod.name}
      desc={mod.description || "모듈이 직접 띄우는 화면입니다."}
      actions={
        <>
          <span className="text-[12.5px] text-muted">
            {mod.alive ? "연결됨" : "응답 없음"} · {mod.endpoint}
          </span>
          <Button size="sm" onClick={() => setNonce((n) => n + 1)}>
            새로 고침
          </Button>
          <Button size="sm" onClick={() => window.open(mod.endpoint, "_blank", "noopener")}>
            새 창으로
          </Button>
        </>
      }
    >
      {!mod.alive && (
        <p className="mb-4 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-[12.5px] text-[#8a6708]">
          모듈이 heartbeat 를 보내지 않고 있습니다. 컨테이너가 내려갔다면 아래 화면도 비어
          있을 것입니다. <code className="font-mono">docker compose --profile yolo up -d</code>
        </p>
      )}
      <Card padded={false} className="overflow-hidden">
        <iframe
          key={nonce}
          src={mod.endpoint}
          title={mod.name}
          className="block h-[calc(100vh-210px)] min-h-[520px] w-full border-0 bg-canvas"
        />
      </Card>
    </Section>
  );
}
