import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { useLiveFeed } from "./lib/hooks";
import { Cameras } from "./pages/Cameras";
import { Dashboard } from "./pages/Dashboard";
import { Events } from "./pages/Events";
import { ModuleFrame } from "./pages/ModuleFrame";

// MQTT 로그와 관리자 화면은 상시 보는 화면이 아니다. mqtt.js 가 번들에서 가장 무거워
// 첫 화면 로딩까지 붙잡아 둘 이유가 없다.
const MqttLog = lazy(() => import("./pages/MqttLog").then((m) => ({ default: m.MqttLog })));
const Admin = lazy(() => import("./pages/Admin").then((m) => ({ default: m.Admin })));
const Stats = lazy(() => import("./pages/Stats").then((m) => ({ default: m.Stats })));

const Loading = () => (
  <div className="py-24 text-center text-[13px] text-muted-soft">불러오는 중…</div>
);

export default function App() {
  // 서버 푸시 구독은 앱 최상단에서 한 번만 한다(화면을 옮겨도 연결이 유지된다).
  const live = useLiveFeed();

  return (
    <Shell live={live}>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="/events" element={<Events />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/mqtt" element={<MqttLog />} />
          <Route path="/admin" element={<Admin />} />
          {/* 화면을 가진 모듈이면 무엇이든 여기로 들어온다. 코어는 안을 모른다. */}
          <Route path="/modules/:moduleId" element={<ModuleFrame />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}
