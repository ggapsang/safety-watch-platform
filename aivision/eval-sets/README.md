# 평가 세트

이미지 판별률 평가(학습·평가 플러그인, 카이스트 시험 항목 5)에 쓰는 세트를 보관한다.
이미지는 **Git LFS** 로 올라가 있다(`.gitattributes`). 받을 때 `git lfs pull` 이 필요하다.

| 세트 | 이미지 | 정답 | 만든 곳 |
|---|---|---|---|
| `TEST_DATASET_I` | 100장 (2560×1440 jpg) | YOLO txt, `classes.txt` = AMR, Human | 데이터셋 `amr-human` 에서 '사용' 으로 고른 100장 |

세트 하나의 모양 — 학습·평가 플러그인이 그대로 읽는다.

```
<세트>/images/*.jpg      이미지
<세트>/labels/*.txt      이미지와 같은 이름. 줄마다 'cls cx cy w h' (0~1)
<세트>/classes.txt       클래스 이름 (줄 번호가 cls)
```

## 돌리기

시스템이 읽는 자리는 `.env` 의 `EVAL_PATH`(예 `C:/aivision-eval`)다. 여기 있는 세트를 그 폴더로
복사하면 화면의 '이미지 판별률 평가' 탭에 나타난다.

```
docker compose exec mod-trainer python evaluate.py /eval/TEST_DATASET_I
```

AMR 박스는 Roboflow 정답이고, Human 박스는 모델(COCO YOLO11m) 자동 라벨을 사람이 보고 고른
것이다. 정답을 고치면 이 폴더도 같이 갱신한다.
