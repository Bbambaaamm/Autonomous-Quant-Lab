from pathlib import Path
p=Path('.github/workflows/ci.yml')
s=p.read_text()
old='''      - name: Production npm audit with bounded network retry
        working-directory: frontend
        env:
          npm_config_fetch_timeout: "60000"
          npm_config_fetch_retries: "1"
        run: |
          set -euo pipefail
          audit_with_retry() {
            local attempt output status
            for attempt in 1 2 3; do
              set +e
              output="$("$@" 2>&1)"
              status=$?
              set -e
              printf '%s\\n' "$output"
              if test "$status" -eq 0; then
                return 0
              fi
              if ! grep -Eiq 'network timeout|audit endpoint returned an error|EAI_AGAIN|ECONNRESET|ETIMEDOUT|502 Bad Gateway|503 Service Unavailable|504 Gateway Timeout' <<<"$output"; then
                return "$status"
              fi
              if test "$attempt" -eq 3; then
                return "$status"
              fi
              sleep $((attempt * 10))
            done
          }
          audit_with_retry npm audit --omit=dev --audit-level=high
      - name: Critical dependency scan (Trivy)
        run: >-
          docker run --rm -v "$PWD:/work:ro" aquasec/trivy:0.66.0
          filesystem --exit-code 1 --severity CRITICAL --scanners vuln
          --skip-version-check /work/frontend
'''
new='''      - name: Frontend dependency vulnerability scan (Trivy)
        run: >-
          docker run --rm -v "$PWD:/work:ro" aquasec/trivy:0.66.0
          filesystem --exit-code 1 --severity HIGH,CRITICAL --scanners vuln
          --skip-version-check /work/frontend
'''
if s.count(old)!=1: raise SystemExit(f'expected one security audit block, got {s.count(old)}')
p.write_text(s.replace(old,new,1))
for path in ['.github/scripts/pr108-trivy-finalize.py','.github/workflows/pr108-trivy-finalize.yml']:
 q=Path(path)
 if q.exists(): q.unlink()
