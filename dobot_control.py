# -*- coding: utf-8 -*-
"""
=====================================================================
 Dobot 2세트 제어 — 통신 코드(warehouse_server.py)에서 불러 쓰는 버전
=====================================================================

원본: 바탕화면 two_sets_full.py
동작 순서 · 좌표 · 안정화 로직(isQueued=False, GetPose 도착 확인, 그리퍼 대기, 스레드 금지)은 그대로입니다.

■ 원본에서 바뀐 것
  1. 실행하면 끝까지 도는 main()  → 신호가 왔을 때 부르는 함수 (run_keycap_conveyor / run_keycap_arms)
  2. 결과를 출력만 함             → 키캡별 성공/실패(True/False)를 돌려줌
  3. 실행마다 연결 → 해제          → 프로그램 시작 때 connect() 한 번, 끝날 때 disconnect()

■ 컨베이어와 로봇팔은 단계별로 따로 (2026-09-18 변경)
  keycap_dispensed 신호 → 두 세트 벨트만 6초 → 컨베이어 결과 보고 (약 7초)
  keycap_reached 신호   → 두 세트 로봇팔이 키캡 4개를 옮기면서, 하나 놓을 때마다 바로 보고
  ※ 전에는 dispensed 때 벨트+로봇팔을 한 번에 다 해서 약 1분 → 플랫폼 단계 제한 60초에 걸려 stage_timeout 남.

■ place_slots 는 최종 트레이가 아니라 "중간 위치"
  트레이가 아직 없어서 벨트에서 집어 옮기는 것까지만 구현. keycapN: done = 중간 위치에 놓았음.
  트레이가 생기면 SETS 의 place_slots 좌표만 트레이 칸으로 바꾸면 되고 통신 코드는 그대로.

■ 키캡 번호 (통신 CNT 기준)
  세트1 COM6: keycap1(EI 선반) = 슬롯1, keycap2(NS 선반) = 슬롯2
  세트2 COM7: keycap3(TF 선반) = 슬롯1, keycap4(PJ 선반) = 슬롯2

■ 실행 환경: DobotRPC 가 설치된 Python 3.9
  단독 테스트(원본처럼 한 번 돌리기):  py -3.9 dobot_control.py
  한 세트만 테스트:                      py -3.9 dobot_control.py set1   (또는 set2)
"""

import asyncio
import json
import os
import time
import math
from DobotRPC import RPCClient, DobotlinkAdapter
from DobotRPC.Utils import loggers as _dobot_loggers
from dotenv import load_dotenv

_dobot_loggers.set_use_file(False)  # 실행할 때마다 폴더에 DobotRPC_날짜.log 가 쌓이지 않게 (화면 출력은 그대로)

load_dotenv()  # 단독 실행(py -3.9 dobot_control.py) 때도 .env 의 ARMS_TAKE_TURNS 를 읽게

NAMESPACE_ARM = "MagicianLite"
NAMESPACE_BOX = "MagicBox"

_MM_TO_PULSE = (360.0 / 1.8 * 5.0 * 16.0) / (3.1415926535898 * 32)


def mm_per_sec_to_pulses(speed_mm_s):
    return int(math.floor(speed_mm_s * _MM_TO_PULSE))


PTP_MODE_JUMP = 0   # 올라갔다 옮겨서 내려감 (낮은 곳 → 낮은 곳)
PTP_MODE_MOVJ = 1   # 관절 이동 — move_to 의 마지막 재시도용
PTP_MODE_MOVL = 2  # 직선 이동 (같은 x·y 에서 바로 위로 들기 / 바로 아래로 내리기)
# ※ 2026-09-18 실측: 로봇이 아직 움직이는 중(도착 3mm 전)에 다음 명령을 보내면 무시됨
#   → wait_until_arrived 가 완전히 멈춘 것까지 확인. 트레이 → 벨트처럼 멀리 갈 때는 JUMP.
CONVEYOR_SPEED = 50       # mm/s, 양쪽 공통
CONVEYOR_RUN_TIME = 6.0   # 초, 양쪽 공통 (2026-09-18 시연 전 8초 → 6초, 멈춘 자리 기준으로 좌표 다시 찍음)
RELEASE_SETTLE_DELAY = 0.5
MOVE_SETTLE_DELAY = 0.5
GRIP_SETTLE_DELAY = 0.5
INTER_KEYCAP_DELAY = 3.0
LIFT_Z_OFFSET = 50.0      # 벨트 위에 떠서 기다리는 높이 (다음에 집을 슬롯 위)
PLACE_LIFT_Z = 30.0       # 트레이에 놓은 뒤 바로 위로 드는 높이 (팔 뻗은 자리라 크게 들면 범위 알람)
RETREAT_RADIUS = 300.0    # 놓은 자리가 이보다 멀면, 벨트로 가기 전에 몸 쪽(중심에서 이 거리)으로 먼저 당김
CALL_TIMEOUT = 5

