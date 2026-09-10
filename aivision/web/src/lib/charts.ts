/** Chart.js 공통 설정 — 시안의 차트 질감(색·눈금·툴팁)을 그대로 옮겨 둔 것.
 *
 * 통계 화면(pages/Stats.tsx)이 쓴다. 옵션을 화면 쪽에 두지 않는 이유는 질감을 한곳에서
 * 정하기 위해서다 — 화면마다 색과 눈금을 따로 적으면 같은 제품으로 보이지 않는다.
 * Stats 는 lazy 로드라 chart.js 가 첫 화면 번들에 들어가지는 않는다.
 */
import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
  type ChartOptions,
} from "chart.js";

Chart.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Filler,
  Tooltip,
  Legend,
);

Chart.defaults.font.family =
  '"Pretendard", -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif';
Chart.defaults.color = "#6c6a64";

const grid = { color: "rgba(230,223,216,.8)", drawTicks: false };

export const lineOptions: ChartOptions<"line"> = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  plugins: {
    legend: { display: false },
    tooltip: { backgroundColor: "#181715", padding: 10, cornerRadius: 8, displayColors: false },
  },
  scales: {
    x: { grid: { ...grid, display: false }, border: { display: false } },
    y: {
      beginAtZero: true,
      // 이벤트 건수는 정수다. 0.5 눈금이 뜨면 읽는 사람이 헷갈린다.
      ticks: { precision: 0 },
      grid,
      border: { display: false },
    },
  },
};

export const barOptions: ChartOptions<"bar"> = {
  responsive: true,
  maintainAspectRatio: false,
  indexAxis: "y",
  plugins: {
    legend: { display: false },
    tooltip: { backgroundColor: "#181715", padding: 10, cornerRadius: 8, displayColors: false },
  },
  scales: {
    x: { beginAtZero: true, ticks: { precision: 0 }, grid, border: { display: false } },
    y: { grid: { display: false }, border: { display: false } },
  },
};

export const doughnutOptions: ChartOptions<"doughnut"> = {
  responsive: true,
  maintainAspectRatio: false,
  cutout: "62%",
  plugins: {
    legend: {
      position: "bottom",
      labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", padding: 16 },
    },
    tooltip: { backgroundColor: "#181715", padding: 10, cornerRadius: 8 },
  },
};

/** 선 아래 그러데이션 — 시안의 라인 차트 질감 */
export const areaFill = (ctx: { chart: Chart }) => {
  const { ctx: c, chartArea } = ctx.chart;
  if (!chartArea) return "rgba(204,120,92,0.25)";
  const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
  g.addColorStop(0, "rgba(204,120,92,0.28)");
  g.addColorStop(1, "rgba(204,120,92,0)");
  return g;
};
