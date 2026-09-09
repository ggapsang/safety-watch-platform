"""학습·평가·내보내기 실행 — 별도 프로세스로 돌린다.

왜 프로세스를 나누는가
  · 학습은 몇 시간을 돌고, 죽을 때 프로세스를 통째로 데려간다(CUDA OOM, segfault).
    같은 프로세스에서 돌리면 추론과 API 가 함께 죽는다.
  · 취소가 확실하다. 스레드는 중간에 멈출 방법이 없지만 프로세스는 죽이면 끝난다.
  · YOLOv7 의 train.py 는 argparse 로 도는 스크립트다. 함수로 뜯어 부르는 것보다
    있는 그대로 부르는 편이 원본을 갈아 끼울 때 편하다.

한 번에 하나만 돌린다. GPU 가 하나이므로 둘을 돌리면 둘 다 느려지거나 OOM 이 난다.

진행 상태는 run 폴더에 남긴다(`run.json`). 컨테이너가 재시작해도 목록이 살아 있어야 한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VENDOR = Path(__file__).resolve().parent / "vendor" / "yolov7"
LOG_TAIL = 400                       # 화면에 보여 줄 최근 로그 줄 수

# tqdm 진행 줄에서 epoch 를 뽑는다. "  12/59     3.2G   0.0421 ..." 모양.
_EPOCH = re.compile(r"^\s*(\d+)/(\d+)\s")
# 학습 끝에 나오는 결과 줄. "Results saved to ..." 로 마무리된다.
_DONE = re.compile(r"Results saved to")


@dataclass
class Run:
    """학습 한 번. 폴더 하나가 곧 한 번의 학습이다."""

    name: str
    status: str = "running"          # running | done | failed | canceled
    started_at: str = ""
    finished_at: str = ""
    epoch: int = 0
    epochs: int = 0
    args: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {**self.__dict__}


class Trainer:
    """학습 프로세스 하나를 소유한다. 동시에 하나만."""

    def __init__(self, root: Path) -> None:
        self.root = root                     # 학습 산출물이 쌓이는 곳 (볼륨)
        self.root.mkdir(parents=True, exist_ok=True)
        self._proc: subprocess.Popen | None = None
        self._run: Run | None = None
        self._lines: list[str] = []
        self._lock = threading.Lock()

    # ── 상태 ────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        run = self._run.to_dict() if self._run else None
        return {"busy": self.busy, "run": run, "root": str(self.root)}

    def log_tail(self, limit: int = LOG_TAIL) -> list[str]:
        with self._lock:
            return self._lines[-limit:]

    def runs(self) -> list[dict]:
        """산출물 폴더를 훑어 학습 목록을 만든다. DB 를 두지 않는 이유는
        폴더가 이미 진실의 원천이고, 사람이 폴더를 지우면 목록에서도 사라져야 하기 때문이다."""
        out = []
        for d in sorted(self.root.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            meta = d / "run.json"
            info = {"name": d.name, "status": "unknown"}
            if meta.is_file():
                try:
                    info.update(json.loads(meta.read_text(encoding="utf-8")))
                except (ValueError, OSError):
                    pass
            weights = d / "weights"
            info["weights"] = sorted(p.name for p in weights.glob("*.pt")) if weights.is_dir() else []
            info["onnx"] = sorted(p.name for p in weights.glob("*.onnx")) if weights.is_dir() else []
            out.append(info)
        return out

    # ── 학습 ────────────────────────────────────────────────────────

    def start(self, *, name: str, data: str, weights: str, hyp: str, cfg: str,
              epochs: int, batch: int, imgsz: int, device: str,
              extra: list[str] | None = None) -> Run:
        if self.busy:
            raise RuntimeError("이미 학습이 돌고 있습니다. 하나씩만 돌립니다(GPU 가 하나입니다).")

        data_path = Path(data)
        if not data_path.is_file():
            raise FileNotFoundError(f"데이터셋 yaml 을 찾을 수 없습니다: {data}")

        run_dir = self.root / name
        run_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "python", "train.py",
            "--data", str(data_path),
            "--hyp", hyp,
            "--cfg", cfg,
            "--epochs", str(epochs),
            "--batch-size", str(batch),
            "--img-size", str(imgsz), str(imgsz),
            "--device", device,
            "--project", str(self.root),
            "--name", name,
            "--exist-ok",
            # Windows 워커에 이미지 캐시가 통째로 복제되어 커밋 메모리가 터진다.
            # (사내 기록: WinError 1455) 그래서 --cache-images 는 절대 넣지 않는다.
            "--workers", "4",
        ]
        if weights:
            cmd += ["--weights", weights]
        else:
            cmd += ["--weights", ""]        # 처음부터 학습(권장하지 않음)
        if extra:
            cmd += extra

        run = Run(name=name, epochs=epochs,
                  started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  args={"data": str(data_path), "weights": weights, "hyp": hyp, "cfg": cfg,
                        "epochs": epochs, "batch": batch, "imgsz": imgsz, "device": device})
        self._run = run
        with self._lock:
            self._lines = [f"$ {' '.join(cmd)}", ""]
        self._save(run)

        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self._proc = subprocess.Popen(cmd, cwd=str(VENDOR), env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, errors="replace", bufsize=1)
        threading.Thread(target=self._pump, args=(run,), daemon=True,
                         name=f"train-{name}").start()
        log.info("학습 시작: %s (%d epoch)", name, epochs)
        return run

    def cancel(self) -> bool:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        # 먼저 얌전히, 안 되면 확실히. 학습 프로세스는 자식(데이터로더 워커)을 데리고 있다.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if self._run:
            self._run.status = "canceled"
            self._run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save(self._run)
        log.info("학습 취소: %s", self._run.name if self._run else "?")
        return True

    def _pump(self, run: Run) -> None:
        """자식 출력을 읽어 로그와 진행률로 옮긴다."""
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            with self._lock:
                self._lines.append(line)
                if len(self._lines) > 4000:          # 메모리를 지킨다
                    del self._lines[:1000]
            m = _EPOCH.match(line)
            if m:
                try:
                    run.epoch = int(m.group(1)) + 1
                    run.epochs = int(m.group(2)) + 1
                except ValueError:
                    pass
            if _DONE.search(line):
                run.epoch = run.epochs

        code = proc.wait()
        if run.status != "canceled":
            run.status = "done" if code == 0 else "failed"
            if code != 0:
                run.error = f"종료 코드 {code}"
                tail = " / ".join(self.log_tail(6))
                log.warning("학습 실패: %s (%s) %s", run.name, run.error, tail[:400])
        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save(run)
        self._write_log(run)
        log.info("학습 종료: %s -> %s", run.name, run.status)

    def _save(self, run: Run) -> None:
        try:
            (self.root / run.name / "run.json").write_text(
                json.dumps(run.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            log.warning("run.json 저장 실패: %s", exc)

    def _write_log(self, run: Run) -> None:
        """끝난 학습의 로그를 파일로 남긴다. 화면 버퍼는 재시작하면 사라진다."""
        try:
            (self.root / run.name / "train.log").write_text(
                "\n".join(self.log_tail(4000)), encoding="utf-8")
        except OSError as exc:
            log.warning("train.log 저장 실패: %s", exc)


# ────────────────────────────────────────────────────────── 내보내기

def export_onnx(weights: Path, imgsz: int = 640, timeout: float = 900.0) -> Path:
    """best.pt -> best.onnx.

    `--grid` 로 decode 를 그래프 안에 넣는다. 그래야 출력이 바로 [1, N, 5+nc] 가 되어
    후처리가 단순해진다(우리 inference.py 가 기대하는 모양이다).
    `--simplify` 는 onnx-simplifier 가 있으면 쓰고 없으면 건너뛴다.
    """
    if not weights.is_file():
        raise FileNotFoundError(f"가중치를 찾을 수 없습니다: {weights}")

    cmd = ["python", "export.py", "--weights", str(weights),
           "--img-size", str(imgsz), str(imgsz),
           "--batch-size", "1", "--grid", "--simplify", "--device", "cpu"]
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, cwd=str(VENDOR), env=env, capture_output=True,
                          text=True, errors="replace", timeout=timeout)
    out = weights.with_suffix(".onnx")
    if proc.returncode != 0 or not out.is_file():
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-800:]
        raise RuntimeError(f"ONNX 내보내기 실패 (코드 {proc.returncode}): {tail.strip()[:600]}")
    log.info("ONNX 내보내기 완료: %s (%.1fMB)", out.name, out.stat().st_size / 1024 / 1024)
    return out


def publish(onnx: Path, models_dir: Path, name: str = "") -> Path:
    """내보낸 모델을 추론이 쓰는 자리로 복사한다.

    학습 산출물 폴더에 그대로 두고 가리키게 할 수도 있지만, 그러면 학습 폴더를 지울 때
    돌고 있던 추론이 죽는다. 쓰기로 한 모델은 따로 복사해 둔다 — 이벤트 클립을 세그먼트에서
    복사해 오는 것과 같은 이유다.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    dest = models_dir / (name or onnx.name)
    shutil.copy2(onnx, dest)
    log.info("모델 배치: %s -> %s", onnx.name, dest)
    return dest