# 두 팔이 트레이 자리를 번갈아 쓸지 (.env ARMS_TAKE_TURNS, 기본 1)
#   1 = 번갈아 (한 팔이 놓고 빠진 뒤 다른 팔. 트레이 자리가 겹칠 때 — 2026-09-18 시연 기준)
#   0 = 차례 없이 두 팔이 동시에 각자 놓음 (최종 트레이에서 두 팔 놓는 자리가 충분히 떨어져 있으면)
ARMS_TAKE_TURNS = os.environ.get("ARMS_TAKE_TURNS", "1").strip() != "0"

# ---------------------------------------------------------------
# 세트별 설정 — keycaps: (이름, 슬롯 번호, 통신 CNT 의 keycap 번호)
# ---------------------------------------------------------------
def _port(name, default):
    """포트는 .env 로 바꿀 수 있음 (SET1_PORT / SET2_PORT / TRAY_PORT).
    멀티포트 허브로 옮기거나 USB 를 다시 꽂아 번호가 바뀌어도 코드는 그대로."""
    return (os.environ.get(name) or default).strip()


SETS = {
    "set1": {
        "port": _port("SET1_PORT", "COM6"),
        "conveyor_index": 1,
        # 2026-09-18 벨트 6초 기준으로 다시 찍음
        "pickup_slots": {
            1: (156.7, -276.1, 36.1, -60.5),  # 집는 높이. 10-01 에 33.6 → 36.1 (2.5mm 위로)
            2: (159.2, -236.7, 32.4, -56.1),
        },
        # place_slots = 중간 위치 (트레이가 아직 없어서 벨트에서 집어 옮기는 것까지만). 트레이가 생기면 이 좌표만 교체
        "place_slots": {
            1: (-88.4, 362.8, 47.8, 103.6),  # 2026-10-01 새로 찍음 (중심에서 373mm — 팔 한계에 가까움)
            2: (-202.8, 285.3, 43.0, 125.3),  # 380mm 는 팔 한계라 점프 불가 → 350mm 로 당김 (2026-10-01)
        },
        "keycaps": [("1번 키캡(EI)", 1, 1), ("2번 키캡(NS)", 2, 2)],
    },
    "set2": {
        "port": _port("SET2_PORT", "COM7"),
        "conveyor_index": 1,
        "conveyor_reverse": True,  # 2026-09-18: 세트2 벨트는 반대 방향으로 돌림 (속도·시간은 같음)        # 2026-09-18 벨트 6초 기준으로 다시 찍음
        "pickup_slots": {
            1: (145.1, 254.0, 34.5, 60.2),  # z +2mm (2026-09-18 요청)
            2: (136.1, 206.7, 31.5, 56.6),
        },
        "place_slots": {
            1: (-187.1, -262.1, 42.6, -125.6),
            2: (-184.2, -313.2, 39.9, -120.5),
        },
        "keycaps": [("3번 키캡(TF)", 1, 3), ("4번 키캡(PJ)", 2, 4)],
    },
}

# 트레이 로봇팔 — 빈 트레이를 쌓아둔 곳에서 집어 벨트에 올림 (status=assigned 때)
# SETS 와 따로 둠 → 키캡 단계에서는 이 팔이 움직이지 않음.
# 좌표는 read_pose.py 로 찍어서 채울 것 (None 이면 아직 안 찍은 것).
TRAY = {
    "port": _port("TRAY_PORT", "COM8"),
    "pickup_slots": {1: None},   # 빈 트레이 쌓아둔 곳
    "place_slots": {1: None},    # 벨트 위 놓는 곳
    "keycaps": [("빈 트레이", 1, 1)],  # arm_set 가 쓰는 형식 (이름, 슬롯, 번호)
}


