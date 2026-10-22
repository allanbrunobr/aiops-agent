"""Simulated metrics exporter — exposes Prometheus metrics for payment-svc.

Simulates the connection pool exhaustion scenario:
- Normal period: stable metrics
- After INCIDENT_DELAY_S seconds: progressive degradation (error spike, latency, pool exhausted)
- After RECOVERY_DELAY_S seconds: gradual recovery

Endpoint: GET /metrics (Prometheus text exposition format)
Control:  POST /trigger-incident  — starts degradation manually
           POST /reset            — returns to the normal state
"""

import math
import random
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9101
INCIDENT_DELAY_S = 120  # 2 min after startup to degrade automatically
RECOVERY_DELAY_S = 300  # 5 min after startup to recover

# Global state
start_time = time.time()
incident_triggered_at: float | None = None
reset_at: float | None = None


def _phase() -> str:
    """Determine the current phase: normal, degrading, incident, recovering."""
    now = time.time()
    if reset_at and now > reset_at:
        return "normal"
    if incident_triggered_at:
        elapsed = now - incident_triggered_at
        if elapsed < 30:
            return "degrading"
        if elapsed < 180:
            return "incident"
        return "recovering"
    # Auto-trigger based on time since startup
    elapsed = now - start_time
    if elapsed < INCIDENT_DELAY_S:
        return "normal"
    if elapsed < INCIDENT_DELAY_S + 30:
        return "degrading"
    if elapsed < RECOVERY_DELAY_S:
        return "incident"
    return "recovering"


