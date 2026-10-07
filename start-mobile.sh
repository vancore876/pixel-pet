#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/mobile"
if ! command -v node >/dev/null 2>&1; then
    echo 'Install Node.js 24 LTS from https://nodejs.org/ then try again.'
    exit 1
fi
if [ ! -f node_modules/expo/package.json ]; then
    npm ci
fi
exec npm start
