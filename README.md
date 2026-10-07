# 창고팀 — 컨베이어·로봇팔 제어 및 통신

스마트 팩토리(COSS 키링) 라인의 **창고 파트**입니다.
플랫폼이 Mobius `cnt_process` 에 올리는 상태를 받아 장비를 움직이고, 결과를 다시 Mobius 에 보고합니다.

자세한 배경·이력은 [`사전정보_총정리.md`](사전정보_총정리.md) (특히 **17장**)에 있습니다.

---

## 담당 범위

| 플랫폼 status | 우리가 하는 일 | 보고하는 컨테이너 |
|---|---|---|
| `assigned` | 트레이 로봇팔이 빈 트레이 투입 | `cnt_robot_arm/cnt_tray_robot_arm` |
| `keycap_dispensed` | 키캡 벨트 2대 구동 (6초) | `cnt_conveyor/cnt_keycap_conveyor` |
| `keycap_reached` | 키캡 로봇팔 2대가 키캡 4개 적재 | `cnt_robot_arm/cnt_keycap_robot_arm` ×4 |
| `keycap_packed` | 트레이 벨트 section1 (장비 없음 → 가짜 done) | `cnt_conveyor/cnt_tray_conveyor` |

보드 로봇팔·보드 배출·section2 는 다른 팀 담당입니다. **`tray_reached` 를 만드는 것까지**가 우리 몫입니다.

---

## 구조

```
Mobius cnt_process
   │
   ├─ (A) pc_gateway.py     1초마다 직접 읽기 (HTTP 폴링)
   └─ (B) mqtt_gateway.py   MQTT 구독  ←  mqtt_relay.py 가 VM 에서 중계
   │
   ▼
warehouse_server.py   판단 — status 별 처리, fault, 보고 형식 (A·B 공용)
   ▼
dobot_control.py      로봇팔 3대 + 컨베이어 2대 실제 제어
   ▼
보고 — mobius.py 로 Mobius 컨테이너에 HTTP 로 CIN 생성
```

- **게이트웨이는 A 또는 B 중 하나만** 실행합니다. 둘 다 켜면 보고가 두 번 올라갑니다.
- 보고는 MQTT 가 아니라 **HTTP** 입니다. 실패를 감지해 재시도할 수 있고, Mobius 에 기록이 남아 추적이 됩니다.

### MQTT 를 쓰는 이유

학교 Mobius 는 MQTT 포트(1883·8883)가 외부에 막혀 있어, PC 가 Mobius 에 MQTT 로 직접 붙을 수 없습니다.
그래서 `mqtt_relay.py` 가 VM 에서 Mobius 를 읽어 브로커(mosquitto)로 중계하고, 현장 PC 는 그 토픽을 구독합니다.

---

## 파일

| 파일 | 역할 |
|---|---|
| `warehouse_server.py` | **판단·보고 핵심.** status 별 처리, fault, 재시도 |
| `dobot_control.py` | 로봇팔·컨베이어 제어. 좌표와 속도 설정이 여기 |
| `mobius.py` | Mobius 읽기·쓰기 공통 함수 |
| `pc_gateway.py` | 상태를 HTTP 폴링으로 받는 게이트웨이 |
| `mqtt_gateway.py` | 상태를 MQTT 로 받는 게이트웨이 |
| `mqtt_relay.py` | VM 에서 Mobius → MQTT 중계 (판단·보고 안 함) |
| `read_pose.py` | 좌표 찍기 · 홈 복귀 · 알람 해제 · 벨트 구동 · 부분 시험 |
| `mqtt_fake_order.py` | 가짜 주문으로 자체 시험 (Mobius 에 아무것도 올리지 않음) |
| `check_order.py` | 주문 하나의 팀별 흐름을 시간순으로 보기 |
| `check_vm_log.py` | VM 게이트웨이 상태·로그 보기 |
| `gcloud_deploy.sh` | VM 에 파일 올리고 게이트웨이·중계 재시작 |
| `pose_tray.json` | 트레이 로봇팔 좌표. **코드 수정 없이 이 파일만 바꾸면 됨** |
| `.env.example` | 설정 항목 설명. 실제 값은 `.env` (공유 금지) |

---

## 준비

1. **Python 3.9** (DobotRPC 가 3.9 에서 동작)
   ```
   py -3.9 -m pip install requests python-dotenv flask paho-mqtt DobotRPC
   ```
