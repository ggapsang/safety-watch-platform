/** 플러그인이 볼 카메라를 고르는 곳.
 *
 * **왜 코어가 그리는가.** 할당은 코어가 들고 있는 계약 데이터다(`ModuleAssignment`,
 * `/api/modules/{id}/assignments`). 플러그인이 하는 일이 무엇이든 '어느 카메라를
 * 담당하나' 는 똑같은 질문이라, 플러그인마다 따로 만들게 하면 같은 화면을 N 번 만들고
 * 협력사가 만든 것에는 영영 생기지 않는다.
 *
 * **왜 플러그인 탭 안인가.** 관리자 화면 어딘가에 두면 '이 플러그인이 왜 아무것도 안
 * 하지' 를 보고 있는 사람이 거기까지 못 간다. 실제로 그랬다 — 카메라를 다시 만들었더니
 * 할당이 사라졌고, 화면 어디에도 그 사실이 보이지 않아 '박스가 안 그려진다' 로 만났다.
 * 원인이 보이는 자리에 고칠 수단을 둔다.
 *
 * 코어가 플러그인을 아는 것이 아니다. 여기서 다루는 것은 카메라 번호뿐이고, 그 플러그인이
 * 그 영상으로 무엇을 하는지는 여전히 모른다(매니페스토 2번).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../lib/api";
import { useCameras } from "../lib/hooks";
import type { AnalyticsModule } from "../lib/types";
import { Button, Card, CardTitle, Dot, cx } from "./ui";

export function ModuleCameras({ mod }: { mod: AnalyticsModule }) {
  const qc = useQueryClient();
  const { data: cameras = [] } = useCameras();
  const assigned = new Map(mod.assignments.map((a) => [a.camera_id, a]));

  const refresh = () => qc.invalidateQueries({ queryKey: ["modules"] });
  const add = useMutation({
    mutationFn: (cameraId: number) => api.assignCamera(mod.id, cameraId),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (assignmentId: number) => api.unassignCamera(mod.id, assignmentId),
    onSuccess: refresh,
  });
  const busy = add.isPending || remove.isPending;

  const toggle = (cameraId: number) => {
    const hit = assigned.get(cameraId);
    if (hit) remove.mutate(hit.id);
    else add.mutate(cameraId);
  };

  // 할당이 있으면 한 줄로 접는다. 프레임 높이가 넉넉하지 않아서, 늘 펼쳐 두면
  // 정작 봐야 할 플러그인 화면이 밀린다. 비어 있을 때는 그것이 문제이므로 펼쳐 둔다.
  const [open, setOpen] = useState(false);
  const expanded = open || assigned.size === 0;

  if (!expanded) {
    return (
      <div className="mb-3 flex items-center gap-2 rounded-lg border border-hairline bg-surface-soft/50 px-3 py-[7px] text-[12.5px]">
        <span className="text-muted">담당 카메라</span>
        <b className="font-semibold text-body-strong">
          {cameras
            .filter((c) => assigned.has(c.id))
            .map((c) => c.name)
            .join(", ")}
        </b>
        <span className="text-muted-soft">
          {assigned.size}/{cameras.length}대
        </span>
        <div className="flex-1" />
        <Button size="sm" onClick={() => setOpen(true)}>
          바꾸기
        </Button>
      </div>
    );
  }

  return (
    <Card className="mb-4">
      <CardTitle
        title="이 플러그인이 볼 카메라"
        desc="고른 카메라만 처리합니다. 하나도 고르지 않으면 플러그인은 아무것도 하지 않습니다."
      />

      {cameras.length === 0 ? (
        <p className="text-[13px] text-muted">
          등록된 카메라가 없습니다. 관리자 → 카메라 등록에서 먼저 등록하세요.
        </p>
      ) : (
        <>
          {/* 하나도 없으면 그 사실을 분명히 말한다. 이것이 없어서 '왜 안 되지' 가 됐다. */}
          {assigned.size === 0 && (
            <p className="mb-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-[12.5px] text-[#8a6708]">
              담당 카메라가 없어 이 플러그인은 <b>아무것도 처리하지 않습니다.</b> 아래에서
              카메라를 고르세요. 카메라를 지웠다 다시 등록하면 할당이 함께 사라집니다.
            </p>
          )}

          <div className="flex flex-wrap gap-[6px]">
            {cameras.map((c) => {
              const on = assigned.has(c.id);
              return (
                <button
                  key={c.id}
                  type="button"
                  disabled={busy}
                  onClick={() => toggle(c.id)}
                  className={cx(
                    "inline-flex items-center gap-2 rounded-full border px-[11px] py-[5px] text-[12.5px] transition-colors disabled:opacity-50",
                    on
                      ? "border-primary bg-primary-soft text-primary-active"
                      : "border-hairline bg-canvas text-muted hover:border-primary",
                  )}
                >
                  {/* 카메라가 오프라인이면 골라도 영상이 없다. 고르기 전에 보이게 한다. */}
                  <Dot ok={c.status === "normal"} />
                  <span className="font-medium">{c.name}</span>
                  <span className="text-[11.5px] opacity-70">{c.location}</span>
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex items-center gap-2">
            <Button
              size="sm"
              disabled={busy || assigned.size === cameras.length}
              onClick={() =>
                cameras.filter((c) => !assigned.has(c.id)).forEach((c) => add.mutate(c.id))
              }
            >
              전부 고르기
            </Button>
            <Button
              size="sm"
              disabled={busy || assigned.size === 0}
              onClick={() => mod.assignments.forEach((a) => remove.mutate(a.id))}
            >
              전부 빼기
            </Button>
            <span className="text-[11.5px] text-muted-soft">
              {assigned.size}/{cameras.length}대 · 반영까지 최대 20초(플러그인이 일감을 다시 묻는 주기)
            </span>
            <div className="flex-1" />
            {/* 하나도 없을 때는 접지 못하게 한다. 접으면 경고가 사라지고,
              * 그 상태로 두면 '왜 안 되지' 로 돌아간다. */}
            {assigned.size > 0 && (
              <Button size="sm" onClick={() => setOpen(false)}>
                접기
              </Button>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
