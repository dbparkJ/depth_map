# Stage 06 checkpoint — advanced geometry screening

## 기준과 결정

- Branch: `feat/road-condition-stage-06-geometry`
- Base SHA: `51945868efe58ec3d856f294a6f067de8a753810`
- 우선순위: 단차·맨홀 후보, 횡단경사, 종단경사, 물고임 screening을 모두 독립 구현
- 기본값: 네 feature flag 모두 비활성화하여 기존 출력 호환
- 맨홀 자산 DB: unknown
- 실제 배수구 위치: unknown
- roughness 비교 장비: unknown
- 모든 threshold: experimental, manual review

## 구현

- residual gradient 기반 단차/맨홀 geometry candidate와 높이·방향별 접근 높이·경사·edge metric
- reference surface 기반 도로/차로별 횡단경사 통계와 crown offset
- trajectory Z를 복원한 reference surface 종단경사 통계와 기존 roughness proxy 명칭 보호
- priority-flood DEM fill 기반 잠재 저류 깊이·면적·체적 screening proxy
- detector별 feature flag, 상태, 오류 격리
- 기존 포트홀·러팅·범프 및 내부 형상 점수 계산 분리
- API capabilities와 설정 예시

## 검증 결과

- 정적 검사: Python compileall, 웹 `node --check`, Compose service/config 검사 통과
- 단위/회귀: `148 passed, 1 warning` (`127.73s`, 최종 변경 후 재실행)
- 네 flag가 독립이며 기본 비활성임을 확인
- 합성 원형 단차에서 높이·방향별 접근 높이·경사·edge metric 확인
- 알려진 평면에서 횡단 2%, 종단 1% 복원
- 합성 폐쇄 함몰에서 잠재 저류 깊이·면적·체적 확인
- detector 강제 실패 시 기존 포트홀 record 불변
- 20m/14,000점 전체 기능 benchmark: `0.54s`, 최대 RSS `74,376KB`
- benchmark profile: 횡단경사 `1.799%`, 종단경사 `0.318%`, roughness proxy `0.00357m`
- Docker: build, 기존 synthetic mixed smoke, 전체 기능 API smoke 통과
- 짧은 실데이터: 승인된 장치/asset/drain/비교 장비 입력이 없어 미실행

## 알려진 제한

- 형상만으로 맨홀 자산을 확정하지 않으며 RGB/asset inventory 확인이 필요하다.
- 교차로 exclusion 의미를 가진 ROI schema가 아직 없어 횡단경사 검토가 필요하다.
- drain 위치와 강우/배수 모델이 없어 drainage capacity와 침수 예측을 계산하지 않는다.
- roughness proxy를 표준 IRI로 환산하지 않는다.