def load_poses(cfg, name):
    """pose_<name>.json 이 있으면 그 좌표를 씀 (read_pose.py 로 찍으면 생기는 파일).
    아직 자리가 확정되지 않은 트레이 팔처럼, 코드를 고치지 않고 좌표만 바꾸려고."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"pose_{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return cfg
    for group in ("pickup_slots", "place_slots"):
        for slot, pose in (saved.get(group) or {}).items():
            if pose:
                cfg[group][int(slot)] = tuple(pose)
    return cfg


load_poses(TRAY, "tray")


def log(tag, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{tag:<6}] {msg}")


def is_ok(res):
    """safe_call 결과가 시간초과/오류가 아니면 True."""
    return not (isinstance(res, str) and (res.startswith("TIMEOUT") or res.startswith("ERROR")))


async def safe_call(coro, timeout_sec=CALL_TIMEOUT):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except asyncio.TimeoutError:
        return "TIMEOUT"
    except Exception as e:
        return f"ERROR: {e}"


async def ptp(arm_api, port, pose, is_queued=False, mode=PTP_MODE_JUMP):
    x, y, z, r = pose
    return await safe_call(arm_api.SetPTPCmd(portName=port, ptpMode=mode,
                                              x=x, y=y, z=z, r=r, isQueued=is_queued))


async def wait_until_arrived(arm_api, port, target_pose, tolerance=3.0, timeout=8.0):
    """목표 근처에 오고 + 완전히 멈춘 것까지 확인 (움직이는 중에 다음 명령을 보내면 로봇이 무시함, 2026-09-18 실측)."""
    tx, ty, tz, tr = target_pose
    start = time.time()
    first_pose = last_pose = None
    while time.time() - start < timeout:
        res = await safe_call(arm_api.GetPose(portName=port, isQueued=False))
        if isinstance(res, dict):
            near = all(abs(res.get(k, 9999) - t) < tolerance for k, t in (("x", tx), ("y", ty), ("z", tz)))
            still = last_pose is not None and _same_pose(res, last_pose)
            first_pose = first_pose or res
            last_pose = res
            if near and still:
                await asyncio.sleep(MOVE_SETTLE_DELAY)
                return True, res
            if not near and time.time() - start > 1.5 and _same_pose(res, first_pose):
                return False, res  # 1.5초 동안 꿈쩍도 안 함 → 명령이 무시된 것. 8초 기다리지 않음
        await asyncio.sleep(0.2)
    return False, last_pose


def _same_pose(a, b, eps=0.2):
    return all(abs(a.get(k, 0) - b.get(k, 9999)) < eps for k in ("x", "y", "z"))


async def move_to(arm_api, port, pose, mode=PTP_MODE_JUMP, fallback=True):
    """이동 + 도착 확인. 로봇이 명령을 무시하고 꿈쩍도 안 했으면 한 번 더, 그래도 안 되면 MOVJ 로 재시도.
    fallback=False 면 MOVJ 로 바꾸지 않고 같은 방식으로만 재시도
      (키캡을 들고 가는 구간: MOVJ 는 경로가 달라 엉뚱한 곳에서 놓을 수 있음 — 2026-10-01 실측)
    반환: (명령 결과, 도착 여부, 현재 좌표)"""
    before = await safe_call(arm_api.GetPose(portName=port, isQueued=False))
    res = await ptp(arm_api, port, pose, mode=mode)
    arrived, now = await wait_until_arrived(arm_api, port, pose)
    for retry_mode in ((mode, PTP_MODE_MOVJ) if fallback else (mode, mode)):
        if arrived:
            break
        if not (isinstance(before, dict) and isinstance(now, dict) and _same_pose(before, now, eps=2.0)):
            break  # 움직이긴 했는데 못 감(알람·장애물) → 재시도하면 위험하니 그대로 실패
        log(port, f"  (명령이 무시됨 → 다시 보냄, 방식 {'MOVJ' if retry_mode == PTP_MODE_MOVJ else '같은 방식'})")
        await asyncio.sleep(MOVE_SETTLE_DELAY)
        res = await ptp(arm_api, port, pose, mode=retry_mode)
        arrived, now = await wait_until_arrived(arm_api, port, pose)
    return res, arrived, now


async def gripper(arm_api, port, on, is_queued=False):
    res = await safe_call(arm_api.SetEndEffectorGripper(portName=port, enable=True,
                                                         on=on, isQueued=is_queued))
    await asyncio.sleep(GRIP_SETTLE_DELAY)
    return res


async def conveyor(box_api, port, index, enable, speed_mm_s, is_queued=False):
    speed_pulses = mm_per_sec_to_pulses(speed_mm_s) if enable else 0
    return await safe_call(box_api.SetEMotor(portName=port, index=index, enable=enable,
                                              speed=speed_pulses, isQueued=is_queued))


# =====================================================================
# 세트 1개 실행 로직 — set1, set2 공용 (동작은 원본과 동일, 결과만 모아서 돌려줌)
# =====================================================================

class _NoZone:
    """한 세트만 돌릴 때: 트레이 자리 차례 기다리기 없음."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def new_place_zone():
    """두 세트가 같이 쓰는 트레이 자리. 한 번에 한 팔만 들어감 (기다린 순서대로)."""
    return {"lock": asyncio.Lock(), "blocked_by": None}


