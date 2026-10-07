# -*- coding: utf-8 -*-
"""
주문 1건의 전체 흐름 확인 — 팀별 Mobius 기록을 모아 시간순으로 보여줌 (읽기 전용: 아무것도 쓰지 않음)

실행:  py -3.9 check_order.py               → 가장 최근 주문
       py -3.9 check_order.py ord_b237e0ab  → 그 주문

※ cnt_process 는 최근 20건 정도만 보관하므로 테스트 직후에 확인하는 게 좋음
※ 다른 팀 서버 안에서 벌어진 일(서버 꺼짐·에러)은 안 보임 — "보고가 안 올라왔다"까지만 알 수 있음
"""

import sys
from datetime import datetime

import mobius

PROCESS = "cnt_process"

# 컨테이너 → 누가 올리는지
REPORTS = {
    "cnt_warehouse/cnt_keycap_dispense": "키캡 카트리지",
    "cnt_robot_arm/cnt_tray_robot_arm": "트레이 로봇팔",
    "cnt_conveyor/cnt_keycap_conveyor": "창고(우리)",
    "cnt_robot_arm/cnt_keycap_robot_arm": "창고(우리)",
    "cnt_conveyor/cnt_tray_conveyor": "창고(우리)",
    "cnt_robot_arm/cnt_board_robot_arm": "보드 로봇암",
    "cnt_warehouse/cnt_board_dispense": "보드",
    "cnt_agv": "AGV",
}

# status → 다음에 움직여야 하는 쪽 (명세 status 표 기준)
NEXT_TURN = {
    "assigned": "키캡 카트리지 — 배출 4건 (cnt_keycap_dispense)",
    "keycap_mismatched": "키캡 카트리지 — 재배출",
    "keycap_dispensed": "창고(우리) — 키캡 컨베이어 보고",
    "keycap_reached": "창고(우리) — 키캡 로봇팔 보고 4건",
    "keycap_packed": "창고(우리) — 트레이 컨베이어 section1 보고",
    "tray_reached": "보드 로봇암 — 보드 투입 보고",
    "board_mismatched": "보드 로봇암 — 재투입",
    "board_packed": "트레이 컨베이어 — section2 보고",
    "pickup_reached": "AGV — 트레이 적재 (cnt_agv loaded)",
    "loaded": "AGV — 조립대로 이동 (cnt_agv station_arrived)",
    "arrived": "조립대 — 사용자 QR 인증",
    "unclaimed": "AGV — 폐기",
    "verified": "AGV — 트레이 하차 (cnt_agv unloaded)",
    "received": "사용자 — 조립 완료",
    "completed": "없음 (주문 종료)",
}


def when(ct):
    # 이 Mobius 의 ct 는 한국 시간
    return datetime.strptime(ct, "%Y%m%dT%H%M%S").strftime("%m-%d %H:%M:%S")


def orders_of(con):
    return (con.get("orders") or {}) if isinstance(con, dict) else {}


def main():
    snapshots = mobius.list_cins(PROCESS)
    if not snapshots:
        print("cnt_process 에 기록이 없습니다")
        return

    if len(sys.argv) > 1:
        order_id = sys.argv[1]
    else:
        recent = []  # 최근에 등장한 주문 순서
        for _, con in reversed(snapshots):
            for oid in orders_of(con):
                if oid not in recent:
                    recent.append(oid)
        if not recent:
            print("최근 cnt_process 기록에 주문이 없습니다")
            return
        order_id = recent[0]
        print(f"최근 주문: {', '.join(recent[:5])}  → {order_id} 확인\n")

    rows = []
    last_status, last_fault = None, None
    for ct, con in snapshots:
        order = orders_of(con).get(order_id)
        if order is None:
            continue
        last_status, last_fault = order.get("status"), order.get("fault")
        fault = f"   ⚠ fault={last_fault}" if last_fault else ""
        rows.append((ct, 0, "플랫폼", f"status → {last_status}{fault}  (seq {con.get('seq')})"))

    for path, who in REPORTS.items():
        for ct, con in mobius.list_cins(path):
            if isinstance(con, dict) and con.get("order_id") == order_id:
                detail = ", ".join(f"{k}={v}" for k, v in con.items() if k != "order_id")
                rows.append((ct, 1, who, f"{path.split('/')[-1]}: {detail}"))

    if not rows:
        print(f"{order_id} 기록이 없습니다 (주문 번호 확인, 또는 오래돼서 cnt_process 에서 밀려남)")
        return

    print(f"===== {order_id} 흐름 (같은 초에 찍힌 줄은 앞뒤가 바뀌어 보일 수 있음) =====")
    for ct, _, who, what in sorted(rows, key=lambda r: (r[0], r[1])):
        print(f"{when(ct)}  [{who}] {what}")

    print("\n===== 요약 =====")
    still_in = order_id in orders_of(snapshots[-1][1])
    print(f"마지막 status: {last_status}" + (f"  ⚠ fault={last_fault}" if last_fault else ""))
    if not still_in:
        print("지금 cnt_process 에는 이 주문이 없음 → 끝났거나 플랫폼이 정리함")
    else:
        print(f"다음 차례: {NEXT_TURN.get(last_status, '(명세에 없는 status)')}")
        if last_fault:
            print("→ fault 가 있어서 멈춘 상태. 위 '다음 차례' 쪽 보고가 제때 안 왔는지 확인")
    ours = [what for _, _, who, what in rows if who == "창고(우리)"]
    print(f"창고(우리) 보고: {len(ours)}건 (정상 흐름이면 키캡 컨베이어 1 + 키캡 로봇팔 4 + section1 1 = 6건)")


if __name__ == "__main__":
    main()
