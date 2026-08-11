#!/usr/bin/env bash
# Генерирует Kubernetes Secret для паролей из .env файла и применяет.
# Конфигурация (нечувствительные vars) уже в deployment.yaml (ConfigMap app-config).
#
# Использование: ./generate-secret.sh [.env] [namespace]

set -euo pipefail

ENV_FILE="${1:-.env}"
NAMESPACE="${2:-webstorage}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ .env файл не найден: $ENV_FILE"
    echo "Создайте его из env.example и заполните реальными значениями."
    echo "  cp env.example .env"
    exit 1
fi

echo "🔐 Генерация Secret app-secrets из $ENV_FILE..."
echo "📋 namespace: $NAMESPACE"

# Секреты: только пароли и чувствительные данные
SECRET_VARS=(
    POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL
    JWT_SECRET ADMIN_EMAIL ADMIN_PASSWORD
    GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
)

# Собираем только нужные переменные из .env
FILTERED_ENV=$(mktemp)
trap "rm -f $FILTERED_ENV" EXIT

while IFS= read -r line; do
    # Пропускаем комментарии и пустые строки
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    # Извлекаем ключ
    key="${line%%=*}"
    # Оставляем только переменные из списка секретов
    for var in "${SECRET_VARS[@]}"; do
        if [[ "$key" == "$var" ]]; then
            echo "$line" >> "$FILTERED_ENV"
            break
        fi
    done
done < "$ENV_FILE"

if [[ ! -s "$FILTERED_ENV" ]]; then
    echo "❌ Не найдено секретных переменных в $ENV_FILE"
    exit 1
fi

kubectl create secret generic app-secrets \
    --from-env-file="$FILTERED_ENV" \
    --dry-run=client -o yaml | \
    kubectl apply -n "$NAMESPACE" -f -

echo "✅ Secret 'app-secrets' применён в namespace '$NAMESPACE'"