async def belt_set(arm_api, box_api, cfg):
    """세트 1개의 벨트만 1회 구동. 반환: {keycap번호: bool}.
    벨트에 도착 센서가 없으므로 "모터 명령이 정상으로 끝났나"로만 판단."""
    port = cfg["port"]
    numbers = [no for _, _, no in cfg["keycaps"]]

    log(port, "그리퍼 오픈 상태로 시작")
    log(port, f"  결과: {await gripper(arm_api, port, on=False)}")

    reverse = cfg.get("conveyor_reverse", False)
    log(port, f"컨베이어 구동 (1회{', 반대 방향' if reverse else ''})")
    started = await conveyor(box_api, port, cfg['conveyor_index'], True,
                             -CONVEYOR_SPEED if reverse else CONVEYOR_SPEED)
    log(port, f"  시작: {started}")
    await asyncio.sleep(CONVEYOR_RUN_TIME)
    stopped = await conveyor(box_api, port, cfg['conveyor_index'], False, 0)
    log(port, f"  정지: {stopped}")

    conveyor_ok = is_ok(started) and is_ok(stopped)
    return {no: conveyor_ok for no in numbers}


async def arm_set(arm_api, cfg, zone=None, on_result=None):
    """세트 1개의 로봇팔: 키캡 2개를 벨트에서 집어 놓는 곳에 놓음. 반환: {keycap번호: bool}
    zone: 두 세트를 같이 돌릴 때 공유하는 트레이 자리 (new_place_zone()). 없으면 차례 없이 혼자 동작.
    on_result(keycap번호, 성공여부): 키캡 하나 끝날 때마다 바로 호출 (그때그때 보고용)."""
    port = cfg["port"]
    results = {}
    lift = cfg.get("final_lift_z", PLACE_LIFT_Z)
    # 집으러 내려가기 전에 반드시 열어 둠 (닫힌 채로 내려가면 키캡을 못 잡음)
    log(port, f"그리퍼 열기: {await gripper(arm_api, port, on=False)}")
    keycaps = cfg["keycaps"]
    for i, (letter, slot_no, keycap_no) in enumerate(keycaps):
        log(port, f"키캡 '{letter}' (슬롯 {slot_no}) → place")

        # 1) 집기 — 각자 자기 벨트라서 두 팔이 동시에 해도 됨
        # 두 번째부터는 이미 그 슬롯 바로 위에 떠 있으므로 곧장 아래로 내림
        moved, pick_arrived, pose = await move_to(arm_api, port, cfg['pickup_slots'][slot_no],
                                                  mode=PTP_MODE_MOVL if i > 0 else PTP_MODE_JUMP)
        log(port, f"  슬롯 이동 명령: {moved}")
        log(port, f"  슬롯 실제 도착: {pick_arrived} (현재 좌표: {pose})")
        if pick_arrived and zone and zone["blocked_by"] == port:
            zone["blocked_by"] = None  # 벨트까지 왔으니 트레이 자리에서 빠진 것 → 막음 해제
            log(port, "  트레이 자리에서 빠진 것 확인 → 다른 팔 다시 들어가도 됨")
        grabbed = await gripper(arm_api, port, on=True)
        log(port, f"  집기: {grabbed}")

        # 2) 놓기 — 트레이 자리는 두 팔이 같이 쓰므로 한 번에 한 팔만 (먼저 집은 팔이 먼저)
        place_arrived, released = False, "SKIP"
        async with (zone["lock"] if zone else _NoZone()):
            if zone and zone["blocked_by"]:
                log(port, f"  !! 트레이 자리에 {zone['blocked_by']} 팔이 멈춰 있음 → 충돌 방지로 놓지 않음 (사람 조치 필요)")
            elif not pick_arrived:
                log(port, "  !! 집는 자리에 못 갔음 → 트레이로 가지 않음 (다른 팔 차례를 막지 않게)")
            else:
                # 키캡을 들고 가는 구간은 JUMP 만 (MOVJ 로 바꾸면 경로가 달라 엉뚱한 곳에서 놓게 됨)
                moved, place_arrived, pose = await move_to(arm_api, port, cfg['place_slots'][slot_no],
                                                           fallback=False)
                log(port, f"  place 이동 명령: {moved}")
                log(port, f"  place 실제 도착: {place_arrived} (현재 좌표: {pose})")
                if not place_arrived:
                    # 못 갔는데 그 자리에서 그리퍼를 열면 키캡이 어딘지 모를 곳에 떨어짐
                    # → 집었던 벨트 자리로 되돌려 놓음 (사람이 찾기 쉽게)
                    log(port, "  !! 놓는 자리에 못 감 → 집었던 벨트 자리로 되돌려 놓음")
                    _, back, pose = await move_to(arm_api, port, cfg['pickup_slots'][slot_no], fallback=False)
                    log(port, f"  되돌리기: {back}" + ("" if back else f" (현재 좌표: {pose} — 이 근처에 떨어짐)"))
                    await gripper(arm_api, port, on=False)
                    released = "SKIP"  # 놓는 자리에 못 놨으므로 실패
                else:
                    released = await gripper(arm_api, port, on=False)
                    log(port, f"  놓기: {released}")
                    await asyncio.sleep(RELEASE_SETTLE_DELAY)

                # 3) 트레이 자리 비우기: 살짝 위로 → 다음에 집을 벨트 위로 점프해서 빠짐.
                #    트레이 자리가 팔을 거의 끝까지 뻗는 곳이라, 거기서 높이 들면 구동 범위 알람이 남 → 조금만 듦
                #    빠진 게 확인돼야 다른 팔 차례
                if place_arrived:  # 못 갔으면 그 자리에 있지도 않으니 들어올릴 것도 없음
                    px, py, pz, pr = cfg['place_slots'][slot_no]
                    up = (px, py, pz + lift, pr)
                    _, lifted, pose = await move_to(arm_api, port, up, mode=PTP_MODE_MOVL)
                    log(port, f"  위로 들기: {lifted}" + ("" if lifted else f" (현재 좌표: {pose})"))
                    # 팔을 많이 뻗은 자리(중심에서 멀리)면 몸 쪽으로 먼저 당김.
                    # 그 상태로 바로 벨트로 점프하면 명령이 무시되고 구동범위 알람이 쌓임 (2026-10-01 실측)
                    reach = math.hypot(px, py)
                    if reach > RETREAT_RADIUS:
                        k = RETREAT_RADIUS / reach
                        pulled = (px * k, py * k, pz + lift, pr)
                        _, ok_pull, pose = await move_to(arm_api, port, pulled, mode=PTP_MODE_MOVL)
                        log(port, f"  몸 쪽으로 당기기: {ok_pull}" + ("" if ok_pull else f" (현재 좌표: {pose})"))
                next_slot = keycaps[i + 1][1] if i + 1 < len(keycaps) else keycaps[0][1]
                nx, ny, nz, nr = cfg['pickup_slots'][next_slot]
                away = (nx, ny, nz + LIFT_Z_OFFSET, nr)
                _, left, pose = await move_to(arm_api, port, away)  # JUMP
                log(port, f"  트레이 자리에서 빠짐: {left}" + ("" if left else f" (현재 좌표: {pose})"))
                if not left and zone:
                    zone["blocked_by"] = port  # 못 빠졌으면 다른 팔이 들어오지 않게 막음

        # 팔이 픽업·플레이스 위치에 실제로 도착했고 그리퍼 명령이 정상이면 성공
        ok = bool(pick_arrived and place_arrived and is_ok(grabbed) and released != "SKIP" and is_ok(released))
        results[keycap_no] = ok
        log(port, f"  keycap{keycap_no} 결과: {'성공' if ok else '실패'}")
        if on_result:
            on_result(keycap_no, ok)  # 끝나는 대로 바로 보고 (4개 다 끝날 때까지 기다리지 않음)

        if not zone and i < len(keycaps) - 1:
            await asyncio.sleep(INTER_KEYCAP_DELAY)  # 한 세트만 돌릴 때는 원래처럼 쉬었다가

    await gripper(arm_api, port, on=False)
    await safe_call(arm_api.SetEndEffectorGripper(portName=port, enable=False, on=False, isQueued=False))

    log(port, "완료")
    return results


