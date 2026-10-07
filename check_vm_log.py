# -*- coding: utf-8 -*-
"""
gcloud VM 게이트웨이 상태·로그 확인 (읽기 전용: VM 에서 아무것도 바꾸지 않음)

실행:  py -3.9 check_vm_log.py        → 마지막 40줄
       py -3.9 check_vm_log.py 100    → 마지막 100줄
       py -3.9 check_vm_log.py -f     → 실시간으로 계속 보기 (Ctrl+C 로 멈춤)

- 이 PC 에 gcloud CLI 가 로그인돼 있어야 함 (gcloud.cmd compute ssh 사용)
- VM 한글 로그가 Windows 터미널에서 깨지지 않도록 base64 로 받아서 풀어 보여줌
"""

import base64
import re
import subprocess
import sys

VM = "warehouse-webhook"
ZONE = "us-central1-a"
LOG = "/home/kojunghyun3154/smart_factory/gateway.log"
PATTERN = "[p]ython3 -u pc_gateway.py"  # [p] 는 이 확인 명령 자신이 검색에 걸리지 않게 하는 요령


def follow():
    """VM 로그를 실시간으로 흘려보기. 게이트웨이를 터미널에서 직접 켠 것처럼 보임."""
    print("VM 게이트웨이 로그 실시간 보기 — Ctrl+C 로 멈춤 (게이트웨이는 계속 돕니다)\n")
    remote = f"sudo tail -n 20 -f {LOG}"
    p = subprocess.Popen(
        ["gcloud.cmd", "compute", "ssh", VM, "--zone", ZONE, "--quiet", "--command", remote],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for raw in p.stdout:
            print(raw.decode("utf-8", errors="replace").rstrip())
    except KeyboardInterrupt:
        print("\n멈춥니다 (VM 게이트웨이는 계속 실행 중)")
    finally:
        p.terminate()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-f", "--follow", "f"):
        follow()
        return
    lines = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    # 원격 명령에는 한글을 넣지 않음 (Windows cmd 를 거치면서 깨짐)
    remote = (
        f"P=$(pgrep -f '{PATTERN}' | head -1); "
        f"if [ -n \"$P\" ]; then echo STATUS=UP; echo UPTIME=$(ps -o etime= -p $P); else echo STATUS=DOWN; fi; "
        f"echo ERRORS=$(sudo grep -cE 'Traceback|Error|HTTPSConnectionPool' {LOG}); "
        f"echo B64START; sudo tail -{lines} {LOG} | base64 -w0; echo; echo B64END"
    )
    result = subprocess.run(
        ["gcloud.cmd", "compute", "ssh", VM, "--zone", ZONE, "--quiet", "--command", remote],
        capture_output=True,
    )
    out = result.stdout.decode("utf-8", errors="replace")
    if "STATUS=" not in out:
        print("VM 접속 실패:")
        print(result.stderr.decode("utf-8", errors="replace")[-1500:])
        return

    status = re.search(r"STATUS=(\w+)", out).group(1)
    uptime = re.search(r"UPTIME=\s*(\S+)", out)
    errors = re.search(r"ERRORS=(\d+)", out)
    if status == "UP":
        print(f"게이트웨이: 실행 중 (켠 지 {uptime.group(1) if uptime else '?'})")
    else:
        print("게이트웨이: !! 꺼져 있음 — README 'gcloud VM 에서 통신 전용 게이트웨이' 로 다시 켜기")
    print(f"로그 속 오류 줄: {errors.group(1) if errors else '?'}건"
          " (한두 건은 잠깐 인터넷이 끊긴 것일 수 있음, 계속 늘면 확인 필요)")

    b64 = re.search(r"B64START\s*([A-Za-z0-9+/=]*)\s*B64END", out)
    print(f"\n===== gateway.log 마지막 {lines}줄 =====")
    print(base64.b64decode(b64.group(1)).decode("utf-8", errors="replace") if b64 else "(로그를 못 읽음)")


if __name__ == "__main__":
    main()