2. `.env.example` 을 복사해 `.env` 로 만들고 값을 채웁니다. 인증값은 팀 담당자에게 받습니다.
3. 실제 로봇을 쓸 때는 **DobotLink** 를 켜고 USB 를 연결합니다.

### 설정 (.env)

| 항목 | 뜻 |
|---|---|
| `HARDWARE` | `fake` = 통신만 / `dobot` = 실제 로봇 |
| `PROCESS_PATH` | 읽을 cnt_process 경로 (합동 테스트 때 `cnt_process`) |
| `REPORT_ROOT` | 비우면 진짜 컨테이너로 보고 |
| `REACT_ASSIGNED` | 1 이면 트레이 로봇팔 동작 |
| `DRY_RUN` | 1 이면 보고를 올리지 않고 화면에만 출력 (자체 시험용) |
| `MANUAL_FIX_DONE` | fault 해제를 "사람이 조치함" 으로 보고 (기본 1) |
| `FORCE_FAIL` | 테스트용. 일부러 failed 로 보고 (주문당 한 번) |
| `ARMS_TAKE_TURNS` | 두 팔 번갈아(1) / 동시(0) |
| `SET1_PORT`·`SET2_PORT`·`TRAY_PORT` | 로봇 COM 포트 |
| `MQTT_HOST`·`MQTT_PORT`·`MQTT_USER`·`MQTT_PASS`·`MQTT_TOPIC` | 브로커 접속 정보 |
| `FAKE_TRAY_CONVEYOR` 등 | 아직 없는 장비 단계에 가짜 done 보고 |
| `CHECK_GRIP_IR`·`CHECK_BOARD_COLOR` | 센서 확인 (연결 전까지 0) |

---

## 실행

### 로봇 준비 — 전원을 켠 뒤 반드시

```
py -3.9 read_pose.py set1 home     # set2 / tray 도 동일
```

**전원을 새로 켜거나 USB 를 다시 꽂으면, 홈 복귀를 한 번 하기 전까지 이동 명령을 전부 무시합니다.**
LED 가 빨간 채로 명령만 받고 움직이지 않습니다.

```
py -3.9 read_pose.py set1 clear    # 알람만 해제 (로봇 안 움직임)
py -3.9 read_pose.py set1          # 좌표 찍기 (손으로 옮기고 Enter)
py -3.9 read_pose.py tray          # 트레이 팔 좌표 → pose_tray.json 생성
```

### 부분 시험

```
py -3.9 read_pose.py set1 belt     # 벨트만 6초 (back = 반대 방향)
py -3.9 read_pose.py set1 one 1    # 키캡 1개만 집고 놓기
py -3.9 dobot_control.py set1      # 한 세트 전체 (벨트 + 키캡 2개)
```

### 자체 시험 — 플랫폼 없이, Mobius 에 아무것도 올리지 않음

`.env` 에 `DRY_RUN=1` 을 두고 게이트웨이를 켠 뒤:

```
py -3.9 mqtt_fake_order.py          # 정상 흐름
py -3.9 mqtt_fake_order.py fault    # fault 걸었다 푸는 흐름
```

가짜 주문은 우리 MQTT 토픽에만 흐르고, 보고는 화면에만 찍힙니다. 다른 팀에 영향이 없습니다.

### 운영

```
py -3.9 pc_gateway.py        # 또는 py -3.9 mqtt_gateway.py (둘 중 하나만)
```

VM 에 올려서 24시간 켜두기 (PowerShell):

```
gcloud.cmd compute scp mobius.py warehouse_server.py pc_gateway.py mqtt_relay.py .env gcloud_deploy.sh warehouse-webhook:/home/ko050/ --zone us-central1-a
gcloud.cmd compute ssh warehouse-webhook --zone us-central1-a --quiet --command "bash ~/gcloud_deploy.sh"
```

### 확인 (읽기 전용)

```
py -3.9 check_order.py [주문번호]   # 생략하면 최근 주문
py -3.9 check_vm_log.py -f          # VM 로그 실시간
```

---

## 로봇 동작 규칙 (실측으로 정한 것)