def _metrics() -> str:
    """Generate metrics in Prometheus text exposition format."""
    phase = _phase()
    now = time.time()
    jitter = random.uniform(-0.05, 0.05)

    if phase == "normal":
        cpu = 25.0 + random.uniform(-3, 3)
        memory = 45.0 + random.uniform(-2, 2)
        error_rate = 0.5 + random.uniform(0, 0.5)
        latency_p50 = 12.0 + random.uniform(-2, 2)
        latency_p95 = 45.0 + random.uniform(-5, 5)
        latency_p99 = 120.0 + random.uniform(-10, 10)
        pool_active = 15 + random.randint(-3, 3)
        pool_max = 200
        pool_waiting = 0
        requests_total = 150 + random.randint(-10, 10)
        errors_total = 1 + random.randint(0, 2)
    elif phase == "degrading":
        t = (now - (incident_triggered_at or start_time + INCIDENT_DELAY_S)) / 30.0
        t = min(t, 1.0)
        cpu = 25.0 + t * 55 + random.uniform(-2, 2)
        memory = 45.0 + t * 30 + random.uniform(-2, 2)
        error_rate = 0.5 + t * 35 + random.uniform(0, 2)
        latency_p50 = 12.0 + t * 200 + random.uniform(-5, 5)
        latency_p95 = 45.0 + t * 800 + random.uniform(-10, 10)
        latency_p99 = 120.0 + t * 2000 + random.uniform(-20, 20)
        pool_active = int(15 + t * 35)
        pool_max = 50  # Bad config: 200 -> 50
        pool_waiting = int(t * 80)
        requests_total = 150 + random.randint(-10, 10)
        errors_total = int(1 + t * 45)
    elif phase == "incident":
        cpu = 82.0 + random.uniform(-3, 5)
        memory = 78.0 + random.uniform(-2, 3)
        error_rate = 38.0 + random.uniform(-3, 5)
        latency_p50 = 250.0 + random.uniform(-20, 30)
        latency_p95 = 950.0 + random.uniform(-50, 80)
        latency_p99 = 2500.0 + random.uniform(-100, 200)
        pool_active = 50
        pool_max = 50  # Exhausted!
        pool_waiting = 80 + random.randint(-5, 15)
        requests_total = 150 + random.randint(-10, 10)
        errors_total = 48 + random.randint(-3, 5)
    else:  # recovering
        t = min((now - (incident_triggered_at or start_time + INCIDENT_DELAY_S) - 180) / 60.0, 1.0)
        cpu = 82.0 - t * 55 + random.uniform(-3, 3)
        memory = 78.0 - t * 30 + random.uniform(-2, 2)
        error_rate = 38.0 - t * 36 + random.uniform(-1, 1)
        latency_p50 = 250.0 - t * 235 + random.uniform(-5, 5)
        latency_p95 = 950.0 - t * 900 + random.uniform(-10, 10)
        latency_p99 = 2500.0 - t * 2350 + random.uniform(-20, 20)
        pool_active = int(50 - t * 35)
        pool_max = 200  # Fixed: 50 -> 200
        pool_waiting = int(80 - t * 80)
        requests_total = 150 + random.randint(-10, 10)
        errors_total = int(48 - t * 46)

    phase_map = {"normal": 0, "degrading": 1, "incident": 2, "recovering": 3}
    phase_value = phase_map[phase]

    return f"""# HELP payment_svc_cpu_percent CPU usage percentage
# TYPE payment_svc_cpu_percent gauge
payment_svc_cpu_percent{{service="payment-svc"}} {cpu:.1f}

# HELP payment_svc_memory_percent Memory usage percentage
# TYPE payment_svc_memory_percent gauge
payment_svc_memory_percent{{service="payment-svc"}} {memory:.1f}

# HELP payment_svc_error_rate_percent Request error rate percentage
# TYPE payment_svc_error_rate_percent gauge
payment_svc_error_rate_percent{{service="payment-svc"}} {error_rate:.1f}

# HELP payment_svc_latency_ms Request latency in milliseconds
# TYPE payment_svc_latency_ms summary
payment_svc_latency_ms{{service="payment-svc",quantile="0.5"}} {latency_p50:.1f}
payment_svc_latency_ms{{service="payment-svc",quantile="0.95"}} {latency_p95:.1f}
payment_svc_latency_ms{{service="payment-svc",quantile="0.99"}} {latency_p99:.1f}

# HELP payment_svc_connection_pool_active Active connections in pool
# TYPE payment_svc_connection_pool_active gauge
payment_svc_connection_pool_active{{service="payment-svc"}} {pool_active}

# HELP payment_svc_connection_pool_max Max connections in pool
# TYPE payment_svc_connection_pool_max gauge
payment_svc_connection_pool_max{{service="payment-svc"}} {pool_max}

# HELP payment_svc_connection_pool_waiting Requests waiting for connection
# TYPE payment_svc_connection_pool_waiting gauge
payment_svc_connection_pool_waiting{{service="payment-svc"}} {pool_waiting}

# HELP payment_svc_requests_total Total requests per scrape interval
# TYPE payment_svc_requests_total gauge
payment_svc_requests_total{{service="payment-svc"}} {requests_total}

# HELP payment_svc_errors_total Total errors per scrape interval
# TYPE payment_svc_errors_total gauge
payment_svc_errors_total{{service="payment-svc"}} {errors_total}

# HELP payment_svc_phase Current incident phase (0=normal, 1=degrading, 2=incident, 3=recovering)
# TYPE payment_svc_phase gauge
payment_svc_phase{{service="payment-svc"}} {phase_value}
"""


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = _metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global incident_triggered_at, reset_at
        if self.path == "/trigger-incident":
            incident_triggered_at = time.time()
            reset_at = None
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Incident triggered\n")
        elif self.path == "/reset":
            reset_at = time.time()
            incident_triggered_at = None
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Reset to normal\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        phase = _phase()
        print(f"[{phase}] {self.address_string()} - {format % args}")


if __name__ == "__main__":
    print(f"Metrics exporter starting on :{PORT}")
    print(f"  GET  /metrics          — Prometheus metrics")
    print(f"  POST /trigger-incident — Trigger degradation manually")
    print(f"  POST /reset            — Reset to normal")
    print(f"  Auto-incident in {INCIDENT_DELAY_S}s, recovery at {RECOVERY_DELAY_S}s")
    server = HTTPServer(("0.0.0.0", PORT), MetricsHandler)
    server.serve_forever()