async def run_set(arm_api, box_api, cfg):
    """단독 테스트용: 세트 1개 벨트 → 로봇팔 이어서."""
    belt = await belt_set(arm_api, box_api, cfg)
    pack = await arm_set(arm_api, cfg) if all(belt.values()) else {no: False for no in belt}
    return {"conveyor": belt, "pack": pack}


async def all_belts(arm_api, box_api):
    """keycap_dispensed: 두 세트 벨트 동시 구동. {1~4: bool}"""
    merged = {}
    for res in await asyncio.gather(*[belt_set(arm_api, box_api, cfg) for cfg in SETS.values()]):
        merged.update(res)
    return merged


async def all_arms(arm_api, on_result=None):
    """keycap_reached: 두 세트 로봇팔 동시. 트레이 자리는 번갈아 (먼저 집은 팔이 먼저 → 보통 1,3,2,4). {1~4: bool}"""
    zone = new_place_zone() if ARMS_TAKE_TURNS else None
    merged = {}
    for res in await asyncio.gather(*[arm_set(arm_api, cfg, zone, on_result) for cfg in SETS.values()]):
        merged.update(res)
    return merged


async def run_all_sets(arm_api, box_api):
    """단독 테스트용: 두 세트 벨트 → 로봇팔."""
    belt = await all_belts(arm_api, box_api)
    pack = await all_arms(arm_api)
    return {"conveyor": belt, "pack": pack}


