#!/bin/sh
set -e

export DJANGO_SETTINGS_MODULE=amica.settings

host=${POSTGRES_HOST:-postgres}
user=${POSTGRES_USER:-devuser}
password=${POSTGRES_PASSWORD:-devpass}
dbname=${POSTGRES_DB:-devdb}

echo "DB=$dbname USER=$user HOST=$host"

# Ждём Postgres
echo "Waiting for Postgres to be ready..."
until PGPASSWORD="$password" psql -h "$host" -U "$user" -d "$dbname" -c '\q' >/dev/null 2>&1; do
  echo "Postgres is unavailable - sleeping"
  sleep 2
done

# Создание БД, если её нет
DB_EXISTS=$(PGPASSWORD="$password" psql -h "$host" -U "$user" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$dbname'")
if [ "$DB_EXISTS" != "1" ]; then
  echo "Database $dbname does not exist. Creating..."
  PGPASSWORD="$password" psql -h "$host" -U "$user" -d postgres -c "CREATE DATABASE \"$dbname\";"
fi

echo "Database $dbname is ready"

# Миграции
echo "Running Django migrations..."
python manage.py makemigrations --noinput
python manage.py migrate --noinput

# Сборка статики только для Django
if [ "$1" = "python" ] && [ "$2" = "manage.py" ]; then
  echo "Collecting static files..."
  python manage.py collectstatic --no-input
fi

# Запуск команды из docker-compose
exec "$@"