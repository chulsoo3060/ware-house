#!/usr/bin/env bash
# gcloud VM(warehouse-webhook)에서 pc_gateway.py 를 "통신 전용"(HARDWARE=fake)으로 켜는 스크립트.
# 폴링 방식이라 cnt_process 구독이 필요 없음 → 구독 알림 100번 제한(exc)·외부 IP 와 무관.
#
# 노트북 PowerShell 에서 (smart_factory 폴더):
#   gcloud.cmd compute scp mobius.py warehouse_server.py pc_gateway.py .env gcloud_deploy.sh warehouse-webhook:/home/ko050/ --zone us-central1-a
#   (Windows pscp 는 ~/ 를 못 알아들어서 전체 경로로 적음. ko050 = gcloud ssh 로그인 사용자)
#   gcloud.cmd compute ssh warehouse-webhook --zone us-central1-a --command "bash ~/gcloud_deploy.sh"
#
# ※ 노트북 pc_gateway.py 와 동시에 켜면 보고가 두 번씩 올라감 → 노트북 쪽은 반드시 끄기.

D=/home/kojunghyun3154/smart_factory
U=kojunghyun3154
B=$D/backup_$(date +%Y%m%d_%H%M%S)

cd ~ || exit 1
FILES="mobius.py warehouse_server.py pc_gateway.py mqtt_relay.py"
for f in $FILES .env; do
  [ -f "$f" ] || { echo "중단: 업로드 안 된 파일 $f"; exit 1; }
done
if ! grep -q '^HARDWARE=fake' .env; then
  echo "중단: VM 에는 Dobot 이 없어서 .env 가 HARDWARE=fake 여야 합니다"; exit 1
fi

echo "[1] 기존 게이트웨이·서버·중계 끄기"
sudo pkill -f '[p]c_gateway.py' && echo "  기존 pc_gateway 종료" || echo "  돌던 pc_gateway 없음"
sudo pkill -f '[w]arehouse_server.py' && echo "  기존 warehouse_server 종료" || echo "  돌던 warehouse_server 없음"
sudo pkill -f '[m]qtt_relay.py' && echo "  기존 mqtt_relay 종료" || echo "  돌던 mqtt_relay 없음"
sleep 1

echo "[2] 기존 파일 백업 → $B"
sudo mkdir -p "$B"
for f in $FILES .env; do
  [ -f "$D/$f" ] && sudo cp -p "$D/$f" "$B/"
done

echo "[3] 새 파일 설치"
for f in $FILES; do
  sudo install -o $U -g $U -m 644 "$f" "$D/$f"
done
sudo install -o $U -g $U -m 600 .env "$D/.env"
rm -f $FILES .env NUL
sudo -u $U python3 -c "import paho.mqtt" 2>/dev/null || sudo -u $U pip3 install -q paho-mqtt

echo "[4] 게이트웨이 켜기 (로그: $D/gateway.log)"
sudo -u $U setsid bash -c "cd $D && exec python3 -u pc_gateway.py > gateway.log 2>&1 < /dev/null" > /dev/null 2>&1 < /dev/null &

echo "[5] MQTT 중계 켜기 (로그: $D/relay.log) — 현장 PC 가 MQTT 로 받을 때 씀. 보고는 안 하므로 같이 켜 둬도 안전"
sudo -u $U setsid bash -c "cd $D && exec python3 -u mqtt_relay.py > relay.log 2>&1 < /dev/null" > /dev/null 2>&1 < /dev/null &
sleep 8
echo "----- gateway.log -----"
sudo tail -5 "$D/gateway.log"
echo "----- relay.log -----"
sudo tail -4 "$D/relay.log"
echo "-----------------------"
ps -eo pid,user,lstart,cmd | grep '[p]c_gateway.py' || echo "!! 게이트웨이가 안 떠 있음 — 위 로그 확인"
ps -eo pid,user,lstart,cmd | grep '[m]qtt_relay.py' || echo "!! MQTT 중계가 안 떠 있음 — 위 로그 확인"