# =====================================================================
# 통신 코드가 부르는 함수 — 모두 같은 스레드·같은 이벤트루프에서 실행 (DobotRPC 규칙)
# =====================================================================

_loop = None
_arm_api = None
_box_api = None


def arms():
    """지금 쓰는 팔들 {이름: 설정}. 트레이 팔은 좌표가 찍혀 있을 때만 (pose_tray.json)."""
    out = dict(SETS)
    if TRAY["pickup_slots"][1] and TRAY["place_slots"][1]:
        out["tray"] = TRAY
    return out


def connect(cfgs=None):
    """프로그램 시작 때 한 번. 쓰는 팔 전부 연결 (cfgs 를 주면 그것만)."""
    global _loop, _arm_api, _box_api
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    dobotlink = DobotlinkAdapter(RPCClient(), is_sync=False)
    _arm_api = getattr(dobotlink, NAMESPACE_ARM)
    _box_api = getattr(dobotlink, NAMESPACE_BOX)

    targets = cfgs if cfgs is not None else arms()
    log("INIT", f"로봇팔 연결: {', '.join(targets)}")
    for key, cfg in targets.items():
        res = _loop.run_until_complete(safe_call(_arm_api.ConnectDobot(portName=cfg["port"])))
        log("INIT", f"{cfg['port']} ({key}): {res}")


def keepalive():
    """게이트웨이가 주문을 기다리는 동안 매 폴링마다 호출.
    DobotLink 연결(websocket)은 이벤트루프가 돌 때만 20초 간격 살아있음 신호를 주고받음.
    오래 안 돌리면 연결이 끊겨 다음 명령이 전부 시간초과 → 2026-09-18 19:33 벨트 4개 failed 사례."""
    if _loop is not None:
        _loop.run_until_complete(asyncio.sleep(0.05))


def ensure_connected(cfgs=None):
    """로봇을 움직이기 전에 쓸 팔들이 응답하는지 확인. 하나라도 응답 없으면 새로 연결.
    cfgs 를 주면 그 팔만 확인 (예: 트레이 팔만 쓸 때 키캡 세트는 안 봄)."""
    targets = cfgs if cfgs is not None else arms()
    if _loop is None:
        connect(targets)
        return
    for key, cfg in targets.items():
        res = _loop.run_until_complete(safe_call(_arm_api.GetPose(portName=cfg["port"], isQueued=False), 3))
        if not isinstance(res, dict):
            log("WARN", f"{cfg['port']} ({key}) 응답 없음({res}) → DobotLink 다시 연결")
            connect(targets)
            break
    clear_alarms(targets)


