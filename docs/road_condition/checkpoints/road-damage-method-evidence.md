# 도로 손상 형상 분석 근거 체크포인트

## 결론

현재 코어의 큰 흐름인 `노면 후보 추출 → 국부 기준면 적합 → 높이 잔차 → 음·양 변위 분리 →
깊이·면적·체적 산출`은 국내외 연구와 같은 계열이다. 다만 현재 장비의 실측 기준값이 없으므로
문헌의 임계값과 성능 수치를 그대로 정확도 주장이나 자동 승인 기준으로 사용하지 않는다.

분석 산출물에는 `method_basis.profile_id=road-geometry-evidence-v1`을 넣어 어떤 근거가 현재
구현에 적용됐고 어떤 부분이 미검증인지 기계 판독 가능하게 남긴다.

## 근거와 적용 판단

| 근거 | 확인한 내용 | 현재 적용 | 적용하지 않은 내용 |
|---|---|---|---|
| FHWA `FHWA-HRT-13-092` | 포트홀은 개수·면적·최대 깊이, 러팅은 횡단 프로파일의 깊이 측정이 핵심이다. 포트홀 최소 평면치수는 150 mm이고 깊이 구간은 25/50 mm다. | 개수·면적·최대 깊이, 좌우 wheel band 깊이 | FHWA 문서 자체가 표준·규정이 아니므로 공식 등급 명칭과 자동 승인에 사용하지 않음 |
| De Blasiis et al. (2020) | MLS 점군에서 노면만 추출하고 기준 노면 대비 높이 잔차를 구한 뒤 음·양 변위를 따로 분할하고 형상값을 계산한다. 입력 파라미터는 데이터와 경계 조건에 따라 정해야 한다. | robust 기준면, signed residual, 포트홀/범프 분리, 형상값 | 논문의 센서·밀도·파라미터와 보고 정확도 전용 금지 |
| El Issaoui et al. (2021) | MLS 러팅 깊이는 현장 기준 측정과 비교해야 하며 센서 거칠기·정확도 영향을 받는다. | 좌우 깊이 series와 실측 truth 요구 | 현재 depth를 표준 러팅 등급으로 자동 변환하지 않음 |
| 김정주·강병호·최수일 (2017) | 국내 도로에서 LiDAR 노이즈 제거, profile/기울기 변화, 깊이·폭 측정과 복수 센서 확인을 사용했다. | noise guard, 형상 깊이·폭, independent-view evidence | 해당 2D LiDAR 임계값을 RGB-D 점군에 직접 적용하지 않음 |
| 한국건설기술연구원 포트홀 Free 연구 (2019) | 국내 관리용 DB에 영상·위치·길이·깊이·포인트클라우드·체적을 함께 두고 25/50 mm 깊이 구간을 제시했다. | 실제 RGB 점군 + 3D mask + 위치·깊이·면적·체적 UX | 실측 없는 현재 후보를 확정 손상이나 공식 등급으로 표시하지 않음 |

## 알고리즘에 반영한 안전장치

1. 원본 분석 입력은 계속 `cloud_raw_enu.ply`와 raw metadata다.
2. 독립 관측 2회 미만인 점을 transient evidence로 제외한다.
3. robust 기준면에서 `-30 cm~+25 cm` 밖의 지지 셀은 결함 검출에서 제외하되 원본은 삭제하지
   않는다.
4. 결과에는 포트홀의 최대/p95/평균 깊이, 면적, 체적을 함께 기록한다.
5. 러팅은 쉬운 등급보다 좌·우 위치와 측정 깊이를 우선 evidence로 취급한다.
6. 실제 RGB 점군의 mask는 이해를 돕는 검수 evidence이며 확정 라벨이 아니다.

## 임계값 결정

- FHWA와 국내 KICT 자료의 25/50 mm 구간 및 약 0.02㎡ 최소 면적은 `비교 후보`로 26개 청크
  감사에 포함한다.
- 현재 기본 검출값 35 mm/0.035㎡를 즉시 바꾸지 않는다. 포트홀 truth가 없는 상태에서 후보
  수 증가 또는 감소는 정확도 개선의 증거가 아니기 때문이다.
- 26개 청크에서 flat/noise control, coverage, high-object 제외, 후보 형상 분포를 비교하고 합성
  회귀를 통과한 뒤에도 결과는 `실험 임계값`으로 표시한다.

## 출처

- FHWA, *Distress Identification Manual for the Long-Term Pavement Performance Program*,
  FHWA-HRT-13-092 (2014):
  https://www.fhwa.dot.gov/publications/research/infrastructure/pavements/ltpp/13092/001.cfm
- De Blasiis, Di Benedetto, Fiani, *Mobile Laser Scanning Data for the Evaluation of Pavement
  Surface Distress*, Remote Sensing 12(6), 942 (2020): https://doi.org/10.3390/rs12060942
- El Issaoui et al., *Feasibility of Mobile Laser Scanning towards Operational Accurate Road Rut
  Depth Measurements*, Sensors 21(4), 1180 (2021): https://doi.org/10.3390/s21041180
- 김정주·강병호·최수일, *2차원 라이다 기반 3차원 포트홀 검출 시스템*, 디지털콘텐츠학회논문지
  18(5), 989–994 (2017): https://doi.org/10.9728/dcs.2017.18.5.989
- 한국건설기술연구원, *포트홀 Free 도로포장시스템 개발* (2019):
  https://www.codil.or.kr/filebank/original/RK/OTKCRK190381/OTKCRK190381.pdf

## 알려진 제한

- 실측 포트홀·러팅·평탄면 truth, 장착 보정, 노면 ROI가 없다.
- RGB crack/patch/raveling/bleeding 분류는 구현 범위 밖이다.
- `내부 형상 참고값`은 PCI가 아니며 `승차감 참고값`은 IRI가 아니다.
