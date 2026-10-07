# -*- coding: utf-8 -*-
"""
MQTT 중계 — gcloud VM 에서 실행 (브로커 mosquitto 와 같은 VM)

하는 일: cnt_process 를 1초마다 읽어서(HTTP), 새 사진이면 MQTT 토픽으로 뿌리기.
  Mobius ──HTTP 읽기──> 이 프로그램 ──MQTT 발행──> 현장 PC(mqtt_gateway.py)

왜 필요한가
  학교 Mobius 는 MQTT 포트(1883·8883)를 밖에서 막아 둬서, PC 가 Mobius 에 MQTT 로 직접 붙을 수 없음.
  그래서 "읽어서 다시 뿌려 주는" 중계가 하나 필요함. 보드 로봇암 팀도 같은 구조(app.py)를 씀.

보고(done/failed)는 이 프로그램과 무관. 현장 PC 가 지금처럼 Mobius 에 HTTP 로 직접 올림.

.env
  MQTT_HOST/PORT/USER/PASS/MQTT_TOPIC, PROCESS_PATH

실행:  python3 -u mqtt_relay.py        (Ctrl+C 로 종료)
※ 이 프로그램은 판단·보고를 하지 않음. 그냥 전달만 함 → 게이트웨이와 같이 켜 둬도 보고가 겹치지 않음.
"""

import json
import os
import time

import paho.mqtt.client as mqtt

import mobius

PROCESS_PATH = (os.environ.get("PROCESS_PATH", "").strip("/")
                or f"{os.environ.get('TEST_ROOT', 'cnt_test_warehouse_arm')}/cnt_process")
TOPIC = os.environ.get("MQTT_TOPIC", "sjuiot/warehouse/conveyor")
POLL_SEC = 1.0


def build_client(client_id, clean_session=True):
    """.env 의 접속 정보로 MQTT 클라이언트 만들기 (끊기면 자동 재접속).
    clean_session=False 면 브로커가 끊긴 동안 온 메시지를 보관했다가 다시 붙을 때 전달 (받는 쪽에서 사용).
    ※ 같은 client_id 로 두 개를 동시에 켜면 서로 밀어내므로 하나만 켤 것."""
    try:  # paho 2.x 는 콜백 버전을 지정해야 함. 1.x 는 인자가 없음
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id,
                             clean_session=clean_session)
    except AttributeError:
        client = mqtt.Client(client_id=client_id, clean_session=clean_session)
    client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    return client


def main():
    print(f"MQTT 중계 시작 — 읽는 곳: {PROCESS_PATH} → 토픽: {TOPIC} ({os.environ['MQTT_HOST']}:{os.environ['MQTT_PORT']})")
    client = build_client("vm_relay")
    client.on_connect = lambda c, u, f, rc: print(f"[브로커] 접속 rc={rc}" + (" (성공)" if rc == 0 else " !! 실패"))
    client.on_disconnect = lambda c, u, rc: print(f"[브로커] 끊김 rc={rc} — 자동 재접속 중")
    client.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), keepalive=30)
    client.loop_start()

    last_seq = None
    try:
        while True:
            try:
                snapshot = mobius.get_latest(PROCESS_PATH)
            except Exception as e:
                print(f"[읽기 실패] {e}")
                time.sleep(POLL_SEC * 3)
                continue
            if isinstance(snapshot, dict) and snapshot.get("seq") != last_seq:
                last_seq = snapshot.get("seq")
                info = client.publish(TOPIC, json.dumps(snapshot, ensure_ascii=False), qos=1)
                info.wait_for_publish(timeout=5)
                orders = snapshot.get("orders") or {}
                status = ", ".join(f"{oid}={o.get('status')}" for oid, o in orders.items()) or "주문 없음"
                print(f"[발행] seq={last_seq} {status}" + ("" if info.is_published() else "  !! 발행 실패"))
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("\n종료합니다")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
