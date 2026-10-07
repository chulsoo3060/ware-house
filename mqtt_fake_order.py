# -*- coding: utf-8 -*-
"""
우리끼리 통신 확인용 — 가짜 주문을 MQTT 토픽에 직접 흘려보냄 (Mobius 는 건드리지 않음)

플랫폼팀 없이 "중계 → 브로커 → 게이트웨이 → 판단 → 보고" 흐름이 이어지는지 볼 때 씁니다.
받는 쪽(mqtt_gateway.py)은 반드시 .env 의 DRY_RUN=1 로 켜세요. 아니면 가짜 주문 보고가 진짜 컨테이너로 올라갑니다.

실행:  py -3.9 mqtt_fake_order.py            정상 흐름
       py -3.9 mqtt_fake_order.py fault      중간에 fault 걸었다 푸는 흐름
"""

import json
import os
import sys
import time

import mqtt_relay

TOPIC = os.environ.get("MQTT_TOPIC", "sjuiot/warehouse/conveyor")
ORDER = "ord_selftest"


def snapshot(seq, status, fault=None):
    return {"seq": seq, "orders": {ORDER: {"station_id": "1", "status": status, "fault": fault,
                                           "keycap": "ENFP", "colors": ["r", "y", "b", "g"], "board": "red"}}}


def main():
    with_fault = len(sys.argv) > 1 and sys.argv[1] == "fault"
    steps = [("assigned", None), ("keycap_dispensed", None), ("keycap_reached", None)]
    if with_fault:
        steps += [("keycap_reached", "keycap_pack_failed"), ("keycap_reached", None)]
    steps += [("keycap_packed", None), ("tray_reached", None), (None, None)]  # 마지막은 주문 치우기

    client = mqtt_relay.build_client("fake_order")
    client.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), keepalive=30)
    client.loop_start()
    print(f"가짜 주문 {ORDER} 를 {TOPIC} 로 보냅니다 (Mobius 에는 아무것도 안 올라감)")

    base = int(time.time()) % 100000
    for i, (status, fault) in enumerate(steps):
        snap = snapshot(base + i, status, fault) if status else {"seq": base + i, "orders": {}}
        client.publish(TOPIC, json.dumps(snap, ensure_ascii=False), qos=1).wait_for_publish(timeout=5)
        print(f"  [{i + 1}/{len(steps)}] seq={base + i} status={status} fault={fault}")
        time.sleep(12 if status in ("keycap_dispensed", "keycap_reached") else 4)

    client.loop_stop()
    client.disconnect()
    print("끝. 받는 쪽 화면에서 보고가 순서대로 찍혔는지 확인하세요")


if __name__ == "__main__":
    main()