def clear_alarms(cfgs=None):
    """남아 있는 알람(빨간불) 해제. 쌓여 있으면 다음 이동 명령이 통째로 무시됨.
    ※ 관절 범위를 실제로 벗어나 있으면 이것만으로는 안 풀림 → 사람이 옮기거나 홈 복귀 필요."""
    for cfg in (cfgs if cfgs is not None else arms()).values():
        state = _loop.run_until_complete(safe_call(_arm_api.GetAlarmsState(portName=cfg["port"]), 3))
        if isinstance(state, dict) and any(state.get("state") or []):
            _loop.run_until_complete(safe_call(_arm_api.ClearAllAlarmsState(portName=cfg["port"]), 3))
            log(cfg["port"], "남아 있던 알람 해제함")


def disconnect():
    """프로그램 끝날 때 한 번."""
    if _loop is None:
        return
    log("INIT", "연결 해제")
    for cfg in SETS.values():
        _loop.run_until_complete(safe_call(_arm_api.DisconnectDobot(portName=cfg["port"])))


def run_keycap_conveyor(order_id):
    """keycap_dispensed 때 호출. 두 세트 벨트만 돌리고 결과 {1~4: bool} 반환 (약 7초)."""
    ensure_connected(SETS)
    return _loop.run_until_complete(all_belts(_arm_api, _box_api))


def run_tray_arm(order_id):
    """assigned 때 호출. 트레이 팔이 빈 트레이를 집어 벨트에 올림. 성공 여부 반환.
    좌표는 pose_tray.json (read_pose.py tray 로 찍음). 안 찍혀 있으면 움직이지 않고 실패."""
    if not (TRAY["pickup_slots"][1] and TRAY["place_slots"][1]):
        log(TRAY["port"], "트레이 좌표가 아직 없음 (pose_tray.json) → 실패 처리")
        return False
    ensure_connected({"tray": TRAY})
    res = _loop.run_until_complete(arm_set(_arm_api, TRAY))
    return bool(res.get(1))


def run_keycap_arms(order_id, on_result=None):
    """keycap_reached 때 호출. 두 세트 로봇팔로 키캡 4개를 옮김. {1~4: bool} 반환.
    on_result(keycap번호, 성공여부) 를 주면 키캡 하나 끝날 때마다 바로 불러줌 (그때그때 보고)."""
    ensure_connected(SETS)
    return _loop.run_until_complete(all_arms(_arm_api, on_result))


# =====================================================================
# 단독 테스트 — 원본 two_sets_full.py 처럼 한 번 돌리고 결과 출력
# =====================================================================

def run_one_set(set_key):
    """단독 테스트용: 한 세트만 연결해서 컨베이어 + 픽앤플레이스 1회."""
    global _loop, _arm_api, _box_api
    cfg = SETS[set_key]
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    dobotlink = DobotlinkAdapter(RPCClient(), is_sync=False)
    _arm_api = getattr(dobotlink, NAMESPACE_ARM)
    _box_api = getattr(dobotlink, NAMESPACE_BOX)
    log("INIT", f"{cfg['port']} ({set_key}): {_loop.run_until_complete(safe_call(_arm_api.ConnectDobot(portName=cfg['port'])))}")
    try:
        res = _loop.run_until_complete(run_set(_arm_api, _box_api, cfg))
        log("DONE", f"컨베이어: {res['conveyor']}")
        log("DONE", f"로봇팔:   {res['pack']}")
    finally:
        _loop.run_until_complete(safe_call(_arm_api.DisconnectDobot(portName=cfg["port"])))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in SETS:  # py -3.9 dobot_control.py set1 → 세트1만
        run_one_set(sys.argv[1])
        sys.exit()
    connect()
    try:
        conveyor_result = run_keycap_conveyor("standalone_test")
        pack_result = run_keycap_arms("standalone_test")
        log("DONE", f"컨베이어: {conveyor_result}")
        log("DONE", f"로봇팔:   {pack_result}")
    finally:
        disconnect()