| 규칙 | 이유 |
|---|---|
| 수평 이동은 JUMP, 같은 x·y 의 수직 이동은 직선(MOVL) | 떠 있는 상태에서 JUMP 로 내려가면 명령이 무시됨 |
| 물건을 든 상태에서 MOVJ 금지 | 경로가 달라 엉뚱한 곳에서 놓게 됨 |
| 도착 판정은 "3mm 이내 + 완전히 멈춤" 후 0.5초 대기 | 움직이는 중에 보낸 명령은 무시됨 |
| 명령이 무시되면 재전송 | |
| 로봇팔 단계 시작 때 그리퍼 먼저 열기 | 닫힌 채 내려가면 못 잡음 |
| 중심에서 300mm 넘는 곳에 놓았으면, 빠지기 전에 몸 쪽으로 당김 | 뻗은 자세에서는 들어올리기·점프가 실패 |
| 놓는 자리에 못 갔으면 그리퍼를 열지 않고 집었던 자리로 되돌림 | 키캡을 모르는 곳에 떨어뜨리지 않게 |

**도달 범위** — 베이스 중심에서 ~320mm 안정적 / ~363mm 불안정 / **380mm 이상 실패**.
작업점은 **반경 330mm 이내**에 두는 것을 권장합니다.

### 두 팔이 같은 트레이를 쓸 때

`ARMS_TAKE_TURNS=1`(기본) 이면 한 번에 한 팔만 트레이 영역에 들어갑니다 (보통 키캡 1→3→2→4).
한 팔이 트레이 자리에서 못 빠지면 다른 팔은 들어가지 않고 그 키캡을 `failed` 로 보고합니다.

---

## fault 처리

- 주문에 `fault` 가 있으면 `[정지]` 만 찍고 아무 동작도 하지 않습니다. 해제되면 그 단계부터 이어갑니다.
- **플랫폼은 fault 중에 받은 컨베이어·로봇암 보고를 버립니다.** 그래서 해제 후에는 키캡 **4건을 전부** 다시 보냅니다.
- 키캡 컨베이어 보고에 `section`(`01`/`02`)을 붙여 실패한 벨트 구간을 알립니다 (keycap1·2=01, 3·4=02).

### 감지할 수 있는 실패 / 없는 실패

| 상황 | 감지 |
|---|---|
| 로봇팔이 목표 위치에 못 감 | ✅ GetPose 로 확인 |
| 명령 시간초과·오류, 그리퍼 오류 | ✅ |
| 벨트 모터 명령 실패 | ✅ |
| **벨트에 키캡이 끼임** | ❌ 도착 센서 없음 |
| **로봇팔이 키캡을 떨어뜨림** | ❌ 센서 없음 |

---

## 시간

| 구간 | 실측 |
|---|---|
| 벨트 구동 + 보고 | 7~9초 |
| 키캡 1개 집고 놓기 | 11~13초 |
| 키캡 4개 적재 | 45~58초 |
| 트레이 1개 투입 | 10~11초 |

플랫폼은 **단계마다 60초 제한**을 둡니다. 키캡 적재가 제한에 가까우므로 동선이 길어지면 초과합니다.

---

## 주의

- **`.env` 는 공유하지 않습니다.** 대화·코드·공유 파일에 인증값을 옮기지 않습니다.
- 게이트웨이는 **한 번에 하나만** 실행합니다 (VM 과 PC 동시 실행 금지).
- 게이트웨이는 켤 때 이미 올라와 있던 status 를 "기준" 으로만 기록합니다. 옛 주문에 로봇이 갑자기 움직이지 않게 하려는 장치이며, `assigned` 부터 보려면 게이트웨이를 먼저 켜고 주문을 넣습니다.
- 문제가 생기면 바로 리셋·재시작하지 말고, 시각·주문번호·증상을 플랫폼팀에 먼저 알립니다 (그 순간 상태가 재현 자료).

---

## 남은 일

1. 트레이 로봇팔 실제 좌표 (적재 위치·벨트 위치 확정 후 `read_pose.py tray`)
2. 트레이를 키캡 받는 위치로 옮기는 구간 — 플랫폼 단계에 없어 방식 결정 필요
3. 키캡 4개 적재 시간 단축 (배치로 동선 줄이기)
4. 로봇팔 fault 해제 후 진행 확인 (4건 재보고로 고쳤으나 실제 검증 전)
5. 전시 환경 전환 — Mobius 주소 `192.168.0.100`, 브로커도 그 서버, 로컬 PC 구동
6. 센서 도입 검토 (벨트 도착 감지)
