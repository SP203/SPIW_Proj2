import subprocess
import requests
import time
import yaml
import json

# === Konfiguracja ===
PROMETHEUS_ADDR = "http://192.168.10.103:9090"
QUERY = 'amf_session{service="open5gs-amf-metrics",namespace="default"}'
NAMESPACE = "default"
CONTAINER_NAME = "open5gs-upf"
INTENT_FILE = "intent.yaml"
SLEEP_SECONDS = 10


def get_upf_pod_name():
    try:
        result = subprocess.check_output([
            "kubectl", "get", "pods", "-n", NAMESPACE,
            "-l", f"app.kubernetes.io/name={CONTAINER_NAME}",
            "-o", "jsonpath={.items[0].metadata.name}"
        ])
        return result.decode().strip()
    except subprocess.CalledProcessError:
        print("[!] Nie udało się znaleźć poda UPF")
        return None


def get_session_count():
    try:
        r = requests.get(f"{PROMETHEUS_ADDR}/api/v1/query", params={"query": QUERY})
        result = r.json()["data"]["result"]
        if not result:
            return 0
        return int(float(result[0]["value"][1]))
    except Exception as e:
        print(f"[!] Błąd pobierania sesji z Prometheusa: {e}")
        return 0


def load_intent(file_path):
    with open(file_path, "r") as f:
        return yaml.safe_load(f)["rules"]


def determine_cpu_limit(session_count, rules):
    for rule in sorted(rules, key=lambda r: r["threshold"]):
        if session_count <= rule["threshold"]:
            return rule["cpu"]
    return rules[-1]["cpu"]


def get_current_cpu_limit(pod_name):
    try:
        out = subprocess.check_output([
            "kubectl", "get", "pod", pod_name, "-n", NAMESPACE,
            "-o", f"jsonpath={{.spec.containers[?(@.name==\"{CONTAINER_NAME}\")].resources.limits.cpu}}"
        ])
        return out.decode("utf-8").strip()
    except subprocess.CalledProcessError:
        return None


def patch_cpu_limit(pod_name, session_count, new_cpu):
    current_cpu = get_current_cpu_limit(pod_name)
    print(f"[-] Sesje: {session_count}, CPU docelowe: {new_cpu}")
    if current_cpu == new_cpu:
        print("[*] Już ustawione (brak zmiany)")
        return
    patch = {
        "spec": {
            "containers": [{
                "name": CONTAINER_NAME,
                "resources": {
                    "limits": {"cpu": new_cpu}
                }
            }]
        }
    }
    try:
        subprocess.run([
            "kubectl", "patch", "-n", NAMESPACE, "pod", pod_name,
            "--subresource", "resize",
            "--patch", json.dumps(patch)
        ], check=True)
        print(f"[+] Zmieniono limit CPU → {new_cpu}")
    except subprocess.CalledProcessError as e:
        print(f"[!] Błąd patchowania: {e}")


if __name__ == "__main__":
    print("[i] Kontroler UPF uruchomiony.")
    rules = load_intent(INTENT_FILE)
    iteracja = 1
    pod_name = get_upf_pod_name()
    if not pod_name:
        exit(1)

    while True:
        print(f"\n[#] Iteracja {iteracja}")
        session_count = get_session_count()
        target_cpu = determine_cpu_limit(session_count, rules)
        patch_cpu_limit(pod_name, session_count, target_cpu)
        iteracja += 1
        time.sleep(SLEEP_SECONDS)
