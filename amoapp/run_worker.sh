# в корне репо
mkdir -p amoapp
cat > amoapp/run_worker.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd /var/www/app
# env подтянется через EnvironmentFile в systemd-юнитах
source .venv/bin/activate

python - <<'PY'
from amoapp.worker import init_env_api, process_once

env, api = init_env_api()
print(process_once(env, api))
PY
EOF

chmod +x amoapp/run_worker.sh
git add amoapp/run_worker.sh
git commit -m "add worker script"
git push