def list_models(models_dir: Path) -> list[dict]:
    if not models_dir.is_dir():
        return []
    out = []
    for p in sorted(models_dir.glob("*.onnx")):
        st = p.stat()
        out.append({"name": p.name, "size_mb": round(st.st_size / 1024 / 1024, 1),
                    "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                    .isoformat(timespec="seconds")})
    return out


def find_datasets(roots: list[Path]) -> list[dict]:
    """데이터셋 yaml 을 찾아 준다.

    라벨링·전처리는 이 모듈의 일이 아니다(별도 도구로 만든다). 여기서는 준비된 데이터셋을
    가리키기만 하므로, 볼륨에서 yaml 을 찾아 목록으로 보여 주는 것으로 충분하다.
    """
    seen: dict[str, dict] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.yaml")):
            text = ""
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            # 데이터셋 yaml 은 train/val 과 nc 를 갖는다. 하이퍼파라미터 yaml 과 구분된다.
            if "train:" not in text or "nc:" not in text:
                continue
            nc = re.search(r"^nc:\s*(\d+)", text, re.M)
            names = re.search(r"^names:\s*(.+)$", text, re.M)
            seen[str(p)] = {"path": str(p), "name": p.name,
                            "nc": int(nc.group(1)) if nc else None,
                            "names": (names.group(1).strip() if names else "")[:120]}
    return list(seen.values())


def wait_free(trainer: Trainer, timeout: float = 5.0) -> bool:
    """학습이 끝나기를 잠깐 기다린다. 테스트에서 쓴다."""
    end = time.time() + timeout
    while time.time() < end:
        if not trainer.busy:
            return True
        time.sleep(0.1)
    return False
